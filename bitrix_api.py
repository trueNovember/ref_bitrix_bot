# bitrix_api.py
import aiohttp
import traceback
from config import (
    BITRIX_PARTNER_WEBHOOK,
    BITRIX_CLIENT_WEBHOOK,
    PARTNER_DEAL_FIELD,
    PARTNER_FUNNEL_ID,  # ID воронки 11
    PARTNER_DEAL_TG_ID_FIELD,
    BITRIX_CLIENT_FUNNEL_ID,
    PARTNER_DEAL_FIELD
)


# (Опционально)
# from config import PARTNER_DEAL_TG_ID_FIELD

async def create_partner_deal(full_name: str, phone: str, user_id: int):
    """
    Отправляет Сделку 'Партнер на верификацию' в Битрикс (в воронку 11).
    Использует aiohttp.
    !!! ИСПРАВЛЕННАЯ ВЕРСИЯ: Возвращает deal_id, а не True !!!
    """
    url_deal_add = BITRIX_PARTNER_WEBHOOK + "crm.deal.add.json"
    url_contact_add = BITRIX_PARTNER_WEBHOOK + "crm.contact.add.json"

    deal_title = f"Новый партнер (бот): {full_name}"

    deal_fields = {
        'TITLE': deal_title,
        'CATEGORY_ID': PARTNER_FUNNEL_ID,  # Воронка 11
        'SOURCE_ID': 'PARTNER_BOT',
    }

    # Добавляем TG ID, если он указан в конфиге
    if PARTNER_DEAL_TG_ID_FIELD:
        deal_fields[PARTNER_DEAL_TG_ID_FIELD] = user_id

    contact_params = {
        'fields': {
            'NAME': full_name,
            'PHONE': [{'VALUE': phone, 'VALUE_TYPE': 'WORK'}]
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Создаем контакт
            async with session.post(url_contact_add, json=contact_params) as contact_response:
                contact_response.raise_for_status()  # Проверка на 4xx/5xx
                contact_data = await contact_response.json()
                contact_id = contact_data.get('result')

            # 2. Создаем сделку и привязываем контакт
            if contact_id:
                deal_fields['CONTACT_ID'] = contact_id

            deal_params = {'fields': deal_fields}

            async with session.post(url_deal_add, json=deal_params) as deal_response:
                deal_response.raise_for_status()
                deal_data = await deal_response.json()

                # === ВОТ ИСПРАВЛЕНИЕ ===
                deal_id = deal_data.get('result')
                if deal_id:
                    print(f"Partner deal created, ID: {deal_id}")
                    return deal_id  # <-- Возвращаем ID
                else:
                    print(f"Error: Partner deal created but no ID returned. {deal_data}")
                    return None  # <-- Возвращаем None
                # ========================

    except aiohttp.ClientResponseError as e:
        # Ошибка HTTP (4xx, 5xx)
        print(f"HTTP error creating partner deal: {e.status} - {e.message}")
        print(f"Response: {await e.text()}")
        return None  # <-- Исправлено на None
    except aiohttp.ClientConnectorError as e:
        # Ошибка соединения
        print(f"Connection error creating partner deal: {e}")
        return None  # <-- Исправлено на None
    except Exception as e:
        # Другие ошибки
        print("--- ПОЛНАЯ ОШИБКА В create_partner_deal ---")
        print(traceback.format_exc())
        print("------------------------------------------")
        print(f"Error creating partner deal: {e}")
        return None  # <-- Исправлено на None


async def create_client_deal(client_name: str, client_phone: str, client_address: str, partner_name: str):
    """
    Отправляет сделку 'Новый клиент' в Битрикс (в воронку КЛИЕНТОВ).
    Возвращает ID созданной сделки (deal_id) или None.
    """
    # Используем ВЕБХУК №2 (для клиентов)
    url_deal_add = BITRIX_CLIENT_WEBHOOK + "crm.deal.add.json"
    url_contact_add = BITRIX_CLIENT_WEBHOOK + "crm.contact.add.json"

    deal_title = f"Заявка от партнера {partner_name} (Клиент: {client_name})"

    deal_fields = {
        'TITLE': deal_title,
        'SOURCE_ID': 'PARTNER_BOT_LEAD',

        # === НОВЫЕ ПОЛЯ ===
        'CATEGORY_ID': BITRIX_CLIENT_FUNNEL_ID,  # Указываем нужную воронку
        PARTNER_DEAL_FIELD: partner_name,  # Поле, где хранится ФИО партнера
        'STAGE_ID': 'C11:UC_JVUM2G'
        # ==================
    }

    contact_params = {
        'fields': {
            'NAME': client_name,
            'PHONE': [{'VALUE': client_phone, 'VALUE_TYPE': 'WORK'}],
            'ADDRESS': client_address
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            # 1. Создаем контакт
            async with session.post(url_contact_add, json=contact_params) as contact_response:
                contact_response.raise_for_status()
                contact_data = await contact_response.json()
                contact_id = contact_data.get('result')

            # 2. Создаем сделку и привязываем контакт
            if contact_id:
                deal_fields['CONTACT_ID'] = contact_id

            deal_params = {'fields': deal_fields}

            async with session.post(url_deal_add, json=deal_params) as deal_response:
                deal_response.raise_for_status()
                deal_data = await deal_response.json()

                deal_id = deal_data.get('result')
                if deal_id:
                    print(f"Client deal created, ID: {deal_id}")
                    return deal_id  # Возвращаем ID
                else:
                    print(f"Error: Client deal created but no ID returned. {deal_data}")
                    return None


    except aiohttp.ClientResponseError as e:
        print(f"HTTP error creating client deal: {e.status} - {e.message}")
        # We removed the problematic line. The status and message are enough.
        return None
    except aiohttp.ClientConnectorError as e:
        print(f"Connection error creating client deal: {e}")
        return None
    except Exception as e:
        print("--- ПОЛНАЯ ОШИБКА В create_client_deal ---")
        print(traceback.format_exc())
        print("------------------------------------------")
        print(f"Error creating client deal: {e}")
        return None


async def move_deal_stage(deal_id: int, stage_id: str):
    """
    Передвигает сделку на новый этап (stage_id).
    Использует ВЕБХУК №1 (для партнеров), т.к. мы двигаем Сделку-Партнера.
    """
    url_deal_update = BITRIX_PARTNER_WEBHOOK + "crm.deal.update.json"

    params = {
        'id': deal_id,
        'fields': {
            'STAGE_ID': stage_id
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url_deal_update, json=params) as response:
                response.raise_for_status()
                result = await response.json()

                if 'result' in result:
                    print(f"Сделка {deal_id} успешно передвинута на этап {stage_id}.")
                    return True
                else:
                    print(f"Ошибка при обновлении сделки {deal_id}: {result}")
                    return False

    except Exception as e:
        print(f"Критическая ошибка в move_deal_stage: {e}")
        return False