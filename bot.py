# bot.py
import re
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
import math

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = web.Application()

# --- Тексты бота ---
WELCOME_TEXT = """
Здравствуйте! 🤝

Это бот партнерской системы компании [Название Компании].
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

# === НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ===
def get_client_stage_name(stage_id: str) -> str:
    """Превращает системный ID стадии в понятное название."""
    stages_map = {
        config.BITRIX_CLIENT_STAGE_1: "Клиенты в обработке",
        config.BITRIX_CLIENT_STAGE_2: "С клиентом назначена встреча",
        config.BITRIX_CLIENT_STAGE_3: "Расчет сметы",
        config.BITRIX_CLIENT_STAGE_WIN: "С клиентом заключен договор",
        config.BITRIX_CLIENT_STAGE_LOSE: "Отказ клиента"
    }
    # Возвращаем название или сам ID, если название не найдено
    return stages_map.get(stage_id, stage_id)
# =====================================


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

@dp.message(F.text == "ℹ️ Инфо Программа")
async def show_partnership_info_partner(message: Message):
    """
    Показывает партнеру актуальную информацию о программе из БД.
    """
    partner_id = message.from_user.id
    status = await db.get_partner_status(partner_id)

    if status != 'verified' and  partner_id not in await db.get_all_admin_ids() :
        await message.answer("Эта функция доступна только верифицированным партнерам.")
        return

    info_text = await db.get_setting("partnership_info", "Информация о программе пока не заполнена.")
    await message.answer(info_text)

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
    """
    Получили телефон клиента, ВАЛИДИРУЕМ, запрашиваем адрес.
    """
    phone_text = message.text

    # 1. Очищаем номер от скобок, тире, пробелов и т.д.
    # Оставляем только цифры
    cleaned_phone = re.sub(r'\D', '', phone_text)  # \D = "любой не-цифровой символ"

    # 2. Нормализуем номер (для РФ)

    # Если ввели '8 (999)...' -> '7999...'
    if cleaned_phone.startswith('8') and len(cleaned_phone) == 11:
        cleaned_phone = '7' + cleaned_phone[1:]

    # Если ввели '999...' (10 цифр) -> '7999...'
    elif len(cleaned_phone) == 10:
        cleaned_phone = '7' + cleaned_phone

    # Если ввели '+7 (999)...' -> '7999...'
    # (Это произойдет автоматически на шаге 1)

    # 3. Финальная проверка: должен быть 11 цифр и начинаться с 7
    if not (len(cleaned_phone) == 11 and cleaned_phone.startswith('7')):
        await message.answer(
            "❌ <b>Неверный формат номера.</b>\n\n"
            "Пожалуйста, введите номер телефона клиента (РФ) в любом удобном формате, "
            "например: <i>+79991234567</i>, <i>8(999)123-45-67</i> или <i>9991234567</i>."
        )
        # Остаемся в том же состоянии, ждем корректный ввод
        return

    # 4. Форматируем в +7... для сохранения
    formatted_phone = '+' + cleaned_phone

    # 5. Сохраняем и идем на следующий шаг
    await state.update_data(client_phone=formatted_phone)
    await message.answer("✅ Номер принят. Теперь введите адрес квартиры (город, улица, дом, кв):",
                         reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_address)


@dp.message(ClientSubmission.waiting_for_client_address)
async def client_address_received(message: Message, state: FSMContext):
    """
    Получили адрес, показываем данные для подтверждения.
    """
    # Сохраняем адрес в FSM
    await state.update_data(client_address=message.text)
    # Получаем все данные из FSM
    data = await state.get_data()
    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    client_address = data.get('client_address')

    # Формируем сообщение для подтверждения
    confirmation_text = (
        f"<b>Проверьте данные клиента:</b>\n\n"
        f"<b>ФИО:</b> {escape(client_name)}\n"
        f"<b>Телефон:</b> {escape(client_phone)}\n"
        f"<b>Адрес:</b> {escape(client_address)}\n\n"
        f"Все верно?"
    )
    # Отправляем сообщение с кнопками
    await message.answer(
        confirmation_text,
        reply_markup=kb.get_client_confirmation_keyboard()
    )
    # Переводим в состояние подтверждения
    await state.set_state(ClientSubmission.confirming_data)

@dp.callback_query(F.data == "confirm_client_submission", ClientSubmission.confirming_data)
async def confirm_client_submission(callback: CallbackQuery, state: FSMContext):
    """
    Ловит нажатие '✅ Подтвердить'. Отправляет данные.
    """
    partner_id = callback.from_user.id
    data = await state.get_data()
    partner_data = await db.get_partner_data(partner_id)

    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    client_address = data.get('client_address')
    partner_name = partner_data.get('full_name')

    await state.clear()

    # Убираем кнопки из сообщения
    await callback.message.edit_reply_markup(reply_markup=None)

    # Отправляем в Битрикс
    deal_id = await bitrix_api.create_client_deal(
        client_name, client_phone, client_address, partner_name
    )

    if deal_id:
        await db.add_client(partner_id, deal_id, client_name)
        await callback.message.answer(
            f"✅ Клиент '{escape(client_name)}' успешно отправлен!",
            reply_markup=kb.get_verified_partner_menu()
        )
    else:
        await callback.message.answer(
            GENERIC_ERROR_TEXT,
            reply_markup=kb.get_verified_partner_menu()
        )
    await callback.answer() # Закрываем "часики" на кнопке

@dp.callback_query(F.data == "retry_client_submission", ClientSubmission.confirming_data)
async def retry_client_submission(callback: CallbackQuery, state: FSMContext):
    """
    Ловит нажатие '🔄 Заполнить заново'. Перезапускает FSM.
    """
    await state.clear()
    # Убираем кнопки и текст подтверждения
    await callback.message.delete()
    await callback.answer("Данные сброшены. Начинаем заново.")
    # Вызываем функцию, которая начинает ввод клиента
    await start_client_submission(callback.message, state)

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
@dp.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery):
    await callback.answer() # Просто закрываем "часики"

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

@dp.message(Command("setinfotext"), IsSeniorAdminFilter())
async def cmd_set_info_text(message: Message):
    """
    Устанавливает новый текст для раздела 'Инфо Программа'. (Только Senior)
    Использование: /setinfotext <весь текст информации>
    Поддерживает HTML-разметку.
    """
    new_text = message.text[len("/setinfotext"):].strip()

    if not new_text:
        await message.answer("❌ Вы не указали текст.\n<b>Использование:</b> /setinfotext &lt;весь текст информации&gt;")
        return

    try:
        await db.set_setting("partnership_info", new_text)
        await message.answer("✅ Текст информации о программе успешно обновлен.")
    except Exception as e:
        logging.error(f"Ошибка при обновлении текста программы: {e}")
        await message.answer(f"❌ Произошла ошибка при сохранении текста: {escape(str(e))}")

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

@dp.message(F.text == "📊 Мои клиенты")
async def show_my_clients(message: Message, state: FSMContext, offset: int = 0):
    """
    Показывает партнеру список отправленных им клиентов (с пагинацией).
    offset - смещение для пагинации (начинается с 0).
    """
    CLIENTS_PER_PAGE = 5
    await state.clear() # На всякий случай сбрасываем состояние
    partner_id = message.from_user.id
    status = await db.get_partner_status(partner_id)

    if status != 'verified':
        await message.answer("Эта функция доступна только верифицированным партнерам.")
        return

    # Получаем общее количество клиентов
    total_clients = await db.count_clients_by_partner_id(partner_id)

    if total_clients == 0:
        await message.answer("Вы еще не отправляли клиентов.")
        return

    # Получаем клиентов для ТЕКУЩЕЙ страницы
    clients = await db.get_clients_by_partner_id(partner_id, limit=CLIENTS_PER_PAGE, offset=offset)

    if not clients and offset > 0: # Если вдруг попали на пустую страницу
         await message.answer("Больше клиентов нет.")
         return
    # Формируем текст сообщения
    response_text = f"<b>Ваши отправленные клиенты (Страница {offset // CLIENTS_PER_PAGE + 1} / {math.ceil(total_clients / CLIENTS_PER_PAGE)}):</b>\n\n"
    # Нумеруем клиентов начиная с offset + 1
    start_index = offset + 1
    for i, (client_name, client_status) in enumerate(clients, start=start_index):
        response_text += f"{i}. <b>{escape(client_name)}</b>\n   Статус: <i>{escape(client_status)}</i>\n"

    # Получаем клавиатуру пагинации
    keyboard = kb.get_clients_pagination_keyboard(offset, total_clients)

    # Отправляем сообщение
    await message.answer(response_text, reply_markup=keyboard)

@dp.callback_query(F.data.startswith("prev_clients:") | F.data.startswith("next_clients:"))
async def paginate_clients(callback: CallbackQuery, state: FSMContext):
    """
    Обрабатывает нажатия кнопок пагинации "Назад" и "Вперед".
    """
    CLIENTS_PER_PAGE = 5
    try:
        # Извлекаем новый offset из callback_data (e.g., "next_clients:5")
        new_offset = int(callback.data.split(":")[1])

        partner_id = callback.from_user.id
        total_clients = await db.count_clients_by_partner_id(partner_id)
        clients = await db.get_clients_by_partner_id(partner_id, limit=CLIENTS_PER_PAGE, offset=new_offset)

        if not clients:
            await callback.answer("Больше клиентов нет.")
            return

        # Формируем новый текст
        response_text = f"<b>Ваши отправленные клиенты (Страница {new_offset // CLIENTS_PER_PAGE + 1} / {math.ceil(total_clients / CLIENTS_PER_PAGE)}):</b>\n\n"
        start_index = new_offset + 1
        for i, (client_name, client_status) in enumerate(clients, start=start_index):
            response_text += f"{i}. <b>{escape(client_name)}</b>\n   Статус: <i>{escape(client_status)}</i>\n"

        # Получаем новую клавиатуру
        keyboard = kb.get_clients_pagination_keyboard(new_offset, total_clients)

        # Редактируем исходное сообщение
        await callback.message.edit_text(response_text, reply_markup=keyboard)
        await callback.answer() # Закрываем часики

    except Exception as e:
        logging.error(f"Ошибка пагинации клиентов: {e}")
        await callback.answer("Произошла ошибка при загрузке.")
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
    !!! ВЕРСИЯ 4.0 (Партнеры + Клиенты) !!!
    Обрабатывает GET-запросы от роботов из ОБЕИХ воронок.
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
        status_or_stage_id = data.get('status')  # 'verified', 'rejected' ИЛИ 'C0:5', 'C0:WON'
        deal_id = int(data.get('deal_id', 0))
        user_id_from_b24_str = str(data.get('user_id', ''))

        # 3. Обрабатываем ивент верификации ПАРТНЕРА
        if event_type == 'partner_verification':
            status = status_or_stage_id
            user_id = int(user_id_from_b24_str) if user_id_from_b24_str.isdigit() else None

            if not user_id:
                logging.error(f"Ошибка от Робота-Партнера: не пришел user_id для сделки {deal_id}.")
                return web.Response(text="OK (no user_id)")

            logging.info(f"Получен статус верификации '{status}' для партнера {user_id} (сделка {deal_id})")
            current_status = await db.get_partner_status(user_id)

            if not current_status:
                logging.warning(f"Партнер {user_id} не найден в БД.")
                return web.Response(text="OK (partner not found)")

            if current_status != status:
                # ... (вся логика верификации партнера, которую мы уже написали) ...
                await db.set_partner_status(user_id, status)

                notification_text = ""
                reply_markup = ReplyKeyboardRemove()

                if status == 'verified':
                    notification_text = VERIFIED_TEXT
                    reply_markup = kb.get_verified_partner_menu()
                elif status == 'rejected':
                    notification_text = STATUS_REJECTED_REVOKED_TEXT if current_status == 'verified' else REJECTED_TEXT
                elif status == 'pending':
                    notification_text = STATUS_PENDING_REVOKED_TEXT

                if notification_text:
                    try:
                        await bot.send_message(user_id, notification_text, reply_markup=reply_markup)
                    except Exception as e:
                        logging.warning(f"Не удалось уведомить партнера {user_id}: {e}")
            else:
                logging.info(f"Партнер {user_id} уже имеет статус '{status}'. Игнорируем.")

        # === НОВАЯ ЛОГИКА: Обрабатываем ивент КЛИЕНТА ===
        elif event_type == 'client_deal_update':
            new_stage_id = status_or_stage_id  # Получаем ID новой стадии (e.g., 'C0:5')

            # Ищем, какому партнеру принадлежит эта сделка
            partner_id, client_name = await db.get_partner_and_client_by_deal_id(deal_id)

            if partner_id:
                logging.info(f"Обновляем Сделку-Клиента {deal_id} для партнера {partner_id}")

                # Получаем "красивое" имя стадии
                stage_name = get_client_stage_name(new_stage_id)

                # Обновляем статус в нашей локальной БД
                await db.update_client_status_by_deal_id(deal_id, stage_name)

                # === ИЗМЕНЕНИЕ ЗДЕСЬ: Отправляем уведомление ТОЛЬКО для нужных стадий ===
                # Собираем ID "важных" стадий из конфига
                important_stages = [
                    get_client_stage_name(config.BITRIX_CLIENT_STAGE_2),  # "Назначена встреча"
                    get_client_stage_name(config.BITRIX_CLIENT_STAGE_WIN),# "Подписан договор"
                    get_client_stage_name(config.BITRIX_CLIENT_STAGE_LOSE)  #Сделка отменена
                ]
                # Проверяем, является ли новая стадия одной из "важных"
                if new_stage_id in important_stages:
                    try:
                        await bot.send_message(
                            partner_id,
                            f"ℹ️ Статус вашего клиента <b>{escape(client_name)}</b> был обновлен.\n<b>Новый этап:</b> {stage_name}"
                        )
                        logging.info(
                            f"Партнеру {partner_id} отправлено уведомление о стадии '{stage_name}' (сделка {deal_id}).")
                    except Exception as e:
                        logging.warning(f"Не удалось уведомить партнера {partner_id} о сделке {deal_id}: {e}")
                else:
                    logging.info(
                        f"Стадия '{stage_name}' (сделка {deal_id}) не требует уведомления партнера {partner_id}.")
                # =========================================================================
            else:
                logging.warning(f"Получен апдейт по Сделке-Клиенту {deal_id}, но она не найдена в нашей БД.")
        # ========================================

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
    default_info_text = """
    <b>ℹ️ Информация о Партнерской Программе</b>

    Здесь будет текст с актуальной информацией о программе:
    - Условия вознаграждения партнеров (% ставки, бонусы).
    - Процесс выплат.
    - Требования к партнерам.
    - Контакты ответственного менеджера.

    <i>(Старший администратор может изменить этот текст командой /setinfotext)</i>
        """
    current_info = await db.get_setting("partnership_info")
    if not current_info:
        await db.set_setting("partnership_info", default_info_text.strip())
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
    telegram_webhook_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        # (Опционально) Можете передать ваш секрет, если хотите доп. проверку
        # secret_token=config.YOUR_SECRET_TOKEN
    )

    # 2. Регистрируем этого "слушателя" на ЛЮБОЙ метод
    app.router.add_route(
        "*", # Ловить и GET (для проверки) и POST (для апдейтов)
        config.TELEGRAM_WEBHOOK_PATH,
        telegram_webhook_handler # Передаем сюда обработчик aiogram
    )

    # 3. Регистрируем обработчик для Битрикс (остается без изменений)
    app.router.add_post(
        config.BITRIX_WEBHOOK_PATH,
        handle_bitrix_webhook
    )
    # ======================

    # Добавляем функции старта/остановки
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
