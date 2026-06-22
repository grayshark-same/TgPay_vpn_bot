import datetime
import json
import os
import sqlite3
from aiohttp import web

USERS_DB = os.path.join(os.getenv('DB_DIR', '.'), 'users.db')
ARCHIVE_CHAT_ID = os.getenv('ARCHIVE_CHAT_ID')

_LAVATOP_PERIODICITY_TO_PLAN = {
    'MONTHLY':     1,
    'period_90':   3,
    'period_180':  6,
    'period_year': 12,
}
_PLAN_DAYS = {1: 30, 3: 90, 6: 180, 12: 360}
_PLAN_NAMES = {1: '1 месяц', 3: '3 месяца', 6: '6 месяцев', 12: '12 месяцев'}


def _add_sub_sync(tg_id: int, days: int):
    with sqlite3.connect(USERS_DB) as db:
        row = db.execute("SELECT end_of_sub FROM users WHERE tg_id = ?", (tg_id,)).fetchone()
        end_date = None
        if row and row[0]:
            try:
                end_date = datetime.datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass
        if not end_date or end_date < datetime.datetime.now():
            end_date = datetime.datetime.now()
        end_date += datetime.timedelta(days=days)
        db.execute("UPDATE users SET end_of_sub = ? WHERE tg_id = ?",
                   (end_date.strftime("%Y-%m-%d %H:%M:%S"), tg_id))


# lava.top — POST /lavatop
async def lavatop_webhook(request: web.Request) -> web.Response:
    from main import bot
    try:
        data = await request.json()
        print(f'[lavatop webhook] {json.dumps(data)}')

        event = data.get('event', '')
        if event not in ('payment.success', 'subscription.recurring.payment.success'):
            return web.Response(text='ok')

        buyer = data.get('buyer', {})
        email = buyer.get('email', '')
        # email передаётся как {tg_id}@vpnbot.tg при создании инвойса
        try:
            tg_id = int(email.split('@')[0])
        except (ValueError, IndexError):
            print(f'[lavatop webhook] cannot parse tg_id from email={email}')
            return web.Response(text='ok')

        receipt = data.get('receipt', {})
        amount = int(float(receipt.get('amount', 0)))

        periodicity = data.get('periodicity') or data.get('contract', {}).get('periodicity', '')
        plan = _LAVATOP_PERIODICITY_TO_PLAN.get(periodicity)
        if not plan:
            print(f'[lavatop webhook] unknown periodicity={periodicity}')
            return web.Response(text='ok')

        days = _PLAN_DAYS[plan]
        _add_sub_sync(tg_id, days)

        # синхронизируем VPN аккаунт
        try:
            from vpn import ensure_vpn_account
            from requests import get_user_sub
            _, end_date = await get_user_sub(tg_id)
            if end_date:
                await ensure_vpn_account(tg_id, end_date, None)
        except Exception as e:
            print(f'[lavatop webhook] vpn sync error: {e}')

        # уведомляем пользователя
        try:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='« Главное Меню', callback_data='menu')]
            ])
            label = 'Подписка продлена' if event == 'subscription.recurring.payment.success' else 'Подписка активирована'
            await bot.send_message(
                tg_id,
                f'✅ <b>{label}!</b>\n\n'
                f'📦 Тариф: <b>{_PLAN_NAMES[plan]}</b>\n'
                f'💵 Сумма: <code>{amount}₽</code>',
                parse_mode='HTML',
                reply_markup=markup
            )
        except Exception as e:
            print(f'[lavatop webhook] notify error: {e}')

        # архив
        try:
            invoice_id = data.get('id', '')
            text = (
                f'🔁 <b>Lava.top {"авто-продление" if "recurring" in event else "покупка"}</b>\n\n'
                f'🆔 TG ID: <code>{tg_id}</code>\n'
                f'📦 Тариф: {_PLAN_NAMES[plan]}\n'
                f'💵 Сумма: <code>{amount}₽</code>\n'
                f'🔖 Invoice: {invoice_id}'
            )
            await bot.send_message(int(ARCHIVE_CHAT_ID), text, parse_mode='HTML')
        except Exception as e:
            print(f'[lavatop webhook] archive error: {e}')

    except Exception as e:
        print(f'[lavatop webhook] error: {e}')
    return web.Response(text='ok')


async def _add_balance(tg_id: int, summ: int):
    with sqlite3.connect(USERS_DB) as db:
        db.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", (summ, tg_id))


async def _notify(bot, tg_id: int, summ: int, provider: str, archive_text: str):
    try:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='« Главное Меню', callback_data='menu')]
        ])
        await bot.send_message(
            tg_id,
            f'<tg-emoji emoji-id="5774022692642492953">✅</tg-emoji> Ваш баланс пополнен на <code>{summ}₽</code>!',
            parse_mode='HTML',
            reply_markup=markup
        )
    except Exception as e:
        print(f'[webhook] notify error: {e}')
    try:
        await bot.send_message(int(ARCHIVE_CHAT_ID), archive_text, parse_mode='HTML')
    except Exception as e:
        print(f'[webhook] archive error: {e}')


# NicePay — GET /nicepay
async def nicepay_webhook(request: web.Request) -> web.Response:
    from main import bot, _archive_topup
    from requests import add_balance, add_report
    try:
        result     = request.rel_url.query.get('result')
        payment_id = request.rel_url.query.get('payment_id')
        order_id   = request.rel_url.query.get('order_id', '')
        amount     = request.rel_url.query.get('amount', '0')

        print(f'[NicePay webhook] result={result} order_id={order_id} amount={amount}')

        parts = order_id.split('_')
        tg_id = int(parts[0])
        summ = int(float(amount))

        if result == 'success':
            await add_balance(tg_id=tg_id, summ=summ)
            await add_report(money=summ)
            archive_text = (
                f'💳 <b>Пополнение NicePay</b>\n\n'
                f'🆔 TG ID: <code>{tg_id}</code>\n'
                f'💵 Сумма: <code>{summ}</code>₽\n'
                f'🔖 Заявка: {payment_id}'
            )
            await _notify(bot, tg_id, summ, 'NicePay', archive_text)
        elif result == 'error':
            await bot.send_message(tg_id, '❌ Платёж отменён.')
    except Exception as e:
        print(f'[NicePay webhook] error: {e}')
    return web.Response(text='ok')


# Cryptomus — POST /cryptomus
async def cryptomus_webhook(request: web.Request) -> web.Response:
    from main import bot
    from requests import add_balance, add_report
    try:
        response = await request.json()
        print(f'[Cryptomus webhook] {response}')

        additional = json.loads(response.get('additional_data', '{}'))
        tg_id    = int(additional['user_id'])
        summ     = int(additional['price'])
        username = additional.get('username', '')
        status   = response.get('status')
        uuid     = response.get('uuid', '')

        if status in ('paid', 'paid_over'):
            await add_balance(tg_id=tg_id, summ=summ)
            await add_report(money=summ)
            archive_text = (
                f'🪙 <b>Пополнение Cryptomus</b>\n\n'
                f'👤 @{username}\n'
                f'🆔 TG ID: <code>{tg_id}</code>\n'
                f'💵 Сумма: <code>{summ}</code>₽\n'
                f'🔖 UUID: {uuid}'
            )
            await _notify(bot, tg_id, summ, 'Cryptomus', archive_text)
        elif status == 'wrong_amount':
            await bot.send_message(tg_id, '❌ Вы отправили неправильную сумму.')
        elif status == 'cancel':
            await bot.send_message(tg_id, '❌ Платёж отменён.')
    except Exception as e:
        print(f'[Cryptomus webhook] error: {e}')
    return web.Response(text='ok')
