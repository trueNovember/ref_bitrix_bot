# bot.py

import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.client.default import DefaultBotProperties
# Импортируем все наши модули
import config
import database as db
import bitrix_api
from states import PartnerRegistration, ClientSubmission
import keyboards as kb  # Импортируем клавиатуры с префиксом kb
from html import escape
from aiogram.filters import Filter

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = web.Application()

# --- Тексты бота ---
WELCOME_TEXT = """
Здравствуйте! 🤝

Это бот партнерской системы компании [Название Вашей Компании].
Мы предлагаем дизайнерам и риэлторам выгодное сотрудничество.

<b>Условия:</b>
1. Вы регистрируетесь в системе.
2. Менеджер связывается с вами для верификации (ваша заявка попадет в воронку).
3. После верификации вы получаете доступ к отправке заявок.

Нажимая "Я согласен", вы принимаете условия обработки персональных данных.
"""
PENDING_VERIFICATION_TEXT = "⏳ Ваша заявка на верификацию принята. Она попала в нашу воронку. Менеджер свяжется с вами в ближайшее рабочее время."
VERIFIED_TEXT = "✅ Вы верифицированный партнер. Теперь вы можете отправлять нам клиентов!"
REJECTED_TEXT = "❌ К сожалению, ваша заявка на партнерство была отклонена."
GENERIC_ERROR_TEXT = "Произошла ошибка. Попробуйте позже."

STATUS_REJECTED_REVOKED_TEXT = """
<b>Уведомление об изменении статуса 🔔</b>

Здравствуйте. Уведомляем вас, что статус вашей партнерской заявки был пересмотрен.

<b>Новый статус:</b> <i>Отклонено</i>.

Доступ к отправке клиентов закрыт. Для уточнения причин, пожалуйста, свяжитесь с вашим менеджером.
"""

STATUS_PENDING_REVOKED_TEXT = """
<b>Внимание: Ошибка статуса</b>

Здравствуйте. Похоже, произошла системная ошибка, или ваш статус верификации был возвращен на этап "На рассмотрении".

<b>Новый статус:</b> <i>В ожидании</i>.

Доступ к отправке клиентов временно приостановлен. Пожалуйста, свяжитесь с вашим менеджером для прояснения ситуации.
"""
# =================================================================
# === ОБРАБОТЧИКИ TELEGRAM (Логика FSM) ===========================
# =================================================================


class IsAdminFilter(Filter):
    """Фильтр проверяет, есть ли у пользователя роль (junior or senior)"""

    async def __call__(self, message: Message) -> bool:
        return await db.get_admin_role(message.from_user.id) is not None


class IsSeniorAdminFilter(Filter):
    """Фильтр проверяет, является ли пользователь Senior админом"""

    async def __call__(self, message: Message) -> bool:
        role = await db.get_admin_role(message.from_user.id)
        return role == 'senior'


@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start."""
    await state.clear()
    status = await db.get_partner_status(message.from_user.id)

    if status == 'verified':
        await message.answer(VERIFIED_TEXT, reply_markup=kb.get_verified_partner_menu())
    elif status == 'pending':
        await message.answer(PENDING_VERIFICATION_TEXT, reply_markup=ReplyKeyboardRemove())
    elif status == 'rejected':
        await message.answer(REJECTED_TEXT, reply_markup=ReplyKeyboardRemove())
    else:
        await message.answer(WELCOME_TEXT, reply_markup=kb.get_agree_keyboard())


# --- 1. Процесс регистрации партнера ---

@dp.callback_query(F.data == "agree_to_terms")
async def process_agree(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Отлично! Давайте начнем регистрацию.")
    await callback.message.answer("Пожалуйста, введите ваше ФИО:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_full_name)
    await callback.answer()


# ---  Отмена FSM ---
@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    # Вызываем /start, чтобы показать актуальное меню
    await cmd_start(message, state)


@dp.message(PartnerRegistration.waiting_for_full_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("Теперь, пожалуйста, поделитесь вашим номером телефона.",
                         reply_markup=kb.get_request_phone_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_phone)


@dp.message(PartnerRegistration.waiting_for_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    phone_number = message.contact.phone_number
    user_id = message.from_user.id
    data = await state.get_data()
    full_name = data.get('full_name')

    await state.clear()

    # 1. Сначала отправляем в Битрикс, чтобы получить deal_id
    deal_id = await bitrix_api.create_partner_deal(full_name, phone_number, user_id)

    if deal_id:
        # 2. Если успешно, сохраняем в локальную БД
        await db.add_partner(user_id, full_name, phone_number, deal_id)
        await message.answer(PENDING_VERIFICATION_TEXT, reply_markup=ReplyKeyboardRemove())

        # === НОВАЯ ЛОГИКА: УВЕДОМЛЕНИЕ АДМИНОВ ===
        admin_ids = await db.get_junior_admin_ids()

        notification_text = (
            f"🔔 <b>Новая заявка на партнерство!</b>\n\n"
            f"<b>ФИО:</b> {escape(full_name)}\n"
            f"<b>Телефон:</b> {escape(phone_number)}\n"
            f"<b>Telegram ID:</b> <code>{user_id}</code>"
        )
        # Получаем нашу новую клавиатуру
        keyboard = kb.get_verification_keyboard(user_id)

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, notification_text, reply_markup=keyboard)
            except Exception as e:
                logging.warning(f"Не удалось отправить уведомление админу {admin_id}: {e}")
        # =======================================

    else:
        # 3. Если ошибка
        await message.answer(GENERIC_ERROR_TEXT, reply_markup=ReplyKeyboardRemove())
    # =======================


@dp.message(PartnerRegistration.waiting_for_phone)
async def process_phone_invalid(message: Message):
    await message.answer("Пожалуйста, нажмите на кнопку 'Поделиться номером телефона'.")


# --- 2. Процесс отправки клиента (для верифицированных) ---

@dp.message(F.text == "🚀 Отправить клиента")
async def start_client_submission(message: Message, state: FSMContext):
    status = await db.get_partner_status(message.from_user.id)
    if status != 'verified':
        await message.answer("Эта функция доступна только верифицированным партнерам.")
        return

    await message.answer("Введите ФИО вашего клиента:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_name)


@dp.message(ClientSubmission.waiting_for_client_name)
async def client_name_received(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("Введите номер телефона клиента:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_phone)


@dp.message(ClientSubmission.waiting_for_client_phone)
async def client_phone_received(message: Message, state: FSMContext):
    await state.update_data(client_phone=message.text)
    await message.answer("Введите адрес квартиры:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_address)


@dp.message(ClientSubmission.waiting_for_client_address)
async def client_address_received(message: Message, state: FSMContext):
    partner_id = message.from_user.id
    data = await state.get_data()
    partner_data = await db.get_partner_data(partner_id)

    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    client_address = message.text
    partner_name = partner_data.get('full_name')

    await state.clear()

    # Отправляем в Битрикс (воронка клиентов)
    deal_id = await bitrix_api.create_client_deal(
        client_name, client_phone, client_address, partner_name
    )

    if deal_id:
        # !!! Сохраняем связку Партнер <-> Сделка
        await db.add_client(partner_id, deal_id, client_name)
        await message.answer(
            f"✅ Клиент '{client_name}' успешно отправлен!",
            reply_markup=kb.get_verified_partner_menu()
        )
    else:
        await message.answer(GENERIC_ERROR_TEXT, reply_markup=kb.get_verified_partner_menu())


# --- 3. Админская часть (Верификация) ---

@dp.message(Command("verify"), IsAdminFilter())
async def cmd_verify(message: Message):
    """
    Команда для верификации (Ручной режим).
    Доступна 'junior' и 'senior' админам.
    """
    try:
        user_id_to_verify = int(message.text.split()[1])
        # Вызываем нашу "ядерную" функцию
        await process_partner_verification(
            admin_id=message.from_user.id,
            partner_user_id=user_id_to_verify,
            new_status='verified'
        )
    except Exception as e:
        error_text = escape(str(e))
        usage_text = "Использование: /verify &lt;user_id&gt;"
        await message.answer(f"Ошибка: {error_text}. {usage_text}")


@dp.callback_query(F.data.startswith("verify_partner:"))
async def on_verify_partner(callback: CallbackQuery):
    """
    Ловит нажатие кнопки '✅ Одобрить'.
    """
    # Проверяем, что тот, кто нажал - админ
    if not await db.get_admin_role(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для этого действия.", show_alert=True)
        return

    # Извлекаем ID партнера из "verify_partner:123456"
    partner_user_id = int(callback.data.split(":")[1])

    await process_partner_verification(
        admin_id=callback.from_user.id,
        partner_user_id=partner_user_id,
        new_status='verified',
        callback=callback
    )


@dp.callback_query(F.data.startswith("reject_partner:"))
async def on_reject_partner(callback: CallbackQuery):
    """
    Ловит нажатие кнопки '❌ Отклонить'.
    """
    if not await db.get_admin_role(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для этого действия.", show_alert=True)
        return

    partner_user_id = int(callback.data.split(":")[1])

    await process_partner_verification(
        admin_id=callback.from_user.id,
        partner_user_id=partner_user_id,
        new_status='rejected',
        callback=callback
    )


# --- Команды управления (Только Senior) ---


@dp.message(Command("addadmin"), IsSeniorAdminFilter())
async def cmd_add_admin(message: Message):
    """Добавляет/обновляет админа. (Только Senior)"""
    try:
        # Использование: /addadmin <user_id> <role> [username]
        parts = message.text.split()
        if len(parts) < 3:
            await message.answer("<b>Использование:</b> /addadmin &lt;user_id&gt; &lt;role&gt; [username]")
            return

        user_id = int(parts[1])
        role = parts[2].lower()  # 'junior' or 'senior'

        # === ИСПРАВЛЕНИЕ ЗДЕСЬ ===
        if len(parts) > 3:
            # Объединяем всё, что идет после role, в одно имя
            username = " ".join(parts[3:])
        else:
            # Оставляем Admin_... по умолчанию, если имя не указано
            username = f"Admin_{user_id}"

        if role not in ('junior', 'senior'):
            await message.answer("❌ <b>Неверная роль.</b> Укажите 'junior' или 'senior'.")
            return

        await db.add_admin(user_id, username, role)
        await message.answer(f"✅ Админ {username} (ID: {user_id}) успешно добавлен с ролью: <b>{role}</b>.")

    except Exception as e:
        error_text = escape(str(e))
        await message.answer(
            f"<b>Ошибка:</b> {error_text}.\n<b>Использование:</b> /addadmin &lt;user_id&gt; &lt;role&gt; [username]")


@dp.message(Command("deladmin"), IsSeniorAdminFilter())
async def cmd_del_admin(message: Message):
    """Удаляет админа. (Только Senior)"""
    try:
        user_id = int(message.text.split()[1])

        if user_id == config.SUPER_ADMIN_ID:
            await message.answer("❌ Нельзя удалить Супер-Админа (владельца бота).")
            return

        await db.remove_admin(user_id)
        await message.answer(f"✅ Админ (ID: {user_id}) успешно удален.")
    except Exception as e:
        error_text = escape(str(e))
        await message.answer(f"<b>Ошибка:</b> {error_text}.\n<b>Использование:</b> /deladmin &lt;user_id&gt;")


@dp.message(Command("listadmins"), IsSeniorAdminFilter())
async def cmd_list_admins(message: Message):
    """Показывает список админов. (Только Senior)"""
    admins = await db.list_admins()
    if not admins:
        await message.answer("Список админов пуст.")
        return

    response = "<b>Список администраторов:</b>\n\n"
    for user_id, username, role in admins:
        response += f"• {username} (ID: <code>{user_id}</code>)\n"
        response += f"  <i>Роль: {role.capitalize()}</i>\n"

    await message.answer(response)


async def process_partner_verification(
        admin_id: int,
        partner_user_id: int,
        new_status: str,
        callback: CallbackQuery = None
):
    """
    "Ядро" верификации. Вызывается из cmd_verify и из callback-ов.
    new_status: 'verified' или 'rejected'
    """
    try:
        partner_data = await db.get_partner_data(partner_user_id)
        partner_name = partner_data.get('full_name', f'ID: {partner_user_id}')
        # 1. Проверяем, что партнер еще не обработан
        current_status = await db.get_partner_status(partner_user_id)
        if current_status != 'pending':
            if callback:
                await callback.answer(f"Этот партнер уже был обработан.", show_alert=True)
            else:
                await bot.send_message(admin_id, f"❌ Этот партнер уже был обработан.")
            return

        # 2. Находим ID сделки партнера
        deal_id = await db.get_partner_deal_id_by_user_id(partner_user_id)
        if not deal_id:
            raise Exception(f"Не найден deal_id для партнера {partner_user_id}")

        # 3. Обновляем статус в нашей БД
        await db.set_partner_status(partner_user_id, new_status)

        # 4. Двигаем сделку в Битриксе
        if new_status == 'verified':
            stage_id = config.BITRIX_PARTNER_VERIFIED_STAGE_ID
            notification_text = VERIFIED_TEXT
            reply_markup = kb.get_verified_partner_menu()
        else:  # 'rejected'
            stage_id = config.BITRIX_PARTNER_REJECTED_STAGE_ID
            notification_text = REJECTED_TEXT
            reply_markup = ReplyKeyboardRemove()

        success_b24 = False
        if stage_id:
            success_b24 = await bitrix_api.move_deal_stage(deal_id, stage_id)

        # 5. Уведомляем партнера и админа
        await bot.send_message(partner_user_id, notification_text, reply_markup=reply_markup)

        admin_answer = f"✅ Партнер {partner_user_id} успешно {new_status}."
        if stage_id and not success_b24:
            admin_answer += f"\n⚠️ Не удалось передвинуть сделку {deal_id} в Битрикс."

        # Если это был клик по кнопке, отвечаем иначе
        if callback:
            # Редактируем исходное сообщение, чтобы убрать кнопки
            admin_username = callback.from_user.username or "Админ"
            await callback.message.edit_text(
                callback.message.text + f"\n\n<b>Обработано:</b> @{admin_username}\n<b>Статус:</b> {new_status.capitalize()}"
            )
            await callback.answer(admin_answer)

            # === НОВАЯ ЛОГИКА: Рассылка остальным админам ===
            all_junior_ids = await db.get_junior_admin_ids()

            # Сообщение для остальных
            notification_text_others = (
                f"🔔 Заявка партнера <b>{escape(partner_name)}</b> была обработана.\n"
                f"<b>Статус:</b> {new_status.capitalize()}\n"
                f"<b>Менеджер:</b> @{admin_username}"
            )

            for admin_id in all_junior_ids:
                # Не отправляем сообщение тому, кто УЖЕ нажал
                if admin_id == callback.from_user.id:
                    continue

                try:
                    await bot.send_message(admin_id, notification_text_others)
                except Exception as e:
                    logging.warning(f"Не удалось отправить доп. уведомление админу {admin_id}: {e}")
        else:
            await bot.send_message(admin_id, admin_answer)

    except Exception as e:
        error_text = f"Ошибка при верификации {partner_user_id}: {e}"
        logging.error(error_text)
        if callback:
            await callback.answer(error_text, show_alert=True)
        else:
            await bot.send_message(admin_id, error_text)


# =================================================================
# === ОБРАБОТЧИКИ AIOHTTP (Сервер) ================================
# =================================================================

async def handle_telegram_webhook(request: web.Request):
    """
    Этот обработчик ловит запросы от TELEGRAM.
    Он передает их в aiogram Dispatcher.
    """
    url = str(request.url)
    logging.info(f"Получен Telegram-апдейт: {url}")

    # Создаем объект SimpleRequestHandler и вызываем его
    handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
    )
    response = await handler.handle(request)
    return response


async def handle_bitrix_webhook(request: web.Request):
    """
    !!! ВЕРСИЯ 3.0 (с пересмотром статуса) !!!
    Этот обработчик ловит GET-параметры от робота
    и корректно обрабатывает смену статуса (даже 'verified' -> 'rejected').
    """
    try:
        data = request.query
        logging.info(f"Получен Bitrix-РОБОТ-апдейт (GET): {data}")

        # 1. Проверка безопасности
        if data.get('secret') != config.BITRIX_INCOMING_SECRET:
            logging.warning("!!! Неверный токен от Робота Битрикс !!!")
            return web.Response(status=403, text="Forbidden")

        # 2. Разбираем параметры
        event_type = data.get('event_type')
        status = data.get('status')  # 'verified', 'rejected' или 'pending' (если вернули)
        deal_id = int(data.get('deal_id', 0))
        user_id_from_b24_str = str(data.get('user_id', ''))
        user_id = int(user_id_from_b24_str) if user_id_from_b24_str.isdigit() else None

        if not user_id:
            logging.error(f"Ошибка от Робота: не пришел user_id для сделки {deal_id}.")
            return web.Response(text="OK (no user_id)")

        # 3. Обрабатываем ивент верификации
        if event_type == 'partner_verification':
            logging.info(f"Получен статус '{status}' для партнера {user_id} (сделка {deal_id})")

            # Получаем ТЕКУЩИЙ статус из нашей БД
            current_status = await db.get_partner_status(user_id)

            if not current_status:
                logging.warning(f"Партнер {user_id} не найден в БД, хотя пришел апдейт.")
                return web.Response(text="OK (partner not found)")

            # --- НОВАЯ ЛОГИКА ---
            # Если статус в Битриксе отличается от статуса в БД, действуем.
            if current_status != status:
                logging.info(f"Статус партнера {user_id} меняется с '{current_status}' на '{status}'.")

                # 1. Обновляем статус в нашей БД
                await db.set_partner_status(user_id, status)

                notification_text = ""
                reply_markup = ReplyKeyboardRemove()

                # 2. Готовим правильное сообщение
                if status == 'verified':
                    notification_text = VERIFIED_TEXT
                    reply_markup = kb.get_verified_partner_menu()

                elif status == 'rejected':
                    if current_status == 'pending':
                        notification_text = REJECTED_TEXT
                    else:
                        # Статус был 'verified', а стал 'rejected'
                        notification_text = STATUS_REJECTED_REVOKED_TEXT

                elif status == 'pending':
                    # Статус был 'verified' или 'rejected', а стал 'pending'
                    notification_text = STATUS_PENDING_REVOKED_TEXT

                # 3. Уведомляем партнера
                if notification_text:
                    try:
                        await bot.send_message(user_id, notification_text, reply_markup=reply_markup)
                    except Exception as e:
                        logging.warning(f"Не удалось уведомить партнера {user_id}: {e}")

            else:
                logging.info(f"Партнер {user_id} уже имеет статус '{status}'. Игнорируем.")

        # --- (Здесь будет логика для CLIENT_DEAL_UPDATE) ---

        return web.Response(text="OK")

    except Exception as e:
        logging.error(f"Ошибка в обработке GET от Робота Битрикс: {e}")
        return web.Response(status=500, text="Server Error")


# =================================================================
# === ЗАПУСК СЕРВЕРА ==============================================
# =================================================================

async def on_startup(app_instance: web.Application):
    """Выполняется при старте сервера."""
    await db.init_db()  # Инициализируем базу данных
    await db.add_admin(config.SUPER_ADMIN_ID, "SUPER_ADMIN", "senior")
    # Устанавливаем вебхук для Telegram
    webhook_url = config.BASE_WEBHOOK_URL + config.TELEGRAM_WEBHOOK_PATH
    await bot.set_webhook(
        url=webhook_url,
        secret_token=config.BITRIX_INCOMING_SECRET  # Используем тот же секрет для Telegram
    )
    logging.info(f"Telegram вебхук установлен на: {webhook_url}")


async def on_shutdown(app_instance: web.Application):
    """Выполняется при остановке сервера."""
    logging.info("Остановка сервера...")
    await bot.delete_webhook()
    logging.info("Telegram вебхук удален.")


def main():
    """Главная функция запуска."""

    # Регистрируем обработчик для Telegram
    app.router.add_post(
        config.TELEGRAM_WEBHOOK_PATH,
        handle_telegram_webhook
    )

    # Регистрируем обработчик для Битрикс
    app.router.add_post(
        config.BITRIX_WEBHOOK_PATH,
        handle_bitrix_webhook
    )

    # Добавляем функции старта/остановки
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    # Передаем aiogram-диспетчер в приложение (для обработчика)
    app['bot'] = bot
    app['dispatcher'] = dp

    logging.info(f"Запуск веб-сервера на {config.WEB_SERVER_HOST}:{config.WEB_SERVER_PORT}")
    web.run_app(
        app,
        host=config.WEB_SERVER_HOST,
        port=config.WEB_SERVER_PORT,
    )


if __name__ == "__main__":
    main()
