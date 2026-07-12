import os
import aiosqlite

# Allow DB_PATH to be overridden via environment variable so the database
# can be stored on a persistent volume (Railway, justrunmy.app, etc.).
# Set DB_PATH=/data/bot.db in your hosting env and mount a volume at /data.
DB_PATH = os.getenv(
    "DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db"),
)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS economy (
                user_id  INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                balance  INTEGER NOT NULL DEFAULT 0,
                last_daily TEXT,
                PRIMARY KEY (user_id, guild_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS event_tickets (
                event_id TEXT NOT NULL,
                user_id  INTEGER NOT NULL,
                guild_id INTEGER NOT NULL,
                PRIMARY KEY (event_id, user_id)
            )
        """)
        await db.commit()


async def get_balance(user_id: int, guild_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT balance FROM economy WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def set_balance(user_id: int, guild_id: int, amount: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO economy (user_id, guild_id, balance)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, guild_id) DO UPDATE SET balance = excluded.balance""",
            (user_id, guild_id, amount),
        )
        await db.commit()


async def add_balance(user_id: int, guild_id: int, delta: int) -> int:
    current = await get_balance(user_id, guild_id)
    new_bal = max(0, current + delta)
    await set_balance(user_id, guild_id, new_bal)
    return new_bal


async def get_last_daily(user_id: int, guild_id: int) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_daily FROM economy WHERE user_id = ? AND guild_id = ?",
            (user_id, guild_id),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_last_daily(user_id: int, guild_id: int, date_str: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO economy (user_id, guild_id, last_daily)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, guild_id) DO UPDATE SET last_daily = excluded.last_daily""",
            (user_id, guild_id, date_str),
        )
        await db.commit()


async def get_leaderboard(guild_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, balance FROM economy WHERE guild_id = ? ORDER BY balance DESC LIMIT ?",
            (guild_id, limit),
        ) as cur:
            return await cur.fetchall()
