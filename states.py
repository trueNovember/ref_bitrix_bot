# states.py
from aiogram.fsm.state import State, StatesGroup

class PartnerRegistration(StatesGroup):
    waiting_for_full_name = State()
    waiting_for_role = State()      # <-- НОВОЕ: Выбор роли
    waiting_for_phone = State()

class ClientSubmission(StatesGroup):
    waiting_for_client_name = State()
    waiting_for_client_phone = State()
    waiting_for_client_address = State()
    waiting_for_client_area = State() # <-- НОВОЕ: Площадь
    waiting_for_client_comment = State()
    confirming_data = State()