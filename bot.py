# bot.py
import re
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, ReplyKeyboardRemove, Update
from aiogram.client.default import DefaultBotProperties
from html import escape
import math

import config
import database as db
import bitrix_api
from states import PartnerRegistration, ClientSubmission
import keyboards as kb

logging.basicConfig(level=logging.INFO)
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = web.Application()


# --- Вспомогательные ---
def get_client_stage_name(stage_id: str) -> str:
    stages_map = {
        config.BITRIX_CLIENT_STAGE_1: "Клиенты в обработке",
        config.BITRIX_CLIENT_STAGE_2: "С клиентом назначена встреча",
        config.BITRIX_CLIENT_STAGE_3: "Расчет сметы",
        config.BITRIX_CLIENT_STAGE_WIN: "С клиентом заключен договор",
        config.BITRIX_CLIENT_STAGE_LOSE: "Отказ клиента"
    }
    return stages_map.get(stage_id, stage_id)


# --- Start ---
@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    status = await db.get_partner_status(message.from_user.id)
    if status == 'verified':
        await message.answer("✅ Вы верифицированный партнер.", reply_markup=kb.get_verified_partner_menu())
    elif status == 'pending':
        await message.answer("⏳ Ваша заявка на рассмотрении.", reply_markup=ReplyKeyboardRemove())
    elif status == 'rejected':
        await message.answer("❌ Ваша заявка отклонена.", reply_markup=ReplyKeyboardRemove())
    else:
        welcome = await db.get_setting("welcome_text", "Здравствуйте!")
        await message.answer(welcome, reply_markup=kb.get_agree_keyboard())


@dp.message(F.text == "❌ Отмена")
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=ReplyKeyboardRemove())
    await cmd_start(message, state)


# ================= REGISTRATION =================

@dp.callback_query(F.data == "agree_to_terms")
async def process_agree(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Отлично! Давайте начнем регистрацию.")
    await callback.message.answer("Пожалуйста, выберите, кем вы являетесь:", reply_markup=kb.get_role_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_role)
    await callback.answer()


@dp.message(PartnerRegistration.waiting_for_role)
async def process_role(message: Message, state: FSMContext):
    if message.text not in ["Риэлтор", "Дизайнер", "Приемщик", "Другое"]:
        await message.answer("Пожалуйста, выберите вариант из меню.")
        return
    await state.update_data(role=message.text)

    await message.answer("Введите ваше ФИО:", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_full_name)


@dp.message(PartnerRegistration.waiting_for_full_name)
async def process_name(message: Message, state: FSMContext):
    await state.update_data(full_name=message.text)
    await message.answer("Теперь поделитесь номером телефона.", reply_markup=kb.get_request_phone_keyboard())
    await state.set_state(PartnerRegistration.waiting_for_phone)


@dp.message(PartnerRegistration.waiting_for_phone, F.contact)
async def process_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    user_id = message.from_user.id
    data = await state.get_data()
    full_name = data['full_name']
    role = data['role']
    username = message.from_user.username

    await state.clear()

    deal_id = await bitrix_api.create_partner_deal(full_name, phone, user_id, username, role)

    if deal_id:
        await db.add_partner(user_id, full_name, phone, deal_id, role)
        await message.answer("⏳ Заявка принята! Менеджер скоро свяжется с вами.", reply_markup=ReplyKeyboardRemove())

        notif_text = (f"🔔 <b>Новый партнер!</b>\n{escape(full_name)}\nRole: {role}\nTel: {phone}")
        keyboard = kb.get_verification_keyboard(user_id)
        for admin_id in await db.get_junior_admin_ids():
            try:
                await bot.send_message(admin_id, notif_text, reply_markup=keyboard)
            except:
                pass
    else:
        await message.answer("Ошибка при создании заявки. Попробуйте позже.")


# ================= CLIENT SUBMISSION =================

@dp.message(F.text == "🚀 Отправить клиента")
async def start_client_submission(message: Message, state: FSMContext):
    if await db.get_partner_status(message.from_user.id) != 'verified':
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
    raw_phone = message.text
    cleaned = re.sub(r'\D', '', raw_phone)
    if cleaned.startswith('8') and len(cleaned) == 11:
        cleaned = '7' + cleaned[1:]
    elif len(cleaned) == 10:
        cleaned = '7' + cleaned

    if not (len(cleaned) == 11 and cleaned.startswith('7')):
        await message.answer("❌ Неверный формат. Введите номер (РФ) корректно.")
        return

    formatted_phone = '+' + cleaned

    contact_id = await bitrix_api.check_contact_exists_by_phone(formatted_phone)

    if contact_id:
        partner_data = await db.get_partner_data(message.from_user.id)
        c_name = (await state.get_data()).get('client_name')

        await bitrix_api.create_duplicate_alert_deal(c_name, formatted_phone, partner_data['full_name'])

        await message.answer(
            f"ℹ️ Клиент с номером {formatted_phone} уже есть в нашей базе.\n"
            "Мы свяжемся с вами для уточнения деталей.",
            reply_markup=kb.get_verified_partner_menu()
        )
        await state.clear()
        return

    await state.update_data(client_phone=formatted_phone)
    await message.answer("✅ Введите адрес квартиры (Улица, дом, кв...):", reply_markup=kb.get_cancel_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_address)


@dp.message(ClientSubmission.waiting_for_client_address)
async def client_address_received(message: Message, state: FSMContext):
    await state.update_data(client_address=message.text)
    await message.answer("Введите площадь квартиры (м2) или нажмите 'Пропустить':", reply_markup=kb.get_skip_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_area)


@dp.message(ClientSubmission.waiting_for_client_area)
async def client_area_received(message: Message, state: FSMContext):
    area_text = message.text
    if area_text == "➡️ Пропустить":
        area_text = None

    await state.update_data(client_area=area_text)
    await message.answer("Введите комментарий или нажмите 'Пропустить':", reply_markup=kb.get_skip_keyboard())
    await state.set_state(ClientSubmission.waiting_for_client_comment)


@dp.message(ClientSubmission.waiting_for_client_comment)
async def client_comment_received(message: Message, state: FSMContext):
    comment_text = message.text if message.text != "➡️ Пропустить" else None
    await state.update_data(client_comment=comment_text)

    data = await state.get_data()
    text = (
        f"<b>Проверьте данные:</b>\n\n"
        f"👤 <b>Имя:</b> {escape(data['client_name'])}\n"
        f"📞 <b>Тел:</b> {data['client_phone']}\n"
        f"🏠 <b>Адрес:</b> {escape(data['client_address'])}\n"
        f"📐 <b>Площадь:</b> {data['client_area'] or 'Не указана'}\n"
        f"💬 <b>Коммент:</b> {escape(comment_text or 'Нет')}\n\n"
        "Все верно?"
    )
    await message.answer(text, reply_markup=kb.get_client_confirmation_keyboard())
    await state.set_state(ClientSubmission.confirming_data)


@dp.callback_query(F.data == "confirm_client_submission", ClientSubmission.confirming_data)
async def confirm_submission(callback: CallbackQuery, state: FSMContext):
    p_id = callback.from_user.id
    d = await state.get_data()
    p_data = await db.get_partner_data(p_id)

    await callback.message.edit_text("⏳ Отправка...", reply_markup=None)

    deal_id = await bitrix_api.create_client_deal(
        d['client_name'], d['client_phone'], d['client_address'], p_data['full_name'],
        d['client_comment'], d['client_area']
    )

    if deal_id:
        await db.add_client(p_id, deal_id, d['client_name'], d['client_address'])
        await callback.message.answer("✅ Клиент успешно отправлен!", reply_markup=kb.get_verified_partner_menu())
    else:
        await callback.message.answer("Ошибка отправки.", reply_markup=kb.get_verified_partner_menu())
    await state.clear()
    await callback.answer()


# ================= STATISTICS & LISTS =================

@dp.message(F.text == "📈 Статистика")
async def show_statistics(message: Message):
    if await db.get_partner_status(message.from_user.id) != 'verified': return

    stats = await db.get_partner_statistics(message.from_user.id)
    text = (
        f"<b>📊 Ваша статистика:</b>\n\n"
        f"👥 <b>Отправлено клиентов:</b> {stats['total_clients']}\n"
        f"💰 <b>Сумма выплат (подтвержденная):</b> {stats['total_payout']:,.0f} руб.\n"
    )
    await message.answer(text)


@dp.message(F.text == "📊 Мои клиенты")
async def show_my_clients(message: Message, state: FSMContext, offset: int = 0):
    p_id = message.from_user.id
    if await db.get_partner_status(p_id) != 'verified': return
    await state.clear()

    total = await db.count_clients_by_partner_id(p_id)
    if total == 0:
        await message.answer("Список пуст.")
        return

    clients = await db.get_clients_by_partner_id(p_id, limit=kb.CLIENTS_PER_PAGE, offset=offset)

    text = f"<b>Ваши клиенты ({offset + 1}-{min(offset + kb.CLIENTS_PER_PAGE, total)} из {total}):</b>\n\n"
    for i, (name, status, address) in enumerate(clients, start=offset + 1):
        addr_str = f" ({address})" if address else ""
        text += f"{i}. <b>{escape(name)}</b>{escape(addr_str)}\n   Статус: <i>{escape(status)}</i>\n"

    keyboard = kb.get_clients_pagination_keyboard(offset, total)
    await message.answer(text, reply_markup=keyboard)


# ================= WEBHOOKS =================

async def handle_telegram_webhook(request: web.Request):
    """
    Обработчик вебхука Telegram. Читает JSON, создает объект Update и передает в Dispatcher.
    """
    try:
        # 1. Читаем JSON (обязательно await!)
        data = await request.json()

        # 2. Создаем объект Update (aiogram 3.x требует объект, а не dict, но feed_webhook_update умеет работать и с dict)
        # Однако лучше явно убедиться, что мы передаем то, что нужно.
        # Метод feed_webhook_update принимает (bot, update: dict | Update)
        await dp.feed_webhook_update(bot, data)

        return web.Response(text="OK")
    except Exception as e:
        logging.error(f"Error handling Telegram webhook: {e}")
        # Возвращаем 200, чтобы Telegram не спамил повторами при ошибке
        return web.Response(text="Error", status=200)


async def handle_bitrix_webhook(request: web.Request):
    data = request.query
    if data.get('secret') != config.BITRIX_INCOMING_SECRET:
        return web.Response(status=403, text="Forbidden")

    event_type = data.get('event_type')
    status_or_stage = data.get('status')
    deal_id = int(data.get('deal_id', 0))

    if event_type == 'partner_verification':
        user_id = int(data.get('user_id', 0))
        if user_id:
            current = await db.get_partner_status(user_id)
            if current != status_or_stage:
                await db.set_partner_status(user_id, status_or_stage)
                if status_or_stage == 'verified':
                    await bot.send_message(user_id, "✅ Вы верифицированы!", reply_markup=kb.get_verified_partner_menu())
                elif status_or_stage == 'rejected':
                    await bot.send_message(user_id, "❌ Заявка отклонена.", reply_markup=ReplyKeyboardRemove())

    elif event_type == 'client_deal_update':
        partner_id, client_name = await db.get_partner_and_client_by_deal_id(deal_id)
        if partner_id:
            deal_data = await bitrix_api.get_deal(deal_id)
            opportunity = 0.0
            if deal_data and 'OPPORTUNITY' in deal_data:
                try:
                    opportunity = float(deal_data['OPPORTUNITY'])
                except:
                    pass

            stage_name = get_client_stage_name(status_or_stage)
            await db.update_client_status_and_payout(deal_id, stage_name, opportunity)

            if status_or_stage == config.BITRIX_CLIENT_STAGE_WIN:
                await bot.send_message(partner_id,
                                       f"✅ С клиентом <b>{client_name}</b> заключен договор! Сумма: {opportunity}")
            elif status_or_stage == config.BITRIX_CLIENT_STAGE_LOSE:
                await bot.send_message(partner_id, f"❌ Клиент <b>{client_name}</b> перешел в статус Отказ.")
            elif status_or_stage == config.BITRIX_CLIENT_STAGE_2:
                await bot.send_message(partner_id, f"ℹ️ Назначена встреча с клиентом <b>{client_name}</b>.")

    return web.Response(text="OK")


async def on_startup(app):
    await db.init_db()
    webhook_url = config.BASE_WEBHOOK_URL + config.TELEGRAM_WEBHOOK_PATH
    await bot.set_webhook(url=webhook_url, secret_token=config.BITRIX_INCOMING_SECRET)


def main():
    # ИСПРАВЛЕНО: Используем именованную асинхронную функцию
    app.router.add_post(config.TELEGRAM_WEBHOOK_PATH, handle_telegram_webhook)
    app.router.add_post(config.BITRIX_WEBHOOK_PATH, handle_bitrix_webhook)
    app.on_startup.append(on_startup)
    web.run_app(app, host=config.WEB_SERVER_HOST, port=config.WEB_SERVER_PORT)


if __name__ == "__main__":
    main()