# bitrix_api.py
import aiohttp
import traceback
from config import (
    BITRIX_PARTNER_WEBHOOK, BITRIX_CLIENT_WEBHOOK,
    PARTNER_FUNNEL_ID, PARTNER_DEAL_TG_ID_FIELD, PARTNER_DEAL_TG_USERNAME_FIELD,
    BITRIX_CLIENT_FUNNEL_ID, PARTNER_DEAL_FIELD,
    PARTNER_ROLE_FIELD, CLIENT_AREA_FIELD, CLIENT_ADDRESS_DEAL_FIELD,BITRIX_CLIENT_STAGE_1
)


async def check_contact_exists_by_phone(phone: str):
    """
    Проверяет, есть ли контакт с таким телефоном в базе CRM.
    Возвращает ID контакта или None.
    """
    url = BITRIX_CLIENT_WEBHOOK + "crm.contact.list.json"
    # Ищем контакт, у которого телефон совпадает
    params = {
        'filter': {'PHONE': phone},
        'select': ['ID', 'NAME', 'LAST_NAME']
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=params) as response:
                result = await response.json()
                if 'result' in result and len(result['result']) > 0:
                    # Контакт найден
                    contact = result['result'][0]
                    return contact['ID']
                return None
    except Exception as e:
        print(f"Error checking contact: {e}")
        return None


async def create_partner_deal(full_name: str, phone: str, user_id: int, username: str = None, role: str = None):
    """Создает сделку партнера (верификация)."""
    url_deal_add = BITRIX_PARTNER_WEBHOOK + "crm.deal.add.json"
    url_contact_add = BITRIX_PARTNER_WEBHOOK + "crm.contact.add.json"

    deal_title = f"Новый партнер (бот): {full_name}"
    deal_fields = {
        'TITLE': deal_title,
        'CATEGORY_ID': PARTNER_FUNNEL_ID,
        'SOURCE_ID': 'PARTNER_BOT',
    }

    if PARTNER_DEAL_TG_ID_FIELD:
        deal_fields[PARTNER_DEAL_TG_ID_FIELD] = user_id
    if PARTNER_DEAL_TG_USERNAME_FIELD and username:
        deal_fields[PARTNER_DEAL_TG_USERNAME_FIELD] = f"@{username}"
    # Передаем Роль
    if PARTNER_ROLE_FIELD and role:
        deal_fields[PARTNER_ROLE_FIELD] = role

    contact_params = {
        'fields': {
            'NAME': full_name,
            'PHONE': [{'VALUE': phone, 'VALUE_TYPE': 'WORK'}]
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url_contact_add, json=contact_params) as contact_response:
                contact_id = (await contact_response.json()).get('result')

            if contact_id:
                deal_fields['CONTACT_ID'] = contact_id

            async with session.post(url_deal_add, json={'fields': deal_fields}) as deal_response:
                deal_id = (await deal_response.json()).get('result')
                return deal_id

    except Exception as e:
        print(f"Error creating partner deal: {e}")
        return None


async def create_client_deal(client_name: str, client_phone: str, client_address: str, partner_name: str,
                             client_comment: str = None, client_area: str = None):
    """Создает сделку клиента (лид от партнера)."""
    url_deal_add = BITRIX_CLIENT_WEBHOOK + "crm.deal.add.json"
    url_contact_add = BITRIX_CLIENT_WEBHOOK + "crm.contact.add.json"

    deal_title = f"Заявка от партнера {partner_name} (Клиент: {client_name})"

    deal_fields = {
        'TITLE': deal_title,
        'SOURCE_ID': 'UC_Y0AEV3',
        'utm_source': 'ref',
        'SOURCE_DESCRIPTION': f"Партнер {partner_name}",
        'CATEGORY_ID': BITRIX_CLIENT_FUNNEL_ID,
        PARTNER_DEAL_FIELD: partner_name,
        'STAGE_ID': BITRIX_CLIENT_STAGE_1
    }

    if client_comment:
        deal_fields['COMMENTS'] = client_comment

    # Передаем площадь
    if CLIENT_AREA_FIELD and client_area:
        deal_fields[CLIENT_AREA_FIELD] = client_area

    # Передаем адрес в поле сделки
    if CLIENT_ADDRESS_DEAL_FIELD and client_address:
        deal_fields[CLIENT_ADDRESS_DEAL_FIELD] = client_address

    contact_params = {
        'fields': {
            'NAME': client_name,
            'PHONE': [{'VALUE': client_phone, 'VALUE_TYPE': 'WORK'}],
            'ADDRESS': client_address
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url_contact_add, json=contact_params) as contact_response:
                contact_id = (await contact_response.json()).get('result')

            if contact_id:
                deal_fields['CONTACT_ID'] = contact_id

            async with session.post(url_deal_add, json={'fields': deal_fields}) as deal_response:
                deal_id = (await deal_response.json()).get('result')
                return deal_id

    except Exception as e:
        print(f"Error creating client deal: {e}")
        return None


async def create_duplicate_alert_deal(client_name: str, client_phone: str, partner_name: str):
    """
    Создает сделку в ВОРОНКЕ ПАРТНЕРОВ для менеджера,
    если найден дубль клиента.
    """
    url_deal_add = BITRIX_PARTNER_WEBHOOK + "crm.deal.add.json"

    deal_title = f"ДУБЛЬ КЛИЕНТА от {partner_name}"
    description = (
        f"Партнер {partner_name} пытался передать клиента, который уже есть в базе.\n"
        f"Клиент: {client_name}\n"
        f"Телефон: {client_phone}\n\n"
        "Свяжитесь с партнером и проясните ситуацию."
    )

    deal_fields = {
        'TITLE': deal_title,
        'CATEGORY_ID': BITRIX_CLIENT_FUNNEL_ID,  # Воронка партнеров (11)
        'COMMENTS': description,
        'SOURCE_ID': 'PARTNER_BOT',
        'STAGE_ID': BITRIX_CLIENT_STAGE_1
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url_deal_add, json={'fields': deal_fields}) as response:
                return (await response.json()).get('result')
    except Exception as e:
        print(f"Error creating duplicate alert: {e}")
        return None


async def get_deal(deal_id: int):
    """Получает данные о сделке (чтобы узнать актуальную сумму)."""
    url = BITRIX_CLIENT_WEBHOOK + "crm.deal.get.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={'id': deal_id}) as response:
                data = await response.json()
                if 'result' in data:
                    return data['result']
                return None
    except Exception as e:
        print(f"Error getting deal: {e}")
        return None


async def move_deal_stage(deal_id: int, stage_id: str):
    # (Оставляем как было)
    url_deal_update = BITRIX_PARTNER_WEBHOOK + "crm.deal.update.json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url_deal_update,
                                    json={'id': deal_id, 'fields': {'STAGE_ID': stage_id}}) as response:
                return 'result' in (await response.json())
    except Exception:
        return False