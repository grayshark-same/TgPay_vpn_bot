import json
import os
import sqlite3
from aiohttp import web

USERS_DB = os.path.join(os.getenv('DB_DIR', '.'), 'users.db')
ARCHIVE_CHAT_ID = os.getenv('ARCHIVE_CHAT_ID')


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
