import sqlite3
import os
from aiogram import Dispatcher, Bot, F
from aiogram.filters import CommandStart, Command, Filter
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from main import REPORTS_DB, USERS_DB
import datetime
import json


async def add_user(tg_id: int, username: str | None) -> bool:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT username FROM users WHERE tg_id = ?", (tg_id,))
        user = cur.fetchone()
        if not user:
            end_date = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("INSERT INTO users (tg_id, username, end_of_sub) VALUES(?, ?, ?)", (tg_id, username, end_date))
            return True
        if user[0] != username:
            cur.execute("UPDATE users SET username = ? WHERE tg_id = ?", (username, tg_id))
        return False

async def add_sub(tg_id: int, plan: int):
    plan_days = {1: 30, 3: 90, 6: 180, 12: 365}
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT end_of_sub FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        end_date = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S") if row and row[0] else None
        if not end_date or end_date < datetime.datetime.now():
            end_date = datetime.datetime.now()
        end_date += datetime.timedelta(days=plan_days[plan])
        cur.execute("UPDATE users SET end_of_sub = ? WHERE tg_id = ?", (end_date.strftime("%Y-%m-%d %H:%M:%S"), tg_id))

async def get_user_balance(tg_id: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT balance FROM users WHERE tg_id = ?", (tg_id,))
        balance = cur.fetchone()
        if balance[0] > 0:
            # print(balance[0])
            return int(balance[0])
        return 0

async def add_balance(tg_id: int, summ: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (summ, tg_id))

async def get_user_sub(tg_id: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT end_of_sub FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            return False, None
        end_date = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
        is_active = end_date > datetime.datetime.now()
        return is_active, end_date

async def get_users_count():
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT COUNT(*) FROM users")
        return cur.fetchone()[0]


async def get_stats(days: int = None) -> tuple:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        if days:
            since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            cur.execute("SELECT COUNT(*) FROM users WHERE date(join_date) >= ?", (since,))
        else:
            cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
    with sqlite3.connect(REPORTS_DB) as db:
        cur = db.cursor()
        if days:
            since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
            cur.execute("SELECT COALESCE(SUM(transactions),0), COALESCE(SUM(money),0) FROM reports_for_day WHERE date(date) >= ?", (since,))
        else:
            cur.execute("SELECT COALESCE(SUM(transactions),0), COALESCE(SUM(money),0) FROM reports_for_day")
        row = cur.fetchone()
        transactions = int(row[0]) if row else 0
        money = float(row[1]) if row else 0.0
    return users, transactions, money


async def get_total_users_balance() -> float:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT COALESCE(SUM(balance),0) FROM users")
        return float(cur.fetchone()[0])


async def get_total_ref_balance() -> float:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT COALESCE(SUM(ref_balance),0) FROM users")
        return float(cur.fetchone()[0])

async def add_report(money: int):
    today = datetime.date.today().isoformat()
    with sqlite3.connect(REPORTS_DB) as db:
        cur = db.cursor()
        cur.execute("INSERT OR IGNORE INTO reports_for_day (date, money, transactions) VALUES (?, 0, 0)", (today,))
        cur.execute("UPDATE reports_for_day SET money = money + ?, transactions = transactions + 1 WHERE date(date) = ?", (money, today))

async def get_report_for_days(days: int):
    with sqlite3.connect(REPORTS_DB) as db:
        cur = db.cursor()
        since = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        cur.execute('SELECT money, transactions, users FROM reports_for_day WHERE date(date) >= ?', (since,))
        reports = cur.fetchall()
        print(reports)
        return reports


async def get_ref_id(tg_id: int) -> int:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT ref_id FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else 0


async def set_ref_id(tg_id: int, ref_id: int):
    if tg_id == ref_id:
        return
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT ref_id FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if not row or row[0]:
            return
        cur.execute("SELECT tg_id FROM users WHERE tg_id = ?", (ref_id,))
        if cur.fetchone():
            cur.execute("UPDATE users SET ref_id = ? WHERE tg_id = ?", (ref_id, tg_id))


async def get_ref_info(tg_id: int) -> tuple[int, int, int]:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT ref_balance, ref_procent FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        ref_balance = int(row[0]) if row and row[0] else 0
        ref_procent = int(row[1]) if row and row[1] else 0
        cur.execute("SELECT COUNT(*) FROM users WHERE ref_id = ?", (tg_id,))
        ref_count = cur.fetchone()[0]
        return ref_balance, ref_count, ref_procent


async def add_ref_balance(tg_id: int, amount: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("UPDATE users SET ref_balance = ref_balance + ? WHERE tg_id = ?", (amount, tg_id))


async def transfer_ref_balance(tg_id: int) -> int:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT ref_balance FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if not row or not row[0] or int(row[0]) <= 0:
            return 0
        amount = int(row[0])
        cur.execute("UPDATE users SET balance = balance + ?, ref_balance = 0 WHERE tg_id = ?", (amount, tg_id))
        return amount


async def get_active_users():
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT tg_id, username, end_of_sub FROM users WHERE end_of_sub IS NOT NULL AND end_of_sub > datetime('now')")
        return cur.fetchall()


async def get_user_info(tg_id: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT tg_id, username, balance, ref_balance FROM users WHERE tg_id = ?", (tg_id,))
        return cur.fetchone()  # (tg_id, username, balance, ref_balance) или None


async def add_days_to_sub(tg_id: int, days: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT end_of_sub FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        end_date = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S") if row and row[0] else None
        if not end_date or end_date < datetime.datetime.now():
            end_date = datetime.datetime.now()
        end_date += datetime.timedelta(days=days)
        cur.execute("UPDATE users SET end_of_sub = ? WHERE tg_id = ?", (end_date.strftime("%Y-%m-%d %H:%M:%S"), tg_id))


async def deduct_ref_balance(tg_id: int, amount: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("UPDATE users SET ref_balance = ref_balance - ? WHERE tg_id = ?", (amount, tg_id))


def _init_devices_table():
    with sqlite3.connect(USERS_DB) as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS vpn_devices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                platform TEXT NOT NULL,
                label TEXT NOT NULL,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tg_id, platform)
            )
        """)


async def get_user_devices(tg_id: int) -> list:
    _init_devices_table()
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute(
            "SELECT id, platform, label, added_at FROM vpn_devices WHERE tg_id = ? ORDER BY added_at",
            (tg_id,),
        )
        return cur.fetchall()


async def upsert_device(tg_id: int, platform: str) -> None:
    _init_devices_table()
    labels = {'android': 'Android', 'ios': 'iOS', 'windows': 'Windows', 'macos': 'macOS'}
    label = labels.get(platform, platform.capitalize())
    with sqlite3.connect(USERS_DB) as db:
        db.execute(
            "INSERT OR IGNORE INTO vpn_devices (tg_id, platform, label) VALUES (?, ?, ?)",
            (tg_id, platform, label),
        )


async def remove_device(device_id: int, tg_id: int) -> None:
    _init_devices_table()
    with sqlite3.connect(USERS_DB) as db:
        db.execute("DELETE FROM vpn_devices WHERE id = ? AND tg_id = ?", (device_id, tg_id))

def load_promo():
    with open('promos.json', 'r') as p:
        return json.load(p)


def create_promocode(code: str, owner_id: int, discount: int) -> bool:
    with sqlite3.connect(USERS_DB) as db:
        try:
            db.execute("INSERT INTO promocodes (code, owner_id, discount) VALUES (?, ?, ?)", (code, owner_id, discount))
            return True
        except sqlite3.IntegrityError:
            return False


def get_promocode(code: str):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT code, owner_id, discount FROM promocodes WHERE code = ?", (code.upper(),))
        return cur.fetchone()


def get_user_promo(tg_id: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT code FROM user_promos WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        return row[0] if row else None


def set_user_promo(tg_id: int, code: str):
    with sqlite3.connect(USERS_DB) as db:
        db.execute("INSERT OR REPLACE INTO user_promos (tg_id, code) VALUES (?, ?)", (tg_id, code))


def get_trafer_promocodes(owner_id: int):
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT code, discount FROM promocodes WHERE owner_id = ?", (owner_id,))
        return cur.fetchall()


def delete_promocode(code: str):
    with sqlite3.connect(USERS_DB) as db:
        db.execute("DELETE FROM promocodes WHERE code = ?", (code.upper(),))


def update_promocode_discount(code: str, discount: int):
    with sqlite3.connect(USERS_DB) as db:
        db.execute("UPDATE promocodes SET discount = ? WHERE code = ?", (discount, code.upper()))

