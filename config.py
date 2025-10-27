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
    BITRIX_PARTNER_VERIFIED_STAGE_ID  # <-- ДОБАВЬТЕ ЭТУ СТРОКУ
]):
    raise ValueError("Необходимо заполнить все *обязательные* переменные BITRIX_* в .env файле")

if not PARTNER_DEAL_TG_ID_FIELD:
    print("ВНИМАНИЕ: PARTNER_DEAL_TG_ID_FIELD не указан в .env.")
    print("           Менеджеру придется искать ID партнера вручную для /verify.")

# --- 3. Администраторы ---
admin_ids_str = os.getenv("ADMIN_IDS", "")
ADMIN_IDS = [
    int(admin_id) for admin_id in admin_ids_str.split(",") if admin_id.strip().isdigit()
]
if not ADMIN_IDS:
    print("ВНИМАНИЕ: ADMIN_IDS не указаны в .env. Админ-команды не будут работать.")

# --- 4. Веб-сервер ---
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")
if not BASE_WEBHOOK_URL:
    raise ValueError("Необходимо указать BASE_WEBHOOK_URL в .env файле")

# --- 5. Генерируемые (не секретные) настройки ---
TELEGRAM_WEBHOOK_PATH = f"/webhook/telegram/{BOT_TOKEN[-10:]}"
BITRIX_WEBHOOK_PATH = f"/webhook/bitrix/{BITRIX_INCOMING_SECRET[:10]}"
WEB_SERVER_HOST = "0.0.0.0"
WEB_SERVER_PORT = int(os.getenv("WEB_SERVER_PORT", 8080))