# states.py
from aiogram.fsm.state import State, StatesGroup

class PartnerRegistration(StatesGroup):
    """
    Состояния для процесса регистрации нового партнера.
    """
    waiting_for_full_name = State() # Ожидание ввода ФИО
    waiting_for_phone = State()     # Ожидание нажатия кнопки "Поделиться номером"

class ClientSubmission(StatesGroup):
    """
    Состояния для процесса отправки нового клиента.
    """
    waiting_for_client_name = State()   # Ожидание ФИО клиента
    waiting_for_client_phone = State()  # Ожидание телефона клиента
    waiting_for_client_address = State() # Ожидание адреса клиента