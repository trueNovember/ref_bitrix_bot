# keyboards.py
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
import math

# --- Клавиатуры для процесса регистрации ---

def get_agree_keyboard():
    """
    Возвращает Inline-кнопку 'Я согласен' для новых пользователей.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Я согласен с условиями", callback_data="agree_to_terms")]
    ])

def get_request_phone_keyboard():
    """
    Возвращает Reply-кнопку для запроса номера телефона (request_contact).
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]
        ],
        resize_keyboard=True, # Делаем кнопки компактными
        one_time_keyboard=True # Скрываем клавиатуру после нажатия
    )

# --- Клавиатуры для верифицированного партнера ---

def get_verified_partner_menu():
    """
    Возвращает главное меню для партнера, прошедшего верификацию.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚀 Отправить клиента")],
            [KeyboardButton(text="📊 Мои клиенты")]
        ],
        resize_keyboard=True
    )

# --- Общая клавиатура для FSM ---

def get_cancel_keyboard():
    """
    Возвращает кнопку 'Отмена' для выхода из FSM-состояний.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_verification_keyboard(partner_user_id: int):
    """
    Клавиатура для админов для верификации нового партнера.
    Мы "зашиваем" ID партнера прямо в callback_data.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Одобрить",
                # "verify_partner:123456789"
                callback_data=f"verify_partner:{partner_user_id}"
            ),
            InlineKeyboardButton(
                text="❌ Отклонить",
                # "reject_partner:123456789"
                callback_data=f"reject_partner:{partner_user_id}"
            )
        ]
    ])

def get_client_confirmation_keyboard():
    """
    Кнопки "Подтвердить" / "Заполнить заново" для отправки клиента.
    """
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_client_submission"),
            InlineKeyboardButton(text="🔄 Заполнить заново", callback_data="retry_client_submission")
        ]
    ])


CLIENTS_PER_PAGE = 5  # Сколько клиентов показывать на странице


def get_clients_pagination_keyboard(current_offset: int, total_clients: int):
    """
    Создает инлайн-клавиатуру для пагинации списка клиентов.
    """
    if total_clients <= CLIENTS_PER_PAGE:
        # Если клиентов мало, кнопки не нужны
        return None

    # Рассчитываем пагинацию
    current_page = current_offset // CLIENTS_PER_PAGE + 1
    total_pages = math.ceil(total_clients / CLIENTS_PER_PAGE)

    buttons = []

    # Кнопка "Назад"
    if current_offset > 0:
        prev_offset = max(0, current_offset - CLIENTS_PER_PAGE)
        buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"prev_clients:{prev_offset}")
        )

    # Кнопка с номером страницы
    buttons.append(
        InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop")  # noop = no operation
    )

    # Кнопка "Вперед"
    if current_offset + CLIENTS_PER_PAGE < total_clients:
        next_offset = current_offset + CLIENTS_PER_PAGE
        buttons.append(
            InlineKeyboardButton(text="Вперед ➡️", callback_data=f"next_clients:{next_offset}")
        )

    return InlineKeyboardMarkup(inline_keyboard=[buttons])


def get_verified_partner_menu():
    """
    Возвращает главное меню для верифицированного партнера.
    """
    keyboard = [
        [KeyboardButton(text="🚀 Отправить клиента")],
        [KeyboardButton(text="📊 Мои клиенты")],
        # === КНОПКА ТЕПЕРЬ ДЛЯ ВСЕХ ===
        [KeyboardButton(text="ℹ️ Инфо Программа")]
        # ==============================
    ]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)

def get_skip_keyboard():
    """
    Возвращает кнопки 'Пропустить' и 'Отмена'.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➡️ Пропустить")],
            [KeyboardButton(text="❌ Отмена")]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )