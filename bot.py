# bot.py
import re
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.client.default import DefaultBotProperties
from html import escape
import math

# Импортируем наши модули
import config
import database as db
import bitrix_api
from states import PartnerRegistration, ClientSubmission
import keyboards as kb

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = web.Application()


# --- Вспомогательные функции ---
def get_client_stage_name(stage_id: str) -> str:
    """Превращает системный ID стадии в понятное название."""
    stages_map = {
        config.BITRIX_CLIENT_STAGE_1: "Клиенты в обработке",
        config.BITRIX_CLIENT_STAGE_2: "С клиентом назначена встреча",
        config.BITRIX_CLIENT_STAGE_3: "Расчет сметы",
        config.BITRIX_CLIENT_STAGE_WIN: "С клиентом заключен договор",
        config.BITRIX_CLIENT_STAGE_LOSE: "Отказ клиента"
    }
    return stages_map.get(stage_id, stage_id)


# =================================================================
# === ОБРАБОТЧИКИ TELEGRAM (Логика бота) ==========================
# =================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик команды /start."""
    await state.clear()
    status = await db.get_partner_status(message.from_user.id)

    if status == 'verified':
        await message.answer("✅ Вы верифицированный партнер.", reply_markup=kb.get_verified_partner_menu())
    elif status == 'pending':
        await message.answer("⏳ Ваша заявка на верификацию принята. Менеджер свяжется с вами.",
                             reply_markup=ReplyKeyboardRemove())
    elif status == 'rejected':
        await message.answer("❌ К сожалению, ваша заявка была отклонена.", reply_markup=ReplyKeyboardRemove())
    else:
        welcome_text = await db.get_setting("welcome_text",
                                            "Здравствуйте! Нажимая 'Я согласен', вы принимаете условия.")
        await message.answer(welcome_text, reply_markup=kb.get_agree_keyboard())


@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await cmd_start(message, state)


# --- 1. Процесс регистрации партнера ---

@dp.callback_query(F.data == "agree_to_terms")
async def process_agree(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Отлично! Давайте начнем регистрацию.")
    # НОВЫЙ ШАГ: Выбор роли
    await callback.message.answer("Пожалуйста, выберите, кем вы являетесь:", reply_markup=kb.get_role_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_role)
    await callback.answer()


@dp.message(PartnerRegistration.waiting_for_role)
async def process_role(message: Message, state: FSMContext):
    if message.text not in ["Риэлтор", "Дизайнер", "Приемщик", "Другое"]:
        await message.answer("Пожалуйста, выберите вариант из меню.")
        return
    await state.update_data(role=message.text)

    await message.answer("Пожалуйста, введите ваше ФИО:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_full_name)


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
    role = data.get('role')  # <-- Роль
    username = message.from_user.username

    await state.clear()

    # 1. Создаем сделку в Битрикс (передаем роль)
    deal_id = await bitrix_api.create_partner_deal(full_name, phone_number, user_id, username, role)

    if deal_id:
        # 2. Сохраняем в БД (с ролью)
        await db.add_partner(user_id, full_name, phone_number, deal_id, role)
        await message.answer("⏳ Ваша заявка принята. Менеджер свяжется с вами.", reply_markup=ReplyKeyboardRemove())

        # 3. Уведомляем Junior-админов
        admin_ids = await db.get_junior_admin_ids()
        notification_text = (
            f"🔔 <b>Новая заявка на партнерство!</b>\n\n"
            f"<b>ФИО:</b> {escape(full_name)}\n"
            f"<b>Роль:</b> {escape(role)}\n"
            f"<b>Телефон:</b> {escape(phone_number)}\n"
            f"<b>Telegram ID:</b> <code>{user_id}</code>"
        )
        keyboard = kb.get_verification_keyboard(user_id)

        for admin_id in admin_ids:
            try:
                await bot.send_message(admin_id, notification_text, reply_markup=keyboard)
            except Exception as e:
                logging.warning(f"Не удалось уведомить админа {admin_id}: {e}")
    else:
        await message.answer("Произошла ошибка. Попробуйте позже.", reply_markup=ReplyKeyboardRemove())


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

    await message.answer("Введите <b>Имя и Фамилию</b> клиента (Отчество не обязательно):",
                         reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_name)


@dp.message(ClientSubmission.waiting_for_client_name)
async def client_name_received(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("Введите номер телефона клиента:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_phone)


@dp.message(ClientSubmission.waiting_for_client_phone)
async def client_phone_received(message: Message, state: FSMContext):
    phone_text = message.text
    # Очистка номера
    cleaned_phone = re.sub(r'\D', '', phone_text)

    if cleaned_phone.startswith('8') and len(cleaned_phone) == 11:
        cleaned_phone = '7' + cleaned_phone[1:]
    elif len(cleaned_phone) == 10:
        cleaned_phone = '7' + cleaned_phone

    if not (len(cleaned_phone) == 11 and cleaned_phone.startswith('7')):
        await message.answer(
            "❌ <b>Неверный формат номера.</b>\nПожалуйста, введите корректный номер РФ."
        )
        return

    formatted_phone = '+' + cleaned_phone

    # === ПРОВЕРКА ДУБЛИКАТА В БИТРИКС ===
    contact_id = await bitrix_api.check_contact_exists_by_phone(formatted_phone)

    if contact_id:
        # Клиент УЖЕ ЕСТЬ. Прерываем процесс.
        partner_data = await db.get_partner_data(message.from_user.id)
        client_name = (await state.get_data()).get('client_name')

        # Создаем алерт для менеджера
        await bitrix_api.create_duplicate_alert_deal(client_name, formatted_phone, partner_data['full_name'])

        await message.answer(
            f"ℹ️ Клиент с номером {formatted_phone} уже есть в нашей базе.\n"
            "Мы свяжемся с ним для уточнения вопросов, а менеджер свяжется с вами.",
            reply_markup=kb.get_verified_partner_menu()
        )
        await state.clear()
        return
    # ====================================

    await state.update_data(client_phone=formatted_phone)
    # Запрашиваем адрес
    await message.answer(
        "✅ Номер принят. Введите адрес квартиры (город, улица, дом, кв). \n<i>Пример: Измайлова 43к2-99</i>",
        reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_address)


@dp.message(ClientSubmission.waiting_for_client_address)
async def client_address_received(message: Message, state: FSMContext):
    await state.update_data(client_address=message.text)

    # НОВЫЙ ШАГ: Площадь
    await message.answer(
        "Введите <b>площадь квартиры</b> (м²) или нажмите 'Пропустить'.",
        reply_markup=kb.get_skip_keyboard()
    )
    await state.set_state(ClientSubmission.waiting_for_client_area)


@dp.message(ClientSubmission.waiting_for_client_area)
async def client_area_received(message: Message, state: FSMContext):
    area_text = message.text
    if area_text == "➡️ Пропустить":
        area_text = None

    await state.update_data(client_area=area_text)

    await message.answer(
        "Введите комментарий или нажмите 'Пропустить'.",
        reply_markup=kb.get_skip_keyboard()
    )
    await state.set_state(ClientSubmission.waiting_for_client_comment)


@dp.message(ClientSubmission.waiting_for_client_comment)
async def client_comment_received(message: Message, state: FSMContext):
    comment_text = message.text if message.text != "➡️ Пропустить" else None
    await state.update_data(client_comment=comment_text)

    data = await state.get_data()

    confirmation_text = (
        f"<b>Проверьте данные клиента:</b>\n\n"
        f"👤 <b>Имя:</b> {escape(data['client_name'])}\n"
        f"📞 <b>Тел:</b> {data['client_phone']}\n"
        f"🏠 <b>Адрес:</b> {escape(data['client_address'])}\n"
        f"📐 <b>Площадь:</b> {data['client_area'] or 'Не указана'}\n"
        f"💬 <b>Коммент:</b> {escape(comment_text or '(нет)')}\n\n"
        f"Все верно?"
    )

    await message.answer(confirmation_text, reply_markup=kb.get_client_confirmation_keyboard())
    await state.set_state(ClientSubmission.confirming_data)


@dp.callback_query(F.data == "confirm_client_submission", ClientSubmission.confirming_data)
async def confirm_client_submission(callback: CallbackQuery, state: FSMContext):
    partner_id = callback.from_user.id
    data = await state.get_data()
    partner_data = await db.get_partner_data(partner_id)

    await state.clear()
    await callback.message.edit_text("⏳ Отправка...", reply_markup=None)

    # Отправляем в Битрикс
    deal_id = await bitrix_api.create_client_deal(
        data['client_name'],
        data['client_phone'],
        data['client_address'],
        partner_data['full_name'],
        client_comment=data['client_comment'],
        client_area=data['client_area']
    )

    if deal_id:
        # Сохраняем клиента в БД (включая адрес)
        await db.add_client(partner_id, deal_id, data['client_name'], data['client_address'])
        await callback.message.answer(
            f"✅ Клиент '{escape(data['client_name'])}' успешно отправлен!",
            reply_markup=kb.get_verified_partner_menu()
        )
    else:
        await callback.message.answer(
            "Произошла ошибка при отправке. Попробуйте позже.",
            reply_markup=kb.get_verified_partner_menu()
        )
    await callback.answer()


@dp.callback_query(F.data == "retry_client_submission", ClientSubmission.confirming_data)
async def retry_client_submission(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer("Данные сброшены.")
    await start_client_submission(callback.message, state)


# --- 3. Админская часть (Верификация) ---

@dp.callback_query(F.data.startswith("verify_partner:"))
async def on_verify_partner(callback: CallbackQuery):
    if not await db.get_admin_role(callback.from_user.id):
        await callback.answer("❌ Нет прав.", show_alert=True)
        return

    partner_user_id = int(callback.data.split(":")[1])
    # Логику верификации можно вынести в отдельную функцию, как было, или оставить тут
    # Для краткости - переиспользуем старую логику или пишем напрямую:

    # 1. Обновляем статус
    await db.set_partner_status(partner_user_id, 'verified')

    # 2. Двигаем сделку (если надо)
    deal_id = await db.get_partner_deal_id_by_user_id(partner_user_id)
    if deal_id:
        await bitrix_api.move_deal_stage(deal_id, config.BITRIX_PARTNER_VERIFIED_STAGE_ID)

    # 3. Уведомляем партнера
    try:
        await bot.send_message(partner_user_id,
                               "✅ Вы верифицированный партнер. Теперь вы можете отправлять нам клиентов!",
                               reply_markup=kb.get_verified_partner_menu())
    except:
        pass

    await callback.message.edit_text(callback.message.text + "\n\n✅ ОДОБРЕНО")
    await callback.answer("Партнер верифицирован")


@dp.callback_query(F.data.startswith("reject_partner:"))
async def on_reject_partner(callback: CallbackQuery):
    if not await db.get_admin_role(callback.from_user.id):
        await callback.answer("❌ Нет прав.", show_alert=True)
        return

    partner_user_id = int(callback.data.split(":")[1])
    await db.set_partner_status(partner_user_id, 'rejected')

    deal_id = await db.get_partner_deal_id_by_user_id(partner_user_id)
    if deal_id:
        await bitrix_api.move_deal_stage(deal_id, config.BITRIX_PARTNER_REJECTED_STAGE_ID)

    try:
        await bot.send_message(partner_user_id, "❌ К сожалению, ваша заявка отклонена.",
                               reply_markup=ReplyKeyboardRemove())
    except:
        pass

    await callback.message.edit_text(callback.message.text + "\n\n❌ ОТКЛОНЕНО")
    await callback.answer("Партнер отклонен")


@dp.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()


# --- 4. Статистика и Списки ---

@dp.message(F.text == "📈 Статистика")
async def show_statistics(message: Message):
    if await db.get_partner_status(message.from_user.id) != 'verified':
        return

    stats = await db.get_partner_statistics(message.from_user.id)

    text = (
        f"<b>📊 Ваша статистика:</b>\n\n"
        f"👥 <b>Отправлено клиентов:</b> {stats['total_clients']}\n"
        f"💰 <b>Сумма выплат (в работе/получено):</b> {stats['total_payout']:,.0f} руб.\n"
        f"<i>(Сумма обновляется автоматически при обновлении сделок)</i>"
    )
    await message.answer(text)


@dp.message(F.text == "📊 Мои клиенты")
async def show_my_clients(message: Message, state: FSMContext, offset: int = 0):
    partner_id = message.from_user.id
    if await db.get_partner_status(partner_id) != 'verified':
        return

    total_clients = await db.count_clients_by_partner_id(partner_id)
    if total_clients == 0:
        await message.answer("Вы еще не отправляли клиентов.")
        return

    clients = await db.get_clients_by_partner_id(partner_id, limit=kb.CLIENTS_PER_PAGE, offset=offset)

    # clients now returns (name, status, address)
    response_text = f"<b>Ваши отправленные клиенты (Страница {offset // kb.CLIENTS_PER_PAGE + 1} / {math.ceil(total_clients / kb.CLIENTS_PER_PAGE)}):</b>\n\n"
    start_index = offset + 1

    for i, (client_name, client_status, client_address) in enumerate(clients, start=start_index):
        addr_info = f" ({client_address})" if client_address else ""
        response_text += f"{i}. <b>{escape(client_name)}</b>{escape(addr_info)}\n   Статус: <i>{escape(client_status)}</i>\n"

    keyboard = kb.get_clients_pagination_keyboard(offset, total_clients)
    await message.answer(response_text, reply_markup=keyboard)


@dp.callback_query(F.data.startswith("prev_clients:") | F.data.startswith("next_clients:"))
async def paginate_clients(callback: CallbackQuery, state: FSMContext):
    new_offset = int(callback.data.split(":")[1])
    # Переиспользуем логику show_my_clients, просто вызываем для сообщения
    # Но проще скопировать часть логики:
    partner_id = callback.from_user.id
    total_clients = await db.count_clients_by_partner_id(partner_id)
    clients = await db.get_clients_by_partner_id(partner_id, limit=kb.CLIENTS_PER_PAGE, offset=new_offset)

    response_text = f"<b>Ваши отправленные клиенты (Страница {new_offset // kb.CLIENTS_PER_PAGE + 1} / {math.ceil(total_clients / kb.CLIENTS_PER_PAGE)}):</b>\n\n"
    start_index = new_offset + 1
    for i, (client_name, client_status, client_address) in enumerate(clients, start=start_index):
        addr_info = f" ({client_address})" if client_address else ""
        response_text += f"{i}. <b>{escape(client_name)}</b>{escape(addr_info)}\n   Статус: <i>{escape(client_status)}</i>\n"

    keyboard = kb.get_clients_pagination_keyboard(new_offset, total_clients)
    await callback.message.edit_text(response_text, reply_markup=keyboard)
    await callback.answer()


@dp.message(F.text == "ℹ️ Инфо Программа")
async def show_partnership_info_partner(message: Message):
    info_text = await db.get_setting("partnership_info", "Информация о программе.")
    await message.answer(info_text)


# =================================================================
# === ОБРАБОТЧИКИ AIOHTTP (Сервер) - ОРИГИНАЛЬНАЯ ВЕРСИЯ =======
# =================================================================

async def handle_telegram_GET(request: web.Request):
    """
    ОТЛАДЧИК: Ловит GET-запрос (проверку) от Telegram.
    Всегда отвечает 200 OK.
    """
    logging.info("!!! ПОЛУЧЕН GET-ЗАПРОС (ПРОВЕРКА) ОТ TELEGRAM !!!")
    return web.Response(text="OK")


async def handle_telegram_POST(request: web.Request):
    """
    Ловит POST-запрос (сообщения) от Telegram и передает в aiogram.
    """
    # Проверка секрета (опционально)
    # if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != config.BITRIX_INCOMING_SECRET:
    #    return web.Response(status=403, text="Forbidden")

    try:
        # !!! ВОТ ЭТО ИСПРАВЛЕНИЕ: ЖДЕМ JSON !!!
        data = await request.json()

        # "Ска-рмливаем" обновление aiogram-диспетчеру
        await dp.feed_webhook_update(bot, data)

        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Ошибка обработки POST-запроса от Telegram: {e}")
        return web.Response(status=500, text="Server Error")


async def handle_bitrix_webhook(request: web.Request):
    """
    Обработчик вебхуков от Битрикс24.
    """
    try:
        data = request.query
        if data.get('secret') != config.BITRIX_INCOMING_SECRET:
            return web.Response(status=403, text="Forbidden")

        event_type = data.get('event_type')
        status_or_stage_id = data.get('status')
        deal_id = int(data.get('deal_id', 0))
        user_id = int(data.get('user_id', 0))

        # --- 1. Верификация Партнера ---
        if event_type == 'partner_verification':
            if user_id:
                current_status = await db.get_partner_status(user_id)
                if current_status != status_or_stage_id:
                    await db.set_partner_status(user_id, status_or_stage_id)

                    if status_or_stage_id == 'verified':
                        await bot.send_message(user_id, "✅ Вы верифицированный партнер!",
                                               reply_markup=kb.get_verified_partner_menu())
                    elif status_or_stage_id == 'rejected':
                        await bot.send_message(user_id, "❌ Ваша заявка отклонена.", reply_markup=ReplyKeyboardRemove())

        # --- 2. Обновление Сделки Клиента ---
        elif event_type == 'client_deal_update':
            # Ищем, чей это клиент
            partner_id, client_name = await db.get_partner_and_client_by_deal_id(deal_id)

            if partner_id:
                # А. Получаем актуальную сумму из Битрикса (OPPORTUNITY)
                deal_data = await bitrix_api.get_deal(deal_id)
                opportunity = 0.0
                if deal_data and 'OPPORTUNITY' in deal_data:
                    try:
                        opportunity = float(deal_data['OPPORTUNITY'])
                    except:
                        pass

                # Б. Обновляем статус и сумму в БД
                stage_name = get_client_stage_name(status_or_stage_id)
                await db.update_client_status_and_payout(deal_id, stage_name, opportunity)

                # В. Отправляем уведомления (если статус важный)
                if status_or_stage_id == config.BITRIX_CLIENT_STAGE_WIN:
                    await bot.send_message(
                        partner_id,
                        f"✅ С клиентом <b>{escape(client_name)}</b> заключен договор!\n"
                        f"Сумма сделки: {opportunity:,.0f} руб."
                    )
                elif status_or_stage_id == config.BITRIX_CLIENT_STAGE_LOSE:
                    await bot.send_message(
                        partner_id,
                        f"❌ Клиент <b>{escape(client_name)}</b> перешел в статус 'Отказ'."
                    )
                elif status_or_stage_id == config.BITRIX_CLIENT_STAGE_2:  # Назначена встреча
                    await bot.send_message(
                        partner_id,
                        f"ℹ️ С клиентом <b>{escape(client_name)}</b> назначена встреча."
                    )

        return web.Response(text="OK")

    except Exception as e:
        logging.error(f"Ошибка в обработке Bitrix webhook: {e}")
        return web.Response(status=500, text="Server Error")


# =================================================================
# === ЗАПУСК СЕРВЕРА ==============================================
# =================================================================

async def on_startup(app_instance: web.Application):
    """Выполняется при старте сервера."""
    await db.init_db()  # Инициализация БД (и миграции)

    # Добавляем суперадмина
    await db.add_admin(config.SUPER_ADMIN_ID, "SUPER_ADMIN", "senior")

    # Тексты по умолчанию
    default_info_text = "Информация о программе..."
    current_info = await db.get_setting("partnership_info")
    if not current_info:
        await db.set_setting("partnership_info", default_info_text)

    default_welcome = "Здравствуйте! Это партнерский бот."
    current_welcome = await db.get_setting("welcome_text")
    if not current_welcome:
        await db.set_setting("welcome_text", default_welcome)

    # Вебхук Telegram
    webhook_url = config.BASE_WEBHOOK_URL + config.TELEGRAM_WEBHOOK_PATH
    await bot.set_webhook(
        url=webhook_url,
        secret_token=config.BITRIX_INCOMING_SECRET
    )
    logging.info(f"Telegram вебхук установлен на: {webhook_url}")


async def on_shutdown(app_instance: web.Application):
    """Выполняется при остановке сервера."""
    logging.info("Остановка сервера...")
    await bot.delete_webhook()
    logging.info("Telegram вебхук удален.")


def main():
    """Главная функция запуска."""

    # 1. GET (Проверка)
    app.router.add_get(
        config.TELEGRAM_WEBHOOK_PATH,
        handle_telegram_GET
    )

    # 2. POST (Сообщения Telegram)
    app.router.add_post(
        config.TELEGRAM_WEBHOOK_PATH,
        handle_telegram_POST
    )

    # 3. POST (Битрикс)
    app.router.add_post(
        config.BITRIX_WEBHOOK_PATH,
        handle_bitrix_webhook
    )

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    logging.info(f"Запуск веб-сервера на {config.WEB_SERVER_HOST}:{config.WEB_SERVER_PORT}")
    web.run_app(
        app,
        host=config.WEB_SERVER_HOST,
        port=config.WEB_SERVER_PORT,
    )


if __name__ == "__main__":
    main()