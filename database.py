# database.py
import aiosqlite
import logging

DB_NAME = 'data/partners.db'


async def init_db():
    """Инициализирует базу данных и обновляет структуру при необходимости."""
    async with aiosqlite.connect(DB_NAME) as db:
        # Таблица партнеров (создаем, если нет)
        await db.execute('''
                CREATE TABLE IF NOT EXISTS partners (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    phone_number TEXT,
                    status TEXT DEFAULT 'pending', 
                    bitrix_deal_id INTEGER,
                    role TEXT
                )
            ''')

        # Таблица клиентов
        await db.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                client_id INTEGER PRIMARY KEY AUTOINCREMENT,
                partner_user_id INTEGER,
                bitrix_deal_id INTEGER,
                client_name TEXT,
                client_address TEXT,
                status TEXT DEFAULT 'new',
                payout_amount REAL DEFAULT 0,
                FOREIGN KEY (partner_user_id) REFERENCES partners (user_id)
            )
        ''')

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

    # Запускаем миграцию (добавляем колонки, если их нет в старой базе)
    await _migrate_db()


async def _migrate_db():
    """Безопасно добавляет новые колонки в существующие таблицы."""
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Добавляем поле role в partners
        try:
            await db.execute("ALTER TABLE partners ADD COLUMN role TEXT")
            logging.info("MIGRATION: Added 'role' column to partners table.")
        except Exception:
            pass  # Колонка уже есть

        # 2. Добавляем поле client_address в clients
        try:
            await db.execute("ALTER TABLE clients ADD COLUMN client_address TEXT")
            logging.info("MIGRATION: Added 'client_address' column to clients table.")
        except Exception:
            pass

        # 3. Добавляем поле payout_amount в clients
        try:
            await db.execute("ALTER TABLE clients ADD COLUMN payout_amount REAL DEFAULT 0")
            logging.info("MIGRATION: Added 'payout_amount' column to clients table.")
        except Exception:
            pass

        await db.commit()


# --- Партнеры ---

async def add_partner(user_id: int, full_name: str, phone_number: str, bitrix_deal_id: int, role: str):
    """Добавляет партнера с ролью. Исправлена ошибка аргументов."""
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO partners (user_id, full_name, phone_number, status, bitrix_deal_id, role) VALUES (?, ?, ?, 'pending', ?, ?)",
            (user_id, full_name, phone_number, bitrix_deal_id, role)
        )
        await db.commit()


async def get_partner_status(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT status FROM partners WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def get_partner_data(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        # Выбираем роль. Если её нет (старая запись), вернется None
        async with db.execute("SELECT full_name, phone_number, role FROM partners WHERE user_id = ?",
                              (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                # row[2] - это роль. Если колонка есть, но пустая -> None
                return {"full_name": row[0], "phone_number": row[1], "role": row[2] if len(row) > 2 else None}
            return None


async def set_partner_status(user_id: int, status: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE partners SET status = ? WHERE user_id = ?", (status, user_id))
        await db.commit()


async def get_partner_deal_id_by_user_id(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT bitrix_deal_id FROM partners WHERE user_id = ?"
        async with db.execute(query, (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


# --- Клиенты ---

async def add_client(partner_user_id: int, bitrix_deal_id: int, client_name: str, client_address: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT INTO clients (partner_user_id, bitrix_deal_id, client_name, client_address, status) VALUES (?, ?, ?, ?, 'new')",
            (partner_user_id, bitrix_deal_id, client_name, client_address)
        )
        await db.commit()


async def get_partner_and_client_by_deal_id(bitrix_deal_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT partner_user_id, client_name FROM clients WHERE bitrix_deal_id = ?"
        async with db.execute(query, (bitrix_deal_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return row[0], row[1]
            else:
                return None, None


async def update_client_status_and_payout(bitrix_deal_id: int, new_status_name: str, payout: float = 0):
    """Обновляет статус и сумму выплаты."""
    async with aiosqlite.connect(DB_NAME) as db:
        if payout > 0:
            query = "UPDATE clients SET status = ?, payout_amount = ? WHERE bitrix_deal_id = ?"
            await db.execute(query, (new_status_name, payout, bitrix_deal_id))
        else:
            query = "UPDATE clients SET status = ? WHERE bitrix_deal_id = ?"
            await db.execute(query, (new_status_name, bitrix_deal_id))

        await db.commit()


async def get_clients_by_partner_id(partner_user_id: int, limit: int = 5, offset: int = 0):
    async with aiosqlite.connect(DB_NAME) as db:
        query = """
            SELECT client_name, status, client_address 
            FROM clients 
            WHERE partner_user_id = ? 
            ORDER BY client_id DESC 
            LIMIT ? OFFSET ? 
        """
        async with db.execute(query, (partner_user_id, limit, offset)) as cursor:
            return await cursor.fetchall()


async def count_clients_by_partner_id(partner_user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        query = "SELECT COUNT(*) FROM clients WHERE partner_user_id = ?"
        async with db.execute(query, (partner_user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_partner_statistics(partner_user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        # 1. Общее количество
        async with db.execute("SELECT COUNT(*) FROM clients WHERE partner_user_id=?", (partner_user_id,)) as cur:
            total = (await cur.fetchone())[0]

        # 2. Сумма выплат
        async with db.execute("SELECT SUM(payout_amount) FROM clients WHERE partner_user_id=?",
                              (partner_user_id,)) as cur:
            res = await cur.fetchone()
            total_payout = res[0] if res and res[0] else 0.0

        return {
            "total_clients": total,
            "total_payout": total_payout
        }


# --- Админы и Настройки ---
async def add_admin(user_id: int, username: str = "", role: str = 'junior'):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO admins (user_id, username, role) VALUES (?, ?, ?)",
                         (user_id, username, role))
        await db.commit()


async def list_admins():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id, username, role FROM admins") as cursor:
            return await cursor.fetchall()


async def get_admin_role(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT role FROM admins WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None


async def remove_admin(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_all_admin_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM admins") as cursor:
            return [row[0] for row in await cursor.fetchall()]


async def get_junior_admin_ids():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT user_id FROM admins WHERE role = 'junior'") as cursor:
            return [row[0] for row in await cursor.fetchall()]


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else default


async def set_setting(key: str, value: str):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        await db.commit()