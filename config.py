# config.py
import os
from dotenv import load_dotenv

# Загружаем переменные окружения из файла .env
load_dotenv()

# --- 1. Telegram ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("Необходимо указать BOT_TOKEN в .env файле")

# --- 2. Bitrix24 ---
BITRIX_PARTNER_WEBHOOK = os.getenv("BITRIX_PARTNER_WEBHOOK")
BITRIX_CLIENT_WEBHOOK = os.getenv("BITRIX_CLIENT_WEBHOOK")
PARTNER_DEAL_FIELD = os.getenv("PARTNER_DEAL_FIELD")
BITRIX_INCOMING_SECRET = os.getenv("BITRIX_INCOMING_SECRET")
BITRIX_PARTNER_VERIFIED_STAGE_ID = os.getenv("BITRIX_PARTNER_VERIFIED_STAGE_ID")
BITRIX_PARTNER_REJECTED_STAGE_ID = os.getenv("BITRIX_PARTNER_REJECTED_STAGE_ID")
# НОВАЯ ПЕРЕМЕННАЯ: ID воронки для партнеров
PARTNER_FUNNEL_ID = os.getenv("PARTNER_FUNNEL_ID")

PARTNER_DEAL_TG_ID_FIELD = os.getenv("PARTNER_DEAL_TG_ID_FIELD")

# Проверяем, что все поля Битрикса заполнены
if not all([
    BITRIX_PARTNER_WEBHOOK,
    BITRIX_CLIENT_WEBHOOK,
    PARTNER_DEAL_FIELD,
    BITRIX_INCOMING_SECRET,
    PARTNER_FUNNEL_ID,
    BITRIX_PARTNER_VERIFIED_STAGE_ID,
    BITRIX_PARTNER_REJECTED_STAGE_ID  # <-- Добавили сюда
]):
    raise ValueError("Необходимо заполнить все *обязательные* переменные BITRIX_* в .env файле")

if not PARTNER_DEAL_TG_ID_FIELD:
    print("ВНИМАНИЕ: PARTNER_DEAL_TG_ID_FIELD не указан в .env.")
    print("           Менеджеру придется искать ID партнера вручную для /verify.")

# --- 3. Администраторы ---
SUPER_ADMIN_ID = os.getenv("SUPER_ADMIN_ID")
if not SUPER_ADMIN_ID or not SUPER_ADMIN_ID.isdigit():
    raise ValueError("SUPER_ADMIN_ID (число) не указан в .env. Бот не может работать без владельца.")

# Конвертируем в число
SUPER_ADMIN_ID = int(SUPER_ADMIN_ID)

# --- 4. Веб-сервер ---
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")
if not BASE_WEBHOOK_URL:
    raise ValueError("Необходимо указать BASE_WEBHOOK_URL в .env файле")

# --- 5. Генерируемые (не секретные) настройки ---
TELEGRAM_WEBHOOK_PATH = f"/webhook/telegram/{BOT_TOKEN[-10:]}"
BITRIX_WEBHOOK_PATH = f"/webhook/bitrix/{BITRIX_INCOMING_SECRET}"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", 8080))