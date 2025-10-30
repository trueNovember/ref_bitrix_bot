# database.py
import aiosqlite

DB_NAME = 'partners.db'


async def init_db():
    """Инициализирует базу данных и создает таблицы."""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица партнеров
        await db.execute('''
                CREATE TABLE IF NOT EXISTS partners (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    phone_number TEXT,
                    status TEXT DEFAULT 'pending', 
                    bitrix_deal_id INTEGER    
                )
            ''')

        # Таблица для клиентов, отправленных партнерами
        await db.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                client_id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_user_id INTEGER,  -- Telegram ID партнера
                bitrix_deal_id INTEGER,   -- ID сделки из Битрикса
                client_name TEXT,
                status TEXT DEFAULT 'new',    -- Статус сделки (будем обновлять)
                FOREIGN KEY (partner_user_id) REFERENCES partners (user_id)
            )
        ''')

        # Индекс для быстрого поиска по ID сделки из Битрикса
        await db.execute('''
            CREATE INDEX IF NOT EXISTS idx_bitrix_deal_id
            ON clients (bitrix_deal_id)
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                role TEXT NOT NULL CHECK(role IN ('junior', 'senior'))
            )
        ''')
        await db.execute('''
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                ''')
        await db.commit()


# --- Функции для Партнеров ---

async def add_partner(user_id: int, full_name: str, phone_number: str, bitrix_deal_id: int):
    """Добавляет нового партнера в статусе 'pending'."""
    async with aiosqlite.connect(DB_NAME) as db:  # <-- ИСПРАВЛЕНО
        await db.execute(
            "INSERT INTO partners (user_id, full_name, phone_number, status, bitrix_deal_id) VALUES (?, ?, ?, 'pending', ?)",
            (user_id, full_name, phone_number, bitrix_deal_id)
        )
        await db.commit()


async def get_partner_status(user_id: int):
    """Возвращает статус партнера или None, если партнер не найден."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT status FROM partners WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_partner_data(user_id: int):
    """Возвращает ФИО и телефон партнера."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT full_name, phone_number FROM partners WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return {"full_name": row[0], "phone_number": row[1]} if row else None


async def set_partner_status(user_id: int, status: str):
    """Обновляет статус партнера (для верификации)."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE partners SET status = ? WHERE user_id = ?", (status, user_id))
        await db.commit()


# --- Функции для Клиентов (Связка Партнер <-> Сделка) ---

async def add_client(partner_user_id: int, bitrix_deal_id: int, client_name: str):
    """Сохраняет связку 'Партнер <-> ID сделки в Битрикс'."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO clients (partner_user_id, bitrix_deal_id, client_name, status) VALUES (?, ?, ?, 'new')",
            (partner_user_id, bitrix_deal_id, client_name)
        )
        await db.commit()


async def get_partner_and_client_by_deal_id(bitrix_deal_id: int):
    """
    Находит ID партнера и ИМЯ КЛИЕНТА по ID сделки в Битрикс.
    Возвращает кортеж (partner_user_id, client_name) или (None, None).
    """
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT partner_user_id, client_name FROM clients WHERE bitrix_deal_id = ?"
        async with db.execute(query, (bitrix_deal_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1] # partner_user_id, client_name
            else:
                return None, None


async def update_client_status_by_deal_id(bitrix_deal_id: int, new_status_name: str):
    """Обновляет статус сделки (полученный от Битрикса) в нашей БД."""
    async with aiosqlite.connect(DB_NAME) as db:
        query = "UPDATE clients SET status = ? WHERE bitrix_deal_id = ?"
        await db.execute(query, (new_status_name, bitrix_deal_id))
        await db.commit()


async def get_clients_by_partner_id(partner_user_id: int, limit: int = 5, offset: int = 0):
    """
    Получает список клиентов для конкретного партнера с пагинацией.
    Сортирует по ID (новые сверху).
    """
    async with aiosqlite.connect(DB_NAME) as db:
        # Сортируем по client_id DESC, чтобы новые были первыми
        query = """
            SELECT client_name, status 
            FROM clients 
            WHERE partner_user_id = ? 
            ORDER BY client_id DESC 
            LIMIT ? OFFSET ? 
        """
        async with db.execute(query, (partner_user_id, limit, offset)) as cursor:
            rows = await cursor.fetchall()
            return rows # Будет список кортежей [('Клиент 1', 'Статус 1'), ...]

async def get_partner_deal_id_by_user_id(user_id: int):
    """Находит ID Сделки-Партнера по его Telegram ID."""
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT bitrix_deal_id FROM partners WHERE user_id = ?"
        async with db.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def add_admin(user_id: int, username: str = "", role: str = 'junior'):
    """Добавляет или обновляет админа с указанной ролью."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR REPLACE INTO admins (user_id, username, role) VALUES (?, ?, ?)",
            (user_id, username, role)
        )
        await db.commit()

async def list_admins():
    """Возвращает список всех админов (id, username, role)."""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, role FROM admins") as cursor:
            return await cursor.fetchall()

async def get_admin_role(user_id: int):
    """Возвращает роль админа ('junior', 'senior') или None."""
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT role FROM admins WHERE user_id = ?"
        async with db.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def remove_admin(user_id: int):
    """Удаляет администратора."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()

async def get_all_admin_ids():
    """Возвращает список ID всех админов (junior и senior)."""
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT user_id FROM admins"
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            # Превращаем список кортежей [(123,), (456,)] в список [123, 456]
            return [row[0] for row in rows]

async def get_junior_admin_ids():
    """Возвращает список ID только JUNIOR админов."""
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT user_id FROM admins WHERE role = 'junior'"
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def count_clients_by_partner_id(partner_user_id: int):
    """Подсчитывает общее количество клиентов для партнера."""
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT COUNT(*) FROM clients WHERE partner_user_id = ?"
        async with db.execute(query, (partner_user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_setting(key: str, default: str = "") -> str:
    """Получает значение настройки по ключу."""
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT value FROM settings WHERE key = ?"
        async with db.execute(query, (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default

async def set_setting(key: str, value: str):
    """Устанавливает (или обновляет) значение настройки."""
    async with aiosqlite.connect(DB_NAME) as db:
        # INSERT OR REPLACE обновит запись, если ключ уже существует
        await db.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value)
        )
        await db.commit()