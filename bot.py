# bot.py
import re
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command, Filter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove
from aiogram.client.default import DefaultBotProperties
from html import escape
import math

# Импортируем модули проекта
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

# =================================================================
# === СПИСОК СТАДИЙ ДЛЯ УВЕДОМЛЕНИЙ ===============================
# =================================================================

# Слева: ТОЧНОЕ название стадии, которое присылает Битрикс (текстом).
# Справа: Тип уведомления ('win', 'lose', 'meeting').
NOTIFICATIONS_MAP = {
    # Успешные стадии
    "С клиентом заключен договор": "win",

    # Провальные стадии
    "Отказ клиента": "lose",
    # Промежуточные стадии
    "С клиентом назначена встреча": "meeting",
    "Встреча назначена": "meeting"
}

# =================================================================
# === ВСПОМОГАТЕЛЬНЫЕ КЛАССЫ И ФУНКЦИИ ============================
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


async def process_partner_verification(admin_id: int, partner_user_id: int, new_status: str,
                                       callback: CallbackQuery = None):
    """
    Ядро верификации.
    Позволяет менять статус даже если партнер уже был отклонен (re-verification).
    """
    try:
        partner_data = await db.get_partner_data(partner_user_id)
        if not partner_data:
            msg = "Партнер не найден в БД."
            if callback:
                await callback.answer(msg, show_alert=True)
            else:
                await bot.send_message(admin_id, msg)
            return

        partner_name = partner_data.get('full_name', f'ID: {partner_user_id}')

        # 1. Обновляем статус в БД
        await db.set_partner_status(partner_user_id, new_status)

        # 2. Двигаем сделку в Битрикс
        deal_id = await db.get_partner_deal_id_by_user_id(partner_user_id)
        if deal_id:
            target_stage = config.BITRIX_PARTNER_VERIFIED_STAGE_ID if new_status == 'verified' else config.BITRIX_PARTNER_REJECTED_STAGE_ID
            if target_stage:
                await bitrix_api.move_deal_stage(deal_id, target_stage)

        # 3. Уведомляем партнера
        if new_status == 'verified':
            await bot.send_message(partner_user_id,
                                   "✅ Вы верифицированный партнер. Теперь вы можете отправлять нам клиентов!",
                                   reply_markup=kb.get_verified_partner_menu())
        else:
            await bot.send_message(partner_user_id, "❌ К сожалению, ваша заявка была отклонена.",
                                   reply_markup=ReplyKeyboardRemove())

        # 4. Ответ админу
        admin_text = f"Партнер {escape(partner_name)} (ID: {partner_user_id}) -> {new_status}."
        if callback:
            # Пытаемся отредактировать сообщение, если оно не слишком старое
            try:
                await callback.message.edit_text(callback.message.text + f"\n\n<b>Итог:</b> {new_status.capitalize()}")
            except:
                pass
            await callback.answer(admin_text)
        elif admin_id > 0:
            await bot.send_message(admin_id, f"✅ {admin_text}")

    except Exception as e:
        logging.error(f"Ошибка верификации: {e}")
        if callback:
            await callback.answer("Ошибка при обработке.", show_alert=True)
        elif admin_id > 0:
            await bot.send_message(admin_id, f"Ошибка: {e}")


# =================================================================
# === ОБРАБОТЧИКИ TELEGRAM: ОБЩИЕ =================================
# =================================================================

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    status = await db.get_partner_status(message.from_user.id)

    if status == 'verified':
        await message.answer("✅ Вы верифицированный партнер.", reply_markup=kb.get_verified_partner_menu())
    elif status == 'pending':
        await message.answer("⏳ Ваша заявка на верификацию принята.", reply_markup=ReplyKeyboardRemove())
    elif status == 'rejected':
        await message.answer("❌ Ваша заявка была отклонена.", reply_markup=ReplyKeyboardRemove())
    else:
        welcome_text = await db.get_setting("welcome_text",
                                            "Здравствуйте! Нажимая 'Я согласен', вы принимаете условия.")
        await message.answer(welcome_text, reply_markup=kb.get_agree_keyboard())


@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await cmd_start(message, state)


@dp.message(F.text == "ℹ️ Инфо Программа")
async def show_partnership_info_partner(message: Message):
    info_text = await db.get_setting("partnership_info", "Информация о программе.")
    await message.answer(info_text)


# =================================================================
# === РЕГИСТРАЦИЯ ПАРТНЕРА (FSM) ==================================
# =================================================================

@dp.callback_query(F.data == "agree_to_terms")
async def process_agree(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Отлично! Давайте начнем регистрацию.")
    await callback.message.answer("Пожалуйста, выберите, кем вы являетесь:", reply_markup=kb.get_role_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_role)
    await callback.answer()


@dp.message(PartnerRegistration.waiting_for_role, F.text)
async def process_role(message: Message, state: FSMContext):
    if message.text not in ["Риэлтор", "Дизайнер", "Приемщик", "Другое"]:
        await message.answer("Пожалуйста, выберите вариант из меню.")
        return
    await state.update_data(role=message.text)
    await message.answer("Пожалуйста, введите ваше ФИО:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_full_name)


@dp.message(PartnerRegistration.waiting_for_full_name, F.text)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("Теперь поделитесь номером телефона.", reply_markup=kb.get_request_phone_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_phone)


@dp.message(PartnerRegistration.waiting_for_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    user_id = message.from_user.id
    data = await state.get_data()
    full_name = data.get('full_name')
    role = data.get('role')
    username = message.from_user.username
    await state.clear()

    # 1. Создаем сделку
    deal_id = await bitrix_api.create_partner_deal(full_name, phone, user_id, username, role)

    if deal_id:
        # 2. Сохраняем в БД (теперь add_partner поддерживает role)
        await db.add_partner(user_id, full_name, phone, deal_id, role)
        await message.answer("⏳ Ваша заявка принята. Менеджер свяжется с вами.", reply_markup=ReplyKeyboardRemove())

        # Уведомление Junior-админов
        notification_text = (
            f"🔔 <b>Новая заявка на партнерство!</b>\n"
            f"<b>ФИО:</b> {escape(full_name)}\n"
            f"<b>Роль:</b> {escape(role)}\n"
            f"<b>Телефон:</b> {escape(phone)}\n"
        )
        keyboard = kb.get_verification_keyboard(user_id)
        for admin_id in await db.get_junior_admin_ids():
            try:
                await bot.send_message(admin_id, notification_text, reply_markup=keyboard)
            except:
                pass
    else:
        await message.answer("Произошла ошибка при регистрации. Попробуйте позже.", reply_markup=ReplyKeyboardRemove())


@dp.message(PartnerRegistration.waiting_for_phone)
async def process_phone_invalid(message: Message):
    await message.answer("Нажмите кнопку 'Поделиться номером телефона'.")


# =================================================================
# === ОТПРАВКА КЛИЕНТА (FSM) ======================================
# =================================================================

@dp.message(F.text == "🚀 Отправить клиента")
async def start_client_submission(message: Message, state: FSMContext):
    status = await db.get_partner_status(message.from_user.id)
    if status != 'verified':
        await message.answer("Эта функция доступна только верифицированным партнерам.")
        return
    await message.answer("Введите <b>Имя и Фамилию</b> клиента:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_name)


@dp.message(ClientSubmission.waiting_for_client_name, F.text)
async def client_name_received(message: Message, state: FSMContext):
    await state.update_data(client_name=message.text)
    await message.answer("Введите номер телефона клиента:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_phone)


@dp.message(ClientSubmission.waiting_for_client_phone, F.text)
async def client_phone_received(message: Message, state: FSMContext):
    phone_text = message.text
    cleaned = re.sub(r'\D', '', phone_text)
    if cleaned.startswith('8') and len(cleaned) == 11:
        cleaned = '7' + cleaned[1:]
    elif len(cleaned) == 10:
        cleaned = '7' + cleaned

    if not (len(cleaned) == 11 and cleaned.startswith('7')):
        await message.answer("❌ Неверный формат. Введите номер РФ (начинается с +7, 8 или 9).")
        return
    formatted_phone = '+' + cleaned

    # Проверка дубля
    contact_id = await bitrix_api.check_contact_exists_by_phone(formatted_phone)
    if contact_id:
        p_data = await db.get_partner_data(message.from_user.id)
        c_name = (await state.get_data()).get('client_name')
        await bitrix_api.create_duplicate_alert_deal(c_name, formatted_phone, p_data['full_name'])

        await message.answer(
            f"ℹ️ Клиент с номером {formatted_phone} уже есть в базе.\nМы свяжемся с ним, а менеджер свяжется с вами.",
            reply_markup=kb.get_verified_partner_menu()
        )
        await state.clear()
        return

    await state.update_data(client_phone=formatted_phone)
    await message.answer("✅ Введите адрес квартиры (город, улица, дом, кв):", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_address)


@dp.message(ClientSubmission.waiting_for_client_address, F.text)
async def client_address_received(message: Message, state: FSMContext):
    await state.update_data(client_address=message.text)
    await message.answer("Введите <b>площадь квартиры</b> (м²) или 'Пропустить':", reply_markup=kb.get_skip_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_area)


@dp.message(ClientSubmission.waiting_for_client_area, F.text)
async def client_area_received(message: Message, state: FSMContext):
    area = message.text if message.text != "➡️ Пропустить" else None
    await state.update_data(client_area=area)
    await message.answer("Введите комментарий или 'Пропустить':", reply_markup=kb.get_skip_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_comment)


@dp.message(ClientSubmission.waiting_for_client_comment, F.text)
async def client_comment_received(message: Message, state: FSMContext):
    comm = message.text if message.text != "➡️ Пропустить" else None
    await state.update_data(client_comment=comm)
    data = await state.get_data()

    client_name = data.get('client_name') or "Не указано"
    client_address = data.get('client_address') or "Не указано"

    txt = (
        f"<b>Проверьте данные:</b>\n\n"
        f"👤 <b>Имя:</b> {escape(client_name)}\n"
        f"📞 <b>Тел:</b> {data.get('client_phone', '-')}\n"
        f"🏠 <b>Адрес:</b> {escape(client_address)}\n"
        f"📐 <b>Площадь:</b> {escape(data.get('client_area') or '-')}\n"
        f"💬 <b>Коммент:</b> {escape(comm or '-')}\n\n"
        f"Все верно?"
    )
    await message.answer(txt, reply_markup=kb.get_client_confirmation_keyboard())
    await state.set_state(ClientSubmission.confirming_data)


@dp.callback_query(F.data == "confirm_client_submission", ClientSubmission.confirming_data)
async def confirm_client(callback: CallbackQuery, state: FSMContext):
    p_id = callback.from_user.id
    d = await state.get_data()
    p_data = await db.get_partner_data(p_id)
    await state.clear()
    await callback.message.edit_text("⏳ Отправка...", reply_markup=None)

    deal_id = await bitrix_api.create_client_deal(
        d['client_name'], d['client_phone'], d['client_address'],
        p_data['full_name'], d['client_comment'], d['client_area']
    )
    if deal_id:
        await db.add_client(p_id, deal_id, d['client_name'], d['client_address'])
        await callback.message.answer(f"✅ Клиент '{escape(d['client_name'])}' отправлен!",
                                      reply_markup=kb.get_verified_partner_menu())
    else:
        await callback.message.answer("Ошибка при отправке.", reply_markup=kb.get_verified_partner_menu())
    await callback.answer()


@dp.callback_query(F.data == "retry_client_submission", ClientSubmission.confirming_data)
async def retry_client(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await start_client_submission(callback.message, state)


# =================================================================
# === СТАТИСТИКА И СПИСКИ =========================================
# =================================================================

@dp.message(F.text == "📈 Статистика")
async def show_statistics(message: Message):
    # Проверка прав доступа
    if await db.get_partner_status(message.from_user.id) != 'verified':
        return

    # 1. Получаем всех клиентов партнера из БД
    # Функция get_all_partner_clients возвращает (Имя, Статус, Сумма)
    clients = await db.get_all_partner_clients(message.from_user.id)

    total_clients = len(clients)
    sum_in_work = 0.0  # Сумма "В работе"
    sum_on_approval = 0.0  # Сумма "На согласовании" (Победа)

    details_text = ""

    # Получаем названия стадий из конфига для точного сравнения
    win_stage_name = get_client_stage_name(config.BITRIX_CLIENT_STAGE_WIN)
    lose_stage_name = get_client_stage_name(config.BITRIX_CLIENT_STAGE_LOSE)

    # 2. Проходим по списку клиентов и считаем деньги
    for name, status, payout in clients:
        payout = payout or 0.0

        if status == win_stage_name:
            # Если статус "Договор заключен" -> деньги на согласовании
            sum_on_approval += payout
            icon = "🟢"
        elif status == lose_stage_name:
            # Если статус "Отказ" -> деньги не считаем
            icon = "🔴"
        else:
            # Все остальные статусы -> деньги в работе
            sum_in_work += payout
            icon = "🟡"

        # Добавляем строку в список детализации
        details_text += f"• {escape(name)}: <b>{payout:,.0f} ₽</b> {icon}\n"

    # 3. Формируем итоговое сообщение
    text = (
        f"<b>📊 Финансовая статистика:</b>\n\n"
        f"🟡 <b>В работе:</b> {sum_in_work:,.0f} руб.\n"
        f"<i>(Прогноз по активным сделкам)</i>\n\n"
        f"🟢 <b>На согласовании:</b> {sum_on_approval:,.0f} руб.\n"
        f"<i>(Договор подписан, ожидайте выплату)</i>\n\n"
        f"👥 <b>Всего клиентов:</b> {total_clients}\n"
        f"--------------------------\n"
        f"<b>Детализация:</b>\n"
        f"{details_text}"
    )

    # Обрезаем сообщение, если оно длиннее лимита Telegram (4096 символов)
    if len(text) > 4000:
        text = text[:4000] + "\n\n... (список обрезан)"

    await message.answer(text)

@dp.message(F.text == "📊 Мои клиенты")
async def show_my_clients(message: Message, state: FSMContext, offset: int = 0):
    p_id = message.from_user.id
    if await db.get_partner_status(p_id) != 'verified': return
    total = await db.count_clients_by_partner_id(p_id)
    if total == 0:
        await message.answer("Вы еще не отправляли клиентов.")
        return
    clients = await db.get_clients_by_partner_id(p_id, limit=kb.CLIENTS_PER_PAGE, offset=offset)

    text = f"<b>Ваши клиенты ({offset + 1}-{min(offset + kb.CLIENTS_PER_PAGE, total)} из {total}):</b>\n\n"
    for i, (name, status, addr) in enumerate(clients, start=offset + 1):
        a_info = f" ({addr})" if addr else ""
        text += f"{i}. <b>{escape(name)}</b>{escape(a_info)}\n   Статус: <i>{escape(status)}</i>\n"

    await message.answer(text, reply_markup=kb.get_clients_pagination_keyboard(offset, total))


@dp.callback_query(F.data.startswith("prev_clients:") | F.data.startswith("next_clients:"))
async def paginate_clients(callback: CallbackQuery, state: FSMContext):
    off = int(callback.data.split(":")[1])
    # Повтор логики show_my_clients
    p_id = callback.from_user.id
    total = await db.count_clients_by_partner_id(p_id)
    clients = await db.get_clients_by_partner_id(p_id, limit=kb.CLIENTS_PER_PAGE, offset=off)

    text = f"<b>Ваши клиенты ({off + 1}-{min(off + kb.CLIENTS_PER_PAGE, total)} из {total}):</b>\n\n"
    for i, (name, status, addr) in enumerate(clients, start=off + 1):
        a_info = f" ({addr})" if addr else ""
        text += f"{i}. <b>{escape(name)}</b>{escape(a_info)}\n   Статус: <i>{escape(status)}</i>\n"

    await callback.message.edit_text(text, reply_markup=kb.get_clients_pagination_keyboard(off, total))
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop_cb(c: CallbackQuery): await c.answer()


# =================================================================
# === АДМИНСКИЕ КОМАНДЫ ===========================================
# =================================================================

@dp.message(Command("verify"), IsAdminFilter())
async def cmd_verify(message: Message):
    """Ручная верификация: /verify 12345"""
    try:
        uid = int(message.text.split()[1])
        await process_partner_verification(message.from_user.id, uid, 'verified')
    except Exception as e:
        await message.answer(f"Ошибка: {e}\nИспользование: /verify ID")


@dp.callback_query(F.data.startswith("verify_partner:"))
async def on_verify_callback(callback: CallbackQuery):
    if not await db.get_admin_role(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    uid = int(callback.data.split(":")[1])
    await process_partner_verification(callback.from_user.id, uid, 'verified', callback)


@dp.callback_query(F.data.startswith("reject_partner:"))
async def on_reject_callback(callback: CallbackQuery):
    if not await db.get_admin_role(callback.from_user.id):
        await callback.answer("Нет прав.", show_alert=True)
        return
    uid = int(callback.data.split(":")[1])
    await process_partner_verification(callback.from_user.id, uid, 'rejected', callback)


@dp.message(Command("addadmin"), IsSeniorAdminFilter())
async def cmd_add_admin(message: Message):
    """/addadmin 12345 junior Name"""
    try:
        parts = message.text.split()
        uid, role = int(parts[1]), parts[2].lower()
        name = " ".join(parts[3:]) if len(parts) > 3 else f"Admin_{uid}"
        if role not in ('junior', 'senior'): raise ValueError("Role must be junior or senior")

        await db.add_admin(uid, name, role)
        await message.answer(f"✅ Админ {name} ({role}) добавлен.")
    except Exception as e:
        await message.answer(f"Ошибка: {e}\n/addadmin ID role [Name]")


@dp.message(Command("deladmin"), IsSeniorAdminFilter())
async def cmd_del_admin(message: Message):
    try:
        uid = int(message.text.split()[1])
        if uid == config.SUPER_ADMIN_ID:
            await message.answer("Нельзя удалить Супер-Админа.")
            return
        await db.remove_admin(uid)
        await message.answer(f"✅ Админ {uid} удален.")
    except:
        await message.answer("/deladmin ID")


@dp.message(Command("listadmins"), IsSeniorAdminFilter())
async def cmd_list_admins(message: Message):
    admins = await db.list_admins()
    txt = "<b>Админы:</b>\n" + "\n".join([f"• {u} (ID:{i}) - {r}" for i, u, r in admins])
    await message.answer(txt)


@dp.message(Command("setinfotext"), IsSeniorAdminFilter())
async def cmd_set_info_text(message: Message):
    """/setinfotext info ТЕКСТ"""
    try:
        args = message.text[len("/setinfotext"):].strip()
        ctype, text = args.split(maxsplit=1)
        if ctype == 'info':
            key = "partnership_info"
        elif ctype == 'welcome':
            key = "welcome_text"
        else:
            raise ValueError

        await db.set_setting(key, text)
        await message.answer(f"✅ Текст '{ctype}' обновлен.")
    except:
        await message.answer("/setinfotext info|welcome ТЕКСТ")


@dp.message(Command("setpercent"), IsSeniorAdminFilter())
async def cmd_set_percent(message: Message):
    """
    Устанавливает процент выплаты партнеру.
    Использование: /setpercent 10 (означает 10%)
    """
    try:
        parts = message.text.split()
        # Проверяем, что есть аргумент
        if len(parts) < 2:
            raise ValueError

        value_str = parts[1]
        value_float = float(value_str.replace(',', '.'))

        await db.set_setting("payout_percent", str(value_float))

        await message.answer(f"✅ Процент выплаты успешно обновлен: <b>{value_float}%</b>")
    except Exception:
        # ИСПРАВЛЕНИЕ: Используем &lt; и &gt; вместо < и >, чтобы не ломать HTML-разметку
        await message.answer(
            "⚠️ <b>Ошибка формата.</b>\n"
            "Используйте: <code>/setpercent &lt;число&gt;</code>\n"
            "Пример: <code>/setpercent 10</code> или <code>/setpercent 12.5</code>"
        )


@dp.message(Command("broadcast"), IsAdminFilter())
async def cmd_broadcast(message: Message):
    """
    Рассылка сообщения всем верифицированным партнерам.
    Использование: /broadcast Текст сообщения
    """
    # 1. Отделяем команду от текста
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "⚠️ <b>Ошибка использования.</b>\n"
            "Введите текст сообщения после команды.\n"
            "Пример: <code>/broadcast Уважаемые партнеры, у нас новости!</code>"
        )
        return

    text_to_send = parts[1]

    # 2. Получаем список ID из базы
    # Шлем только 'verified', чтобы не беспокоить тех, кому отказали или кто еще ждет
    partner_ids = await db.get_all_partner_ids(status='verified')

    if not partner_ids:
        await message.answer("ℹ️ В базе нет верифицированных партнеров для рассылки.")
        return

    await message.answer(f"⏳ Начинаю рассылку на <b>{len(partner_ids)}</b> пользователей...")

    # 3. Рассылаем
    success_count = 0
    fail_count = 0

    for user_id in partner_ids:
        try:
            # Можно добавить небольшую задержку, если партнеров очень много (>1000)
            # await asyncio.sleep(0.05)
            await bot.send_message(user_id, text_to_send)
            success_count += 1
        except Exception:
            # Ошибка возникает, если пользователь заблокировал бота
            fail_count += 1

    # 4. Отчет
    await message.answer(
        f"✅ <b>Рассылка завершена.</b>\n\n"
        f"📨 Успешно отправлено: {success_count}\n"
        f"🚫 Не доставлено (бот заблокирован): {fail_count}"
    )
# =================================================================
# === ВЕБ-СЕРВЕР ==================================================
# =================================================================

async def handle_telegram_GET(request: web.Request):
    return web.Response(text="OK")


async def handle_telegram_POST(request: web.Request):
    try:
        data = await request.json()
        await dp.feed_webhook_update(bot, data)
        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Telegram webhook error: {e}")
        return web.Response(status=500, text="Server Error")


async def handle_bitrix_webhook(request: web.Request):
    try:
        data = dict(request.query)
        if data.get('secret') != config.BITRIX_INCOMING_SECRET:
            return web.Response(status=403, text="Forbidden")

        evt = data.get('event_type')
        status_text = data.get('STAGE_ID') or data.get('status')
        did = int(data.get('deal_id', 0))
        uid = int(data.get('user_id', 0))

        # --- 1. Верификация Партнера ---
        if evt == 'partner_verification' and uid:
            cur = await db.get_partner_status(uid)
            if cur != status_text:
                await process_partner_verification(0, uid, status_text)

        # --- 2. Обновление Клиента ---
        elif evt == 'client_deal_update':
            pid, cname = await db.get_partner_and_client_by_deal_id(did)
            if pid:
                # А. Получаем данные о сумме сделки
                ddata = await bitrix_api.get_deal(did)
                full_opportunity = float(ddata.get('OPPORTUNITY', 0)) if ddata else 0

                # Б. Получаем актуальный ПРОЦЕНТ из БД
                percent_str = await db.get_setting("payout_percent", "0")
                try:
                    percent_val = float(percent_str)
                except ValueError:
                    percent_val = 0.0

                # В. Считаем сумму выплаты
                # (Сумма * Процент / 100)
                partner_payout = full_opportunity * (percent_val / 100.0)

                # Г. Если стадия ОТКАЗ -> обнуляем выплату
                if status_text == config.BITRIX_CLIENT_STAGE_LOSE:
                    partner_payout = 0.0

                # Д. Обновляем статус и сумму в БД
                sname = get_client_stage_name(status_text)
                await db.update_client_status_and_payout(did, sname, partner_payout)

                # Е. Уведомления
                if status_text in NOTIFICATIONS_MAP:
                    action_type = NOTIFICATIONS_MAP[status_text]

                    if action_type == "win":
                        await bot.send_message(pid,
                                               f"✅ С клиентом <b>{escape(cname)}</b> заключен договор! Ваша выплата: {partner_payout:,.0f} руб.")

                    elif action_type == "lose":
                        await bot.send_message(pid, f"❌ Клиент <b>{escape(cname)}</b> отказ. Выплата отменена.")

                    elif action_type == "meeting":
                        await bot.send_message(pid, f"ℹ️ Встреча с клиентом <b>{escape(cname)}</b> назначена.")

        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Bitrix webhook error: {e}", exc_info=True)
        return web.Response(status=500)
async def on_startup(app):
    await db.init_db()
    await db.add_admin(config.SUPER_ADMIN_ID, "SUPER", "senior")
    if not await db.get_setting("partnership_info"): await db.set_setting("partnership_info", "Инфо...")
    if not await db.get_setting("welcome_text"): await db.set_setting("welcome_text", "Приветствие...")

    url = config.BASE_WEBHOOK_URL + config.TELEGRAM_WEBHOOK_PATH
    await bot.set_webhook(url=url, secret_token=config.BITRIX_INCOMING_SECRET)


async def on_shutdown(app):
    await bot.delete_webhook()


def main():
    app.router.add_get(config.TELEGRAM_WEBHOOK_PATH, handle_telegram_GET)
    app.router.add_post(config.TELEGRAM_WEBHOOK_PATH, handle_telegram_POST)
    app.router.add_post(config.BITRIX_WEBHOOK_PATH, handle_bitrix_webhook)
    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)
    web.run_app(app, host=config.WEB_SERVER_HOST, port=config.WEB_SERVER_PORT)


if __name__ == "__main__":
    main()