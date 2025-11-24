# keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import math

# --- Регистрация ---

def get_agree_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я согласен с условиями", callback_data="agree_to_terms")]
    ])

def get_role_keyboard():
    """Клавиатура для выбора роли партнера."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="Риэлтор"), KeyboardButton(text="Дизайнер")],
            [KeyboardButton(text="Приемщик"), KeyboardButton(text="Другое")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_request_phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

# --- Меню партнера ---

def get_verified_partner_menu():
    """Главное меню с кнопкой статистики."""
    keyboard = [
        [KeyboardButton(text="🚀 Отправить клиента")],
        [KeyboardButton(text="📊 Мои клиенты"), KeyboardButton(text="📈 Статистика")], # <-- НОВОЕ
        [KeyboardButton(text="ℹ️ Инфо Программа")]
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

# --- FSM / Служебные ---

def get_cancel_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Отмена")]],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_skip_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➡️ Пропустить")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_client_confirmation_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_client_submission"),
            InlineKeyboardButton(text="🔄 Заполнить заново", callback_data="retry_client_submission")
        ]
    ])

def get_verification_keyboard(partner_user_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"verify_partner:{partner_user_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_partner:{partner_user_id}")
        ]
    ])

# --- Пагинация ---
CLIENTS_PER_PAGE = 5

def get_clients_pagination_keyboard(current_offset: int, total_clients: int):
    if total_clients <= CLIENTS_PER_PAGE:
        return None
    current_page = current_offset // CLIENTS_PER_PAGE + 1
    total_pages = math.ceil(total_clients / CLIENTS_PER_PAGE)
    buttons = []
    if current_offset > 0:
        prev_offset = max(0, current_offset - CLIENTS_PER_PAGE)
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prev_clients:{prev_offset}"))
    buttons.append(InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop"))
    if current_offset + CLIENTS_PER_PAGE < total_clients:
        next_offset = current_offset + CLIENTS_PER_PAGE
        buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"next_clients:{next_offset}"))
    return InlineKeyboardMarkup(inline_keyboard=[buttons])