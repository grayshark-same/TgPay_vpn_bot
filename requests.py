import sqlite3
import os
from aiogram import Dispatcher, Bot, F
from aiogram.filters import CommandStart, Command, Filter
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from main import REPORTS_DB, USERS_DB
import datetime


async def add_user(tg_id: int, username: str) -> bool:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
        user = cur.fetchone()
        if not user:
            end_date = (datetime.datetime.now() + datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("INSERT INTO users (tg_id, username, end_of_sub) VALUES(?, ?, ?)", (tg_id, username, end_date))
            return True
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
