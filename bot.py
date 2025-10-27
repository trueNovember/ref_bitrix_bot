# bot.py
import asyncio
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

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)

# --- Инициализация ---
bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
app = web.Application()

# --- Тексты бота ---
WELCOME_TEXT = WELCOME_TEXT = """
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


# =================================================================
# === ОБРАБОТЧИКИ TELEGRAM (Логика FSM) ===========================
# =================================================================

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

    # === НОВАЯ ЛОГИКА ===
    # 1. Сначала отправляем в Битрикс, чтобы получить deal_id
    deal_id = await bitrix_api.create_partner_deal(full_name, phone_number, user_id)

    if deal_id:
        # 2. Если успешно, сохраняем в локальную БД (!!! ТЕПЕРЬ 4 АРГУМЕНТА !!!)
        await db.add_partner(user_id, full_name, phone_number, deal_id)
        await message.answer(PENDING_VERIFICATION_TEXT, reply_markup=ReplyKeyboardRemove())
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

@dp.message(Command("verify"), F.from_user.id.in_(config.ADMIN_IDS))
async def cmd_verify(message: Message):
    """
    Команда для верификации.
    Использование: /verify 123456789
    Теперь она также ОБНОВЛЯЕТ сделку в Битриксе.
    """
    try:
        user_id_to_verify = int(message.text.split()[1])

        # 1. Находим ID сделки, связанной с этим партнером
        deal_id = await db.get_partner_deal_id_by_user_id(user_id_to_verify)

        if not deal_id:
            await message.answer(
                f"❌ Ошибка: Партнер с ID {user_id_to_verify} найден в боте, но с ним не связана сделка в Битриксе.")
            return

        # 2. Обновляем статус в нашей БД
        await db.set_partner_status(user_id_to_verify, 'verified')

        # 3. Отправляем команду в Битрикс на передвижение сделки
        success = await bitrix_api.move_deal_stage(
            deal_id,
            config.BITRIX_PARTNER_VERIFIED_STAGE_ID  # Используем ID "успешного" этапа
        )

        if success:
            await message.answer(
                f"✅ Партнер {user_id_to_verify} верифицирован. Сделка {deal_id} в Битриксе передвинута.")
        else:
            await message.answer(
                f"⚠️ Партнер {user_id_to_verify} верифицирован в боте, но не удалось передвинуть сделку {deal_id} в Битриксе.")

        # 4. Уведомляем партнера
        try:
            await bot.send_message(
                user_id_to_verify,
                VERIFIED_TEXT,
                reply_markup=kb.get_verified_partner_menu()
            )
        except Exception as e:
            await message.answer(f"Не удалось уведомить партнера (возможно, он заблокировал бота): {e}")
    except Exception as e:

        # Мы экранируем и ошибку {e}, и наш <user_id>, чтобы все было безопасно

        error_text = escape(str(e))

        usage_text = "Использование: /verify &lt;user_id&gt;"

        await message.answer(f"Ошибка: {error_text}. {usage_text}")





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
    !!! ЭТОТ ОБРАБОТЧИК ЛОВИТ ЗАПРОСЫ ОТ БИТРИКС24 !!!
    Он проверяет секретный ключ и обновляет статусы в нашей БД.
    """
    try:
        # Битрикс шлет данные в 'application/x-www-form-urlencoded'
        data = await request.post()
        logging.info(f"Получен Bitrix-апдейт: {data}")

        # 1. Проверка безопасности
        auth_token = data.get('auth[application_token]')
        if auth_token != config.BITRIX_INCOMING_SECRET:
            logging.warning("!!! Неверный токен от Битрикс !!!")
            return web.Response(status=403, text="Forbidden")

        # 2. Проверяем тип события
        event = data.get('event')
        if event == 'ONCRMDEALUPDATE':
            deal_id = int(data.get('data[FIELDS][ID]', 0))
            new_stage_id = data.get('data[FIELDS][STAGE_ID]')  # e.g. "C1:WON"

            if not deal_id or not new_stage_id:
                logging.info("Недостаточно данных (нет ID или StageID) в апдейте.")
                return web.Response(text="OK (no data)")

            # 3. Находим, какому партнеру принадлежит эта сделка
            partner_id = await db.get_partner_id_by_deal_id(deal_id)

            # 4. Если сделка найдена в нашей БД (т.е. это Клиент, а не Партнер)
            if partner_id:
                logging.info(f"Обновляем сделку {deal_id} для партнера {partner_id}")

                # Обновляем статус в нашей локальной БД
                await db.update_client_status_by_deal_id(deal_id, new_stage_id)

                # Отправляем уведомление партнеру
                # (Можно сделать более красиво, сопоставив new_stage_id с названием)
                await bot.send_message(
                    partner_id,
                    f"ℹ️ Статус вашего клиента (сделка №{deal_id}) был обновлен до: {new_stage_id}"
                )
            else:
                logging.info(f"Сделка {deal_id} не найдена в БД партнеров (возможно, это сделка-партнер)")

        return web.Response(text="OK")

    except Exception as e:
        logging.error(f"Ошибка в обработчике Битрикс: {e}")
        return web.Response(status=500, text="Server Error")


# =================================================================
# === ЗАПУСК СЕРВЕРА ==============================================
# =================================================================

async def on_startup(app_instance: web.Application):
    """Выполняется при старте сервера."""
    await db.init_db()  # Инициализируем базу данных

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