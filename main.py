import os
import datetime
from aiogram import Dispatcher, Bot, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State, default_state
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery, ReplyKeyboardRemove
from aiogram.utils.deep_linking import create_start_link, decode_payload
from dotenv import load_dotenv
from requests import *
from lava import create_lava_invoice, check_lava_status
from vpn import (
    ensure_vpn_account,
    get_happ_activation_url,
    get_subscription_url,
    init_vpn_db,
    start_subscription_server,
)
import sqlite3
 
load_dotenv() 

_proxy = os.getenv('PROXY')
bot = Bot(os.getenv('BOT_TOKEN'), session=AiohttpSession(proxy=_proxy) if _proxy else None)
dp = Dispatcher()

admins = os.getenv('ADMINS').split(', ')
admin = os.getenv('ADMIN')
card = os.getenv('CARD')
REF_PERCENT = int(os.getenv('REF_PERCENT', 70))
USD_RATE = float(os.getenv('USD_RATE', 90))

DB_DIR = os.getenv('DB_DIR', '.')
USERS_DB = os.path.join(DB_DIR, 'users.db')
REPORTS_DB = os.path.join(DB_DIR, 'reports.db')



with sqlite3.connect(USERS_DB) as db:
    cursor = db.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            balance REAL DEFAULT 0.0,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            devices INTEGER DEFAULT 0,
            end_of_sub TIMESTAMP,
            ref_id INTEGER DEFAULT 0,
            ref_balance INTEGER DEFAULT 0,
            ref_procent INTEGER DEFAULT 0
        )
    ''')
    for _col, _def in [
        ('devices',          'INTEGER DEFAULT 0'),
        ('ref_id',           'INTEGER DEFAULT 0'),
        ('ref_balance',      'INTEGER DEFAULT 0'),
        ('ref_procent',      'INTEGER DEFAULT 0'),
        ('notified_expiry',  'TIMESTAMP'),
    ]:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {_col} {_def}")
        except sqlite3.OperationalError:
            pass
with sqlite3.connect(REPORTS_DB) as db:
    cursor = db.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS reports_for_day (
                        date TIMESTAMP DEFAULT CURRENT_TIMESTAMP UNIQUE,
                        money INTEGER,
                        transactions INTEGER,
                        users INTEGER
                   )''')
with sqlite3.connect(USERS_DB) as db:
    db.execute('''CREATE TABLE IF NOT EXISTS pending_payments (
        order_id TEXT PRIMARY KEY,
        tg_id INTEGER NOT NULL,
        summ INTEGER NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
init_vpn_db()


def save_pending_payment(order_id: str, tg_id: int, summ: int):
    with sqlite3.connect(USERS_DB) as db:
        db.execute(
            "INSERT OR IGNORE INTO pending_payments (order_id, tg_id, summ) VALUES (?, ?, ?)",
            (order_id, tg_id, summ)
        )
    print(f"[payment] saved: order={order_id} tg_id={tg_id} summ={summ} db={USERS_DB}")


def remove_pending_payment(order_id: str):
    with sqlite3.connect(USERS_DB) as db:
        db.execute("DELETE FROM pending_payments WHERE order_id = ?", (order_id,))


def get_all_pending_payments():
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT order_id, tg_id, summ, created_at FROM pending_payments")
        return cur.fetchall()

bot_balance = 0
plans = {1: 360, 3: 960, 6: 1790, 12: 2990}
plan_days_map = {1: 30, 3: 90, 6: 180, 12: 360}
plan_names = {1: '1 месяц', 3: '3 месяца', 6: '6 месяцев', 12: '⚡️12 месяцев'}

admin_panel = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='Статистика', callback_data='statistic')],
    [InlineKeyboardButton(text='Баланс пользователя', callback_data='admin_balance')],
    [InlineKeyboardButton(text='Добавить трафера', callback_data='admin_add_trafer')],
    [InlineKeyboardButton(text='Выдать подписку', callback_data='admin_give_sub')],
    [InlineKeyboardButton(text='Выгрузить пользователей', callback_data='export_users')],
    [InlineKeyboardButton(text='Рассылка', callback_data='newsletter')]
])
admin_return_button = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='Назад', callback_data='admin_return')]
])


class States(StatesGroup):
    summ = State()
    pay_receipt = State()
    crypto_txid = State()
    # pay_way = State()
    newsletter_text = State()
    newsletter_photo = State()
    newsletter_buttons = State()
    admin_check_id = State()
    admin_deduct_summ = State()
    admin_deduct_ref_summ = State()
    admin_trafer_id = State()
    admin_trafer_procent = State()
    admin_give_sub_id = State()

# @dp.message(F.photo)
# async def get_file_id(message: Message):
#     print(message.photo[-1].file_id)

async def edit_or_answer(callback: CallbackQuery, text: str, reply_markup=None, parse_mode='HTML'):
    try:
        if callback.message.photo:
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        print(f'[edit_or_answer] {type(e).__name__}: {e}')


ARCHIVE_CHAT_ID = os.getenv('ARCHIVE_CHAT_ID')
CHANNEL_ID = os.getenv('CHANNEL_ID')
CHANNEL_URL = os.getenv('CHANNEL_URL')

_sub_required_text = (
    f'<tg-emoji emoji-id="6021418126061605425">📢</tg-emoji>  Для использования бота подпишитесь на <a href="{CHANNEL_URL}">наш канал</a>.\n\n'
    'После подписки нажмите «<b><tg-emoji emoji-id="5774022692642492953">✅</tg-emoji> Я подписался</b>».'
)
_sub_required_markup = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='Новостной канал', url=CHANNEL_URL, icon_custom_emoji_id='6021418126061605425')],
    [InlineKeyboardButton(text='Я подписался', callback_data='check_sub', icon_custom_emoji_id='5774022692642492953')]
])

async def is_subscribed(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
        print(f'[sub check] user={user_id} status={member.status}')
        return member.status not in ('left', 'kicked', 'banned')
    except Exception as e:
        print(f'[sub check ERROR] {type(e).__name__}: {e}')
        return False


def back_menu_btn():
    return [InlineKeyboardButton(text='« Главное Меню', callback_data='menu')]


def back_btn(cb):
    return [InlineKeyboardButton(text='« Назад', callback_data=cb)]



async def send_main_menu(target, user_id, username=None):
    await add_user(user_id, username)
    is_active, end_date = await get_user_sub(user_id)
    status = "Активна ✅" if is_active else "Не активна ❌"
    date_str = end_date.strftime("%d.%m.%Y") if end_date else "—"
    balance = await get_user_balance(user_id)

    text = (
        '<tg-emoji emoji-id="5994750571041525522">👋</tg-emoji><b>Добро пожаловать в Скорость от TgPay</b>\n\n'
        '<tg-emoji emoji-id="5195033767969839232">🚀</tg-emoji> Быстрый и стабильный VPN\n'
        '<tg-emoji emoji-id="5985479497586053461">🗺</tg-emoji> Работает в любой точке мира\n'
        '<tg-emoji emoji-id="5865981429963296202">📱</tg-emoji> Поддержка iPhone, Android, Windows, Mac\n'
        f"<blockquote><tg-emoji emoji-id='6043896193887506430'>📌</tg-emoji> Ваша подписка: <code>{status}</code>\n"
        f"<tg-emoji emoji-id='5967382867632722656'>📅</tg-emoji>Действует до: <code>{date_str}</code>\n<tg-emoji emoji-id='5769126056262898415'>👛</tg-emoji>Ваш баланс: <code>{balance}₽</code></blockquote>\n\n"
        'Выберите действие ниже <tg-emoji emoji-id="5231102735817918643">👇</tg-emoji>'
    )
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Купить ключ', callback_data='extend', icon_custom_emoji_id='5927169041595634481'),
         InlineKeyboardButton(text='Пробный ключ', callback_data='trial_key', icon_custom_emoji_id='6005570495603282482')],
        [InlineKeyboardButton(text='Пополнить баланс', callback_data='balance_0', icon_custom_emoji_id='5769126056262898415')],
        [InlineKeyboardButton(text='Моя подписка', callback_data='settings', icon_custom_emoji_id='5258096772776991776'),
         InlineKeyboardButton(text='Рефералы', callback_data='referral', icon_custom_emoji_id='5944970130554359187')],
        [InlineKeyboardButton(text='Новости', url=CHANNEL_URL, icon_custom_emoji_id='6021418126061605425'),
         InlineKeyboardButton(text='Помощь', callback_data='support', icon_custom_emoji_id='5873121512445187130')],
        [InlineKeyboardButton(text='Правила', callback_data='policy', icon_custom_emoji_id='5956561916573782596')],
    ])
    photo = FSInputFile('menu.png')
    if isinstance(target, CallbackQuery):
        if target.message.photo:
            await target.message.edit_caption(caption=text, reply_markup=buttons, parse_mode='HTML')
        else:
            await target.message.delete()
            await target.message.answer_photo(photo=photo, caption=text, reply_markup=buttons, parse_mode='HTML')
    else:
        await target.answer_photo(photo=photo, caption=text, reply_markup=buttons, parse_mode='HTML')



async def _archive_topup(tg_id: int, summ: int, provider: str = "Lava", username: str | None = None):
    uname = f"@{username}" if username else f"tg_id: {tg_id}"
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    text = (
        f"💰 <b>Пополнение баланса</b>\n\n"
        f"👤 Клиент: {uname}\n"
        f"🆔 TG ID: <code>{tg_id}</code>\n"
        f"💵 Сумма: <code>{summ}</code>₽\n"
        f"🏦 Провайдер: {provider}\n\n"
        f"🕖 Дата: {now}"
    )
    try:
        await bot.send_message(ARCHIVE_CHAT_ID, text, parse_mode='HTML')
    except Exception as e:
        print(f'[archive] topup error: {e}')


async def _archive_purchase(tg_id: int, summ: int, plan: int, username: str | None = None):
    uname = f"@{username}" if username else f"tg_id: {tg_id}"
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    text = (
        f"🛒 <b>Покупка подписки</b>\n\n"
        f"👤 Клиент: {uname}\n"
        f"🆔 TG ID: <code>{tg_id}</code>\n"
        f"📦 Тариф: {plan_names[plan]}\n"
        f"💵 Сумма: <code>{summ}</code>₽\n\n"
        f"🕖 Дата: {now}"
    )
    try:
        await bot.send_message(ARCHIVE_CHAT_ID, text, parse_mode='HTML')
    except Exception as e:
        print(f'[archive] purchase error: {e}')


async def _handle_confirmed_payment(order_id: str, tg_id: int, summ: int):
    remove_pending_payment(order_id)
    await add_balance(tg_id=tg_id, summ=summ)
    await add_report(money=summ)
    await _archive_topup(tg_id, summ)
    ref_id = await get_ref_id(tg_id)
    if ref_id:
        user_info = await get_user_info(tg_id)
        uname = f"@{user_info[1]}" if user_info and user_info[1] else f"ID {tg_id}"
        *_, ref_procent = await get_ref_info(ref_id)
        if ref_procent > 0:
            reward = int(summ * ref_procent / 100)
            if reward > 0:
                await add_ref_balance(ref_id, reward)
            try:
                await bot.send_message(
                    ref_id,
                    f'<tg-emoji emoji-id="5890848474563352982">🪙</tg-emoji> <b>Реферальная комиссия!</b>\n\n'
                    f'<tg-emoji emoji-id="6033108709213736873">➕</tg-emoji> Ваш реферал {uname}\n'
                    f'<tg-emoji emoji-id="5258204546391351475">💰</tg-emoji> Пополнил баланс на <code>{summ}</code> ₽\n\n'
                    f'<tg-emoji emoji-id="5805298713211447980">🎁</tg-emoji> Ваша комиссия ({ref_procent}%): <code>{reward}</code> ₽\n\n'
                    f'<tg-emoji emoji-id="5836907383292436018">💎</tg-emoji> Средства зачислены на ваш баланс.',
                    parse_mode='HTML'
                )
            except Exception:
                pass
    try:
        await bot.send_message(
            tg_id,
            f'<tg-emoji emoji-id="5774022692642492953">✅</tg-emoji> Ваш баланс пополнен на <code>{summ}₽</code>!',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_menu_btn()[0]]])
        )
    except Exception:
        pass


async def payment_checker_loop():
    import asyncio
    print("[payment checker] loop started")
    while True:
        await asyncio.sleep(5)
        try:
            payments = get_all_pending_payments()
            if payments:
                print(f"[payment checker] tick: {len(payments)} pending")
            for order_id, tg_id, summ, created_at in payments:
                try:
                    created = datetime.datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
                    if (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - created).total_seconds() > 3600:
                        remove_pending_payment(order_id)
                        continue
                    print(f"[payment checker] calling check for order={order_id}")
                    status = await check_lava_status(order_id)
                    print(f"[payment checker] order={order_id} tg_id={tg_id} status={status}")
                    if status == 'success':
                        await _handle_confirmed_payment(order_id, tg_id, summ)
                    elif status in ('expired', 'cancel'):
                        remove_pending_payment(order_id)
                        try:
                            await bot.send_message(tg_id, '❌ Платёж отменён.')
                        except Exception:
                            pass
                except BaseException as e:
                    import traceback
                    print(f'[payment checker] order {order_id} error: {type(e).__name__}: {e}')
                    print(traceback.format_exc())
        except Exception as e:
            print(f'[payment checker loop ERROR] {type(e).__name__}: {e}')



@dp.message(Command('start'))
async def start_handler(message: Message):
    try:
        msg = await message.answer('.', reply_markup=ReplyKeyboardRemove())
        await bot.unpin_all_chat_messages(chat_id=message.chat.id)
        await msg.delete()
    except Exception as e:
        print(f'[start cleanup] {type(e).__name__}: {e}')
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            _sub_required_text,
            reply_markup=_sub_required_markup,
            parse_mode='HTML'
        )
        return
    is_new = await add_user(message.from_user.id, message.from_user.username)
    args = message.text.split() if message.text else []
    ref_id_from_link = 0
    if len(args) > 1 and args[1].isdigit():
        ref_id_from_link = int(args[1])
        await set_ref_id(message.from_user.id, ref_id_from_link)
    if is_new and ref_id_from_link and ref_id_from_link != message.from_user.id:
        uname = f"@{message.from_user.username}" if message.from_user.username else f"ID {message.from_user.id}"
        try:
            await bot.send_message(
                ref_id_from_link,
                f'<tg-emoji emoji-id="6033125983572201397">👥</tg-emoji> <b>Новый реферал!</b>\n\n'
                f'По вашей реферальной ссылке перешёл {uname}.',
                parse_mode='HTML'
            )
        except Exception:
            pass
    await send_main_menu(message, message.from_user.id, message.from_user.username)
    if is_new:
        await message.answer(
            '<b>Добро пожаловать!</b> 👋\n\n'
            '📍Мы активировали для вас пробный доступ к VPN на <b>3 дня</b>. Спасибо, что выбрали нас!\n\n'
            'Подключиться очень просто:\n'
            '<blockquote>Нажмите:<b>"</b><b><tg-emoji emoji-id="5850309953293653168">⚙️</tg-emoji></b><b> Управление подпиской"</b> ниже, чтобы мгновенно получить доступ.</blockquote>',
            parse_mode='HTML'
        )

@dp.message(Command('test_archive'))
async def test_archive_command(message: Message):
    if str(message.from_user.id) not in admins:
        return
    try:
        r = await bot.send_message(ARCHIVE_CHAT_ID, '✅ Тест архива работает!')
        await message.answer(f'✅ Сообщение отправлено, message_id: {r.message_id}')
    except Exception as e:
        await message.answer(f'❌ Ошибка: {e}')



@dp.message(Command('getlink'))
async def getlink_handler(message: Message):
    if str(message.from_user.id) not in admins:
        return
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=1"
    await message.answer(f'🔗 Ссылка для запуска бота:\n<code>{link}</code>', parse_mode='HTML')


@dp.message(Command('unpin'))
async def unpin_handler(message: Message):
    if str(message.from_user.id) not in admins:
        return
    try:
        await bot.unpin_all_chat_messages(chat_id=message.chat.id)
        await message.answer('✅ Все закреплённые сообщения откреплены.')
    except Exception as e:
        await message.answer(f'❌ Ошибка: {type(e).__name__}: {e}')


@dp.message(Command('restart'))
async def restart_handler(message: Message):
    if str(message.from_user.id) not in admins:
        return
    await message.answer('🔄 Перезапуск...')
    import sys
    os.execv(sys.executable, [sys.executable] + sys.argv)


@dp.message(Command('admin'))
async def admin_command(message: Message):
    if str(message.from_user.id) in admins:
        count, total_purchases, total_profit = await get_stats()
        _, today_purchases, today_profit = await get_stats(days=1)
        await message.answer(
            f'---------ADMIN_PANEL---------\n\n'
            f'<tg-emoji emoji-id="5890848474563352982">🪙</tg-emoji>Всего заработано: <code>{total_profit:.2f}₽</code> / <code>${total_profit/USD_RATE:.2f}</code>\n'
            f'<tg-emoji emoji-id="5902206159095339799">🤑</tg-emoji>Прибыль сегодня: <code>{today_profit:.2f}₽</code> / <code>${today_profit/USD_RATE:.2f}</code>\n\n'
            f'<tg-emoji emoji-id="6032594876506312598">👥</tg-emoji>Всего юзеров: <b>{count}</b>\n'
            f'<tg-emoji emoji-id="5902206159095339799">🤑</tg-emoji> Покупок всего: <b>{total_purchases}</b> | сегодня: <b>{today_purchases}</b>',
            reply_markup=admin_panel, parse_mode='HTML'
        )
    


@dp.callback_query()
async def callbacks(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    user = callback.from_user

    if data == 'check_sub':
        if await is_subscribed(user.id):
            await add_user(user.id, user.username)
            await send_main_menu(callback, user.id, user.username)
        else:
            await callback.answer('❌ Вы ещё не подписались на канал!', show_alert=True)
        return

    if not await is_subscribed(user.id):
        await callback.answer('📢 Подпишитесь на канал для использования бота.', show_alert=True)
        await callback.message.answer(
            _sub_required_text,
            reply_markup=_sub_required_markup,
            parse_mode='HTML'
        )
        return

    if data == 'menu':
        await send_main_menu(callback, user.id, user.username)

    elif data == 'settings':
        text = (
            "📍Главное меню » <tg-emoji emoji-id='6032742198179532882'>⚙️</tg-emoji> <b>Управление подпиской</b>\n\n"
            "🔗 <b>Подключение</b> — выберите платформу, затем автонастройку Happ.\n\n"
            "📱 <b>Мои устройства</b> — список привязанных устройств.\n\n"
            "📋 <b>Универсальная ссылка</b> — строка подписки для вставки в Happ и другие клиенты."
        )
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔗 Подключиться к VPN', callback_data='connect')],
            [InlineKeyboardButton(text='📱 Мои устройства', callback_data='devices')],
            [InlineKeyboardButton(text='📋 Универсальная ссылка', callback_data='universal_link')],
            [back_menu_btn()[0]]
        ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    elif data == 'connect':
        text = "📍Главное меню » <tg-emoji emoji-id='6032742198179532882'>⚙️</tg-emoji> Управление подпиской » 🔗 <b>Подключиться к VPN</b>\n\nВыберите устройство:"
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Android', callback_data='connect_android', icon_custom_emoji_id='6030400221232501136'),
             InlineKeyboardButton(text='iOS', callback_data='connect_ios', icon_custom_emoji_id='5775870512127283512')],
            [InlineKeyboardButton(text='🖥 Windows', callback_data='connect_windows'),
             InlineKeyboardButton(text='💻 MacOS', callback_data='connect_macos')],
            [back_btn('settings')[0]]
        ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    elif data.startswith('connect_'):
        platform = data.replace('connect_', '')
        info = {
            'android': ("<tg-emoji emoji-id='6030400221232501136'>🤖</tg-emoji> Android", 'https://play.google.com/store/apps/details?id=com.happproxy'),
            'ios':     ("<tg-emoji emoji-id='5775870512127283512'>🍏</tg-emoji> iOS",     'https://apps.apple.com/us/app/happ-proxy-utility/id6504287215'),
            'windows': ('🖥 Windows',  'https://www.happ.su/main/ru'),
            'macos':   ('💻 MacOS',   'https://apps.apple.com/us/app/happ-proxy-utility/id6504287215'),
        }
        name, download_url = info[platform]
        is_active, end_date = await get_user_sub(user.id)
        sub_url = None
        if is_active and end_date:
            try:
                await ensure_vpn_account(user.id, end_date, user.username)
                await upsert_device(user.id, platform)
                sub_url = get_subscription_url(user.id, user.username)
            except Exception as e:
                print(f'[vpn sync ERROR] {type(e).__name__}: {e}')

        sub_line = f"\n\n🔗 Ссылка для вставки в Happ:\n<code>{sub_url}</code>" if (platform == 'windows' and sub_url) else ""
        text = (
            f"📍Главное меню » <tg-emoji emoji-id='6032742198179532882'>⚙️</tg-emoji> Управление подпиской » 🔗 Подключиться к VPN » <b>{name}</b>\n\n"
            "1. Нажмите «📥 Скачать приложение» и установите программу.\n\n"
            "2. Нажмите «🔗 Активировать VPN-профиль», чтобы добавить подключение.\n\n"
            f"3. Готово! Выберите локацию и подключитесь!{sub_line}"
        )
        rows = []
        if download_url:
            rows.append([InlineKeyboardButton(text='📥 Скачать Happ (US App Store)', url=download_url)])
        if platform == 'ios':
            rows.append([InlineKeyboardButton(text='📥 Скачать Happ (RU App Store)', url='https://apps.apple.com/ru/app/happ-proxy-utility-plus/id6746188973')])
        if sub_url: 
            try:
                happ_url = await get_happ_activation_url(user.id, user.username)
                rows.append([InlineKeyboardButton(text='🔗 Активировать VPN-профиль', url=happ_url)])
            except Exception as e:
                print(f'[vpn sync ERROR] {type(e).__name__}: {e}')
                rows.append([InlineKeyboardButton(text='🔗 Активировать VPN-профиль', callback_data='activate_error')])
        else:
            rows.append([InlineKeyboardButton(text='🔗 Активировать VPN-профиль', callback_data='activate_no_sub')])
        rows.append([back_btn('connect')[0]])
        await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    elif data == 'activate_no_sub':
        await callback.answer("Сначала продлите подписку.", show_alert=True)

    elif data == 'activate_error':
        await callback.answer("Не удалось подготовить подписку. Напишите в поддержку.", show_alert=True)

    elif data.startswith('activate_'):
        await callback.answer("Откройте раздел подключения и нажмите кнопку автонастройки.", show_alert=True)

    elif data == 'devices' or data.startswith('device_remove_'):
        if data.startswith('device_remove_'):
            dev_id = int(data.split('_')[2])
            await remove_device(dev_id, user.id)
            await callback.answer("✅ Устройство удалено.")
        devices = await get_user_devices(user.id)
        _icons = {'android': '🤖', 'ios': '🍏', 'windows': '🖥', 'macos': '💻'}
        if devices:
            lines = []
            for i, (_, platform, label, added_at) in enumerate(devices, 1):
                icon = _icons.get(platform, '📱')
                try:
                    date_str = datetime.datetime.strptime(added_at, "%Y-%m-%d %H:%M:%S").strftime("%d.%m.%Y")
                except Exception:
                    date_str = "—"
                lines.append(f"{i}. {icon} {label} — {date_str}")
            body = "\n".join(lines)
        else:
            body = "Нет подключённых устройств.\n\nАктивируйте VPN через раздел «🔗 Подключиться к VPN»."
        text = (
            "📍Главное меню » ⚙️ Управление подпиской » <b>📱 Мои устройства</b>\n\n"
            f"{body}\n\n"
            f"<blockquote>Подключено: {len(devices)}/3</blockquote>"
        )
        rows = [
            [InlineKeyboardButton(text=f'🗑 Удалить {_icons.get(p, "📱")} {lbl}', callback_data=f'device_remove_{did}')]
            for did, p, lbl, _ in devices
        ]
        rows.append([back_btn('settings')[0]])
        await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    elif data == 'universal_link':
        is_active, end_date = await get_user_sub(user.id)
        if not is_active or not end_date:
            await callback.answer("Сначала продлите подписку.", show_alert=True)
            return
        try:
            sub_url = await ensure_vpn_account(user.id, end_date, user.username)
        except Exception as e:
            print(f'[vpn sync ERROR] {type(e).__name__}: {e}')
            await callback.answer("Не удалось подготовить подписку. Напишите в поддержку.", show_alert=True)
            return
        text = (
            "📋 <b>Универсальная ссылка</b>\n\n"
            f"<code>{sub_url}</code>\n\n"
            "Эта ссылка содержит все доступные серверы и действует до конца подписки."
        )
        happ_url = await get_happ_activation_url(user.id, user.username)
        await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔗 Активировать в Happ', url=happ_url)],
            [back_btn('settings')[0]]
        ]))

    elif data == 'extend':
        _, end_date = await get_user_sub(user.id)
        days_left = max((end_date - datetime.datetime.now()).days, 0) if end_date else 0
        text = (
            f"📍Главное меню » <b><tg-emoji emoji-id='5258204546391351475'>👛</tg-emoji> Продлить</b>\n\n"
            f"<blockquote>⏳ До окончания подписки: {days_left} дней\n"
            f"📱 Количество устройств: 3</blockquote>\n\n"
            f"💳 <b>Выберите тариф</b>:"
        )
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f'1 месяц — {plans[1]}₽', callback_data='plan_1')],
            [InlineKeyboardButton(text=f'3 месяца — {plans[3]}₽ ({round(plans[3]/3)}₽/мес)', callback_data='plan_3')],
            [InlineKeyboardButton(text=f'6 месяцев — {plans[6]}₽ ({round(plans[6]/6)}₽/мес)', callback_data='plan_6')],
            [InlineKeyboardButton(text=f'⚡️12 месяцев — {plans[12]}₽ ({round(plans[12]/12)}₽/мес)', callback_data='plan_12')],
            [back_menu_btn()[0]]
        ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    elif data.startswith('plan_'):
        plan = int(data.replace('plan_', ''))
        summ = plans[plan]
        text = (
            f"📍Главное меню » <tg-emoji emoji-id='5258204546391351475'>👛</tg-emoji> Продлить » <b>{plan_names[plan]}</b>\n\n"
            f"<blockquote>⏱️ {plan_days_map[plan]} дней • 3 устройства\n"
            f"💰 Сумма: {summ}₽</blockquote>\n"
        )
        buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Оплатить', callback_data=f'pay_sub_{summ}_{plan}', style='success')],
        [back_btn(f'extend')[0]]
        ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    # elif data.startswith('plan_'):
    #     plan = int(data.replace('plan_', ''))
    #     text = (
    #         f"📍Главное меню » <tg-emoji emoji-id='5258204546391351475'>👛</tg-emoji> Продлить » <b>{plan_names[plan]}</b>\n\n"
    #         f"<blockquote>⏱️ {plan_days_map[plan]} дней • 3 устройства\n"
    #         f"💰 Сумма: {plans[plan]}₽</blockquote>\n"
    #     )
    #     buttons = InlineKeyboardMarkup(inline_keyboard=[
    #         [InlineKeyboardButton(text='СБП', callback_data=f'pay_{plan}_sbp', icon_custom_emoji_id='5425008221330880308')],
    #         [InlineKeyboardButton(text='Карта', callback_data=f'pay_{plan}_card', icon_custom_emoji_id='5312057711091813718'),
    #          InlineKeyboardButton(text='Крипта', callback_data=f'pay_{plan}_crypto', icon_custom_emoji_id='5195308461193182892')],
    #         [back_btn('extend')[0]]
    #     ])
    #     await edit_or_answer(callback, text, reply_markup=buttons)

    elif data.startswith('balance_'):
        summ = data.split('_')[1]
        txt = ''
        if int(summ) != 0:
            txt = f"💰 Сумма: {summ}₽\n"
        text = (
            f"📍Главное меню » <tg-emoji emoji-id='5258204546391351475'>👛</tg-emoji> <b>Пополнение баланса</b>\n\n"
            f"{txt}"
            f"Выберите способ оплаты<tg-emoji emoji-id='5429411030960711866'>💬</tg-emoji>"
        )
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='СБП', callback_data=f'pay_{summ}_sbp', icon_custom_emoji_id='5425008221330880308')],
            [InlineKeyboardButton(text='Крипта', callback_data=f'pay_{summ}_crypto', icon_custom_emoji_id='5195308461193182892')],
            [InlineKeyboardButton(text='Картой', callback_data=f'pay_{summ}_card', icon_custom_emoji_id='5927169041595634481')],
            [back_menu_btn()[0]]
        ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    elif data.startswith('pay_sub_'):
        parts = data.split('_')
        summ, plan = int(parts[2]), int(parts[3])
        if summ <= await get_user_balance(user.id):
            await add_balance(summ=-summ, tg_id=user.id)
            await add_sub(tg_id=user.id, plan=plan)
            await _archive_purchase(user.id, summ, plan, user.username)
            _, end_date = await get_user_sub(user.id)
            if end_date:
                try:
                    await ensure_vpn_account(user.id, end_date, user.username)
                except Exception as e:
                    print(f'[vpn sync ERROR] {type(e).__name__}: {e}')
            ref_id = await get_ref_id(user.id)
            if ref_id:
                *_, ref_procent = await get_ref_info(ref_id)
                try:
                    uname = f"@{user.username}" if user.username else f"ID {user.id}"
                    if ref_procent > 0:
                        reward = int(summ * ref_procent / 100)
                        if reward > 0:
                            await add_ref_balance(ref_id, reward)
                            await bot.send_message(
                                ref_id,
                                f'<tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> <b>Реферал оплатил подписку!</b>\n\n'
                                f'👤 {uname}\n'
                                f'💵 Сумма: <code>{summ}₽</code>\n'
                                f'<tg-emoji emoji-id="5890848474563352982">🪙</tg-emoji> Вам начислено <b>{reward}₽</b> на реферальный баланс.',
                                parse_mode='HTML'
                            )
                    else:
                        await add_days_to_sub(ref_id, 10)
                        await bot.send_message(
                            ref_id,
                            f'<tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> <b>Реферал оплатил подписку!</b>\n\n'
                            f'👤 {uname}\n'
                            f'💵 Сумма: <code>{summ}₽</code>\n'
                            f'🎁 Вам добавлено <b>+10 дней</b> бесплатной подписки.',
                            parse_mode='HTML'
                        )
                except Exception:
                    pass
            await edit_or_answer(callback, text='✅ Подписка успешно куплена!', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_menu_btn()[0]]]))
        else:
            await edit_or_answer(callback, text=f'На вашем балансе нехватает <code>{summ - await get_user_balance(user.id)}</code>₽', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пополнить баланс', callback_data=f'balance_{summ}')]]))

    elif data.startswith('pay_') and not data.startswith('pay_sub_'):
        parts = data.split('_')
        summ, method = int(parts[1]), parts[2]
        if summ == 0:
            await state.update_data(method=method)
            await state.set_state(States.summ)
            await edit_or_answer(callback, '<tg-emoji emoji-id="5904359114531675993">💰</tg-emoji> Введите сумму пополнения:')
        elif method == 'card':
            await state.update_data(summ=summ)
            await state.set_state(States.pay_receipt)
            await edit_or_answer(
                callback,
                f'<tg-emoji emoji-id="5927169041595634481">💳</tg-emoji> <b>Оплата картой</b>\n\n'
                f'Переведите <b>{summ}₽</b> на карту:\n\n'
                f'<code>{card}</code>\n\n'
                f'После оплаты пришлите скриншот чека сюда.',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn(f'balance_{summ}')[0]]]),
            )
        elif method == 'sbp':
            import uuid
            order_id = str(uuid.uuid4())
            invoice = await create_lava_invoice(summ, order_id, user.id)
            pay_url = invoice.get('data', {}).get('url') if invoice else None
            if pay_url:
                save_pending_payment(order_id, user.id, summ)
                await edit_or_answer(
                    callback,
                    f'<tg-emoji emoji-id="5425008221330880308">🏦</tg-emoji> <b>Оплата через СБП</b>\n\n'
                    f'Нажмите кнопку ниже для оплаты <b>{summ}₽</b>:\n\n'
                    f'<tg-emoji emoji-id="5778647930038653243">✨</tg-emoji> Ссылка действительна 1 час.',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f'Оплатить {summ}₽', url=pay_url, icon_custom_emoji_id='5425008221330880308')],
                        [back_btn(f'balance_{summ}')[0]]
                    ])
                )
            else:
                await callback.answer("❌ Ошибка создания платежа. Попробуйте позже.", show_alert=True)
        elif method == 'crypto':
            import aiohttp
            usdt_wallet = os.getenv('USDT_WALLET', '')
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get('https://api.binance.com/api/v3/ticker/price?symbol=USDTRUB') as r:
                        kurs = float((await r.json())['price'])
            except Exception:
                kurs = 90.0
            summ_usdt = round(summ / kurs, 2)
            await state.update_data(summ=summ)
            await state.set_state(States.crypto_txid)
            await callback.message.delete()
            await callback.message.answer(
                f'<tg-emoji emoji-id="5195308461193182892">🪙</tg-emoji> <b>Оплата USDT (TRC-20)</b>\n\n'
                f'Переведите <code>{summ_usdt}</code> USDT на адрес:\n\n'
                f'<code>{usdt_wallet}</code>\n\n'
                f'После оплаты пришлите TxID транзакции сюда.',
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_btn(f'balance_{summ}')[0]]])
            )
        else:
            await callback.answer("Метод оплаты недоступен.", show_alert=True)

        
    elif data in ('referral', 'ref_withdraw'):
        if data == 'ref_withdraw':
            amount = await transfer_ref_balance(user.id)
            if amount > 0:
                await callback.answer(f'✅ {amount}₽ переведено на ваш баланс!', show_alert=True)
            else:
                await callback.answer('Реферальный баланс пуст.', show_alert=True)
        ref_balance, ref_count, ref_procent = await get_ref_info(user.id)
        me = await bot.get_me()
        ref_link = f"https://t.me/{me.username}?start={user.id}"
        if ref_procent > 0:
            reward_line = (
                f"<tg-emoji emoji-id=\"5879814368572478751\">🏧</tg-emoji> Реферальный баланс: <code>{ref_balance}</code><b>₽</b>\n"
                f"<tg-emoji emoji-id=\"5936143551854285132\">📊</tg-emoji> Ваш процент: <code>{ref_procent}%</code>\n\n"
                f"За каждую оплату реферала вы получаете <code>{ref_procent}%</code> от суммы."
            )
        else:
            reward_line = "🎁 За каждую оплату реферала вы получаете <b>10 дней</b> бесплатной подписки."
        text = (
            f"📍Главное меню » <tg-emoji emoji-id=\"6033125983572201397\">👥</tg-emoji> <b>Реферальная система</b>\n\n"
            f"<blockquote>🔗 Ваша реферальная ссылка:\n<code>{ref_link}</code></blockquote>\n\n"
            f"<tg-emoji emoji-id=\"6033108709213736873\">➕</tg-emoji> Приглашено: <code>{ref_count}</code> чел.\n"
            f"{reward_line}"
        )
        rows = []
        if ref_balance > 0:
            rows.append([InlineKeyboardButton(text=f'💸 Вывести {ref_balance}₽ на основной баланс', callback_data='ref_withdraw')])
        rows.append([back_menu_btn()[0]])
        await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    elif data == 'support':
        text = (
            f"📍Главное меню » <b><tg-emoji emoji-id='6030329749409108167'>💬</tg-emoji> Поддержка</b>\n\n"
            f"Скопируйте ваш ID и отправьте в поддержку с описанием проблемы.\n\n"
            f"📋 Ваш ID: <blockquote>{user.id}</blockquote>"
        )
        buttons = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='Поддержка', url=f'https://t.me/{admin.lstrip("@")}', icon_custom_emoji_id='6030329749409108167')],
            [back_menu_btn()[0]]
        ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    elif data == 'about':
        text = ('''📍Главное меню » <tg-emoji emoji-id="6032594876506312598">👥</tg-emoji><b>Что это?</b>

<tg-emoji emoji-id="5920515922505765329">⚡️</tg-emoji>Молниеносная скорость:
<blockquote>• До 25 Гбит/с — смотрите 4K без задержек
• 13+ Серверов — стабильное соединение
• VLESS + Reality — современный протокол</blockquote>

<tg-emoji emoji-id="6039729023343400390">🔨</tg-emoji><b>Максимальная защита:</b>
<blockquote>• Никаких логов — ваша приватность под защитой
• Умное шифрование — ваши данные в безопасности
• Защита от утечек — полная анонимность</blockquote>

<tg-emoji emoji-id="5890925363067886150">✨</tg-emoji><b>Почему выбирают нас:</b>
<blockquote>• Никакой рекламы — чистый интернет
• Настройка за 1 минуту — всё просто 
• Поддержка 24/7 — всегда на связи
• Пробный период — попробуйте бесплатно
• Доступная цена — качество без переплат</blockquote>'''
        )
        await edit_or_answer(
            callback,
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Политика Конфиденциальности', icon_custom_emoji_id="6037397706505195857", url='https://telegra.ph/Politika-konfidencialnosti-servisa-Skorost-ot-TgPay-05-19')],
                                                               [InlineKeyboardButton(text='Пользовательское соглашение', icon_custom_emoji_id="6039422865189638057", url='https://telegra.ph/Polzovatelskoe-soglashenie-servisa-Skorost-ot-TgPay-05-19')],
                                                               [back_menu_btn()[0]]])
        )

    elif data == 'trial_key':
        is_active, end_date = await get_user_sub(user.id)
        if is_active and end_date:
            date_str = end_date.strftime("%d.%m.%Y")
            text = (
                f"📍Главное меню » <b>🔑 Пробный ключ</b>\n\n"
                f"У вас уже есть активная подписка до <code>{date_str}</code>.\n\n"
                "Нажмите «Подключиться», чтобы получить ключ для вашего устройства."
            )
            buttons = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔗 Подключиться к VPN', callback_data='connect')],
                [back_menu_btn()[0]]
            ])
        else:
            text = (
                "📍Главное меню » <b>🔑 Пробный ключ</b>\n\n"
                "<tg-emoji emoji-id='5890925363067886150'>✨</tg-emoji> Новым пользователям мы предоставляем <b>3 дня бесплатного доступа</b>!\n\n"
                "Если у вас нет активной подписки — пополните баланс и оформите тариф, "
                "либо воспользуйтесь пробным периодом при первом входе в бот."
            )
            buttons = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🛒 Купить подписку', callback_data='extend')],
                [back_menu_btn()[0]]
            ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    elif data == 'policy':
        text = (
            "📍Главное меню » <b>📄 Политика</b>\n\n"
            "Ознакомьтесь с нашими документами:"
        )
        await edit_or_answer(
            callback,
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='Политика конфиденциальности', icon_custom_emoji_id="6037397706505195857", url='https://telegra.ph/Politika-konfidencialnosti-servisa-Skorost-ot-TgPay-05-19')],
                [InlineKeyboardButton(text='Пользовательское соглашение', icon_custom_emoji_id="6039422865189638057", url='https://telegra.ph/Polzovatelskoe-soglashenie-servisa-Skorost-ot-TgPay-05-19')],
                [back_menu_btn()[0]]
            ])
        )

    elif str(user.id) in admins:
        if data == 'statistic' or data.startswith('stat_'):
            period = data.replace('stat_', '') if data.startswith('stat_') else 'all'
            period_days = {'day': 1, 'week': 7, 'month': 30, 'all': None}
            period_names = {'day': '1 день', 'week': 'Неделя', 'month': 'Месяц', 'all': 'Всё время'}
            users, transactions, money = await get_stats(period_days[period])
            def _btn(label, cb):
                return InlineKeyboardButton(text=f'• {label}' if cb == f'stat_{period}' else label, callback_data=cb)
            stat_text = (
                f'📊 <b>Статистика за {period_names[period].lower()}</b>\n\n'
                f'👥 Всего пользователей: <b>{users}</b>\n'
                f'🛒 Всего покупок: <b>{transactions}</b>\n'
                f'💰 Общая прибыль: <b>{money:.2f} ₽</b>'
            )
            stat_markup = InlineKeyboardMarkup(inline_keyboard=[
                [_btn('1 день', 'stat_day'), _btn('Неделя', 'stat_week')],
                [_btn('Месяц', 'stat_month'), _btn('Всё время', 'stat_all')],
                [InlineKeyboardButton(text='◀ Назад', callback_data='admin_return')]
            ])
            await callback.message.edit_text(stat_text, reply_markup=stat_markup, parse_mode='HTML')
        elif data == 'admin_return':
            count, total_purchases, total_profit = await get_stats()
            _, today_purchases, today_profit = await get_stats(days=1)
            await callback.message.edit_text(
                f'---------ADMIN_PANEL---------\n\n'
                f'<tg-emoji emoji-id="5890848474563352982">🪙</tg-emoji>Всего заработано: <code>{total_profit:.2f}₽</code> / <code>${total_profit/USD_RATE:.2f}</code>\n'
                f'<tg-emoji emoji-id="5902206159095339799">🤑</tg-emoji>Прибыль сегодня: <code>{today_profit:.2f}₽</code> / <code>${today_profit/USD_RATE:.2f}</code>\n\n'
                f'<tg-emoji emoji-id="6032594876506312598">👥</tg-emoji>Всего юзеров: <b>{count}</b>\n'
                f'<tg-emoji emoji-id="5902206159095339799">🤑</tg-emoji> Покупок всего: <b>{total_purchases}</b> | сегодня: <b>{today_purchases}</b>',
                reply_markup=admin_panel,
                parse_mode='HTML'
            )
        elif data.startswith('accept_'):
            _, summ, uid = data.split('_')
            summ, uid = int(summ), int(uid)
            print(uid)
            try:
                await callback.message.delete()
            except:
                pass
            await add_balance(tg_id=uid,summ=summ)
            # await add_sub(tg_id=uid, summ=summ)
            await add_report(money=summ)
            await _archive_topup(uid, summ, provider="Ручное пополнение")
            await bot.send_message(chat_id=uid, text=f'<tg-emoji emoji-id="5774022692642492953">✅</tg-emoji> Ваш баланс пополнен на <code>{summ}₽</code>!', parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_menu_btn()[0]]]))
        elif data.startswith('decline_'):
            uid = int(data.split('_')[1])
            await callback.message.delete()
            await bot.send_message(chat_id=uid, text=f'❌ Чек отклонён. Обратитесь в поддержку: @{admin.lstrip("@")}')
        elif data == 'admin_give_sub':
            await state.set_state(States.admin_give_sub_id)
            await callback.message.answer('👤 Введите ID пользователя для выдачи подписки:')

        elif data.startswith('admin_give_plan_'):
            parts = data.split('_')
            plan, uid = int(parts[3]), int(parts[4])
            await add_sub(tg_id=uid, plan=plan)
            _, end_date = await get_user_sub(uid)
            if end_date:
                try:
                    info = await get_user_info(uid)
                    await ensure_vpn_account(uid, end_date, info[1] if info else None)
                except Exception as e:
                    print(f'[vpn sync ERROR] {type(e).__name__}: {e}')
            date_str = end_date.strftime('%d.%m.%Y') if end_date else '—'
            await callback.message.edit_text(
                f'✅ Подписка <b>{plan_names[plan]}</b> выдана пользователю <code>{uid}</code>\n'
                f'📅 Активна до: <code>{date_str}</code>',
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='« В панель', callback_data='admin_return')]])
            )
            try:
                await bot.send_message(
                    uid,
                    f'🎁 Администратор выдал вам подписку на <b>{plan_names[plan]}</b>!\n'
                    f'📅 Активна до: <code>{date_str}</code>',
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_menu_btn()[0]]])
                )
            except Exception:
                pass

        elif data == 'admin_add_trafer':
            await state.set_state(States.admin_trafer_id)
            await callback.message.answer('👤 Введите ID пользователя для назначения трафером:')

        elif data == 'admin_balance':
            await state.set_state(States.admin_check_id)
            await callback.message.answer('👤 Введите ID пользователя:')

        elif data.startswith('admin_deduct_'):
            parts = data.split('_')
            if parts[2] == 'ref':
                uid = int(parts[3])
                ref_balance, *_ = await get_ref_info(uid)
                await state.update_data(deduct_uid=uid)
                await state.set_state(States.admin_deduct_ref_summ)
                await callback.message.answer(
                    f'🤝 Реферальный баланс: <code>{ref_balance}₽</code>\n\nВведите сумму для списания:',
                    parse_mode='HTML'
                )
            else:
                uid = int(parts[2])
                info = await get_user_info(uid)
                balance = int(info[2]) if info else 0
                await state.update_data(deduct_uid=uid)
                await state.set_state(States.admin_deduct_summ)
                await callback.message.answer(
                    f'💰 Основной баланс: <code>{balance}₽</code>\n\nВведите сумму для списания:',
                    parse_mode='HTML'
                )

        elif data == 'export_users':
            import openpyxl, io
            from aiogram.types import BufferedInputFile
            wb = openpyxl.Workbook()

            def _dump_table(db_path, table, sheet_name, ws=None):
                with sqlite3.connect(db_path) as db:
                    cur = db.cursor()
                    cur.execute(f"SELECT * FROM {table}")
                    cols = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                sheet = ws if ws else wb.create_sheet(sheet_name)
                sheet.title = sheet_name
                sheet.append(cols)
                for row in rows:
                    sheet.append(list(row))
                return len(rows)

            first = wb.active
            n_users      = _dump_table(USERS_DB,   'users',             'Пользователи', ws=first)
            _dump_table(USERS_DB,   'vpn_accounts',      'VPN аккаунты')
            _dump_table(USERS_DB,   'vpn_node_accounts', 'Node аккаунты')
            try:
                _dump_table(USERS_DB, 'vpn_devices', 'Устройства')
            except Exception:
                pass
            _dump_table(REPORTS_DB, 'reports_for_day',   'Отчёты по дням')

            buf = io.BytesIO()
            wb.save(buf)
            buf.seek(0)
            await callback.message.answer_document(
                BufferedInputFile(buf.read(), filename='vpn_db.xlsx'),
                caption=f'📊 Выгрузка БД\n👥 Пользователей: {n_users}'
            )
            await callback.answer()

        elif data == 'newsletter':
            await state.set_state(States.newsletter_text)
            await callback.message.answer('✍️ Введите текст рассылки:')
        elif data == 'nl_skip_photo':
            await state.set_state(States.newsletter_buttons)
            await callback.message.answer(
                '🔗 Введите кнопки в формате:\n<code>Текст кнопки | https://ссылка</code>\n\nКаждая кнопка с новой строки. Или нажмите «Пропустить».',
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пропустить', callback_data='nl_skip_buttons')]])
            )
        elif data == 'nl_skip_buttons':
            fsm_data = await state.get_data()
            await _send_newsletter_preview(callback.message, fsm_data)
        elif data == 'nl_confirm':
            fsm_data = await state.get_data()
            await state.clear()
            await edit_or_answer(callback, '📤 Рассылка запущена...')
            count = await _do_newsletter(fsm_data)
            await edit_or_answer(text=f'✅ Рассылка отправлена {count} пользователям.', callback=callback)
        elif data == 'nl_cancel':
            await state.clear()
            await callback.message.edit_or_answer(callback, '❌ Рассылка отменена.')



async def _parse_buttons(text: str) -> list:
    rows = []
    for line in text.strip().splitlines():
        if '|' in line:
            label, url = line.split('|', 1)
            rows.append([InlineKeyboardButton(text=label.strip(), url=url.strip())])
    return rows


async def _send_newsletter_preview(message: Message, fsm_data: dict):
    text = fsm_data.get('nl_text', '')
    photo = fsm_data.get('nl_photo')
    buttons_text = fsm_data.get('nl_buttons', '')
    rows = await _parse_buttons(buttons_text) if buttons_text else []
    rows.append([
        InlineKeyboardButton(text='✅ Отправить', callback_data='nl_confirm'),
        InlineKeyboardButton(text='❌ Отмена', callback_data='nl_cancel')
    ])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.answer('👁 <b>Предпросмотр:</b>', parse_mode='HTML')
    if photo:
        await message.answer_photo(photo=photo, caption=text, parse_mode='HTML', reply_markup=markup)
    else:
        await message.answer(text, parse_mode='HTML', reply_markup=markup)


async def _do_newsletter(fsm_data: dict) -> int:
    text = fsm_data.get('nl_text', '')
    photo = fsm_data.get('nl_photo')
    buttons_text = fsm_data.get('nl_buttons', '')
    rows = await _parse_buttons(buttons_text) if buttons_text else []
    markup = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None

    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT tg_id FROM users")
        user_ids = [row[0] for row in cur.fetchall()]

    count = 0
    for uid in user_ids:
        try:
            if photo:
                await bot.send_photo(chat_id=uid, photo=photo, caption=text, parse_mode='HTML', reply_markup=markup)
            else:
                await bot.send_message(chat_id=uid, text=text, parse_mode='HTML', reply_markup=markup)
            count += 1
        except:
            pass
    return count


@dp.message(States.newsletter_text)
async def newsletter_get_text(message: Message, state: FSMContext):
    await state.update_data(nl_text=message.text or message.caption or '')
    await state.set_state(States.newsletter_photo)
    await message.answer(
        '🖼 Прикрепите фото или нажмите «Пропустить»:',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пропустить', callback_data='nl_skip_photo')]])
    )


@dp.message(States.newsletter_photo, F.photo)
async def newsletter_get_photo(message: Message, state: FSMContext):
    await state.update_data(nl_photo=message.photo[-1].file_id)
    await state.set_state(States.newsletter_buttons)
    await message.answer(
        '🔗 Введите кнопки в формате:\n<code>Текст кнопки | https://ссылка</code>\n\nКаждая кнопка с новой строки. Или нажмите «Пропустить».',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Пропустить', callback_data='nl_skip_buttons')]])
    )


@dp.message(States.newsletter_buttons)
async def newsletter_get_buttons(message: Message, state: FSMContext):
    await state.update_data(nl_buttons=message.text)
    fsm_data = await state.get_data()
    await _send_newsletter_preview(message, fsm_data)

@dp.message(States.summ)
async def summ_handler(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        await message.answer('Введите целое число:')
        return
    summ = int(message.text)
    fsm_data = await state.get_data()
    method = fsm_data['method']
    if method == 'card':
        await state.update_data(summ=summ)
        await state.set_state(States.pay_receipt)
        await message.answer(
            f'💳 <b>Оплата картой</b>\n\n'
            f'Переведите <b>{summ}₽</b> на карту:\n\n'
            f'<code>{card}</code>\n\n'
            f'После оплаты пришлите скриншот чека сюда.',
            parse_mode='HTML'
        )
        return
    if method == 'crypto':
        import aiohttp
        usdt_wallet = os.getenv('USDT_WALLET', '')
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get('https://api.binance.com/api/v3/ticker/price?symbol=USDTRUB') as r:
                    kurs = float((await r.json())['price'])
        except Exception:
            kurs = 90.0
        summ_usdt = round(summ / kurs, 2)
        await state.update_data(summ=summ)
        await state.set_state(States.crypto_txid)
        await message.answer(
            f'<tg-emoji emoji-id="5195308461193182892">🪙</tg-emoji> <b>Оплата USDT (TRC-20)</b>\n\n'
            f'Переведите <code>{summ_usdt}</code> USDT на адрес:\n\n'
            f'<code>{usdt_wallet}</code>\n\n'
            f'После оплаты пришлите TxID транзакции сюда.',
            parse_mode='HTML'
        )
        return
    await state.clear()
    import uuid
    order_id = str(uuid.uuid4())
    invoice = await create_lava_invoice(summ, order_id, message.from_user.id)
    pay_url = invoice.get('data', {}).get('url') if invoice else None
    if pay_url:
        save_pending_payment(order_id, message.from_user.id, summ)
        await message.answer(
            f'<tg-emoji emoji-id="5904359114531675993">💰</tg-emoji> Нажмите кнопку ниже для оплаты <b>{summ}₽</b> через <b>СБП</b>:\n\n'
            f'<tg-emoji emoji-id="5778647930038653243">✨</tg-emoji> Ссылка действительна 15 минут.',
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f'Оплатить {summ}₽', url=pay_url, icon_custom_emoji_id='5425008221330880308')],
                [back_menu_btn()[0]]
            ])
        )
    else:
        await message.answer('❌ Ошибка создания платежа. Попробуйте позже.')
    

@dp.message(States.pay_receipt, F.photo)
async def receive_receipt(message: Message, state: FSMContext):
    photo = message.photo[-1].file_id
    fsm_data = await state.get_data()
    # plan = fsm_data.get('plan')
    summ = fsm_data.get('summ')

    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Принять', callback_data=f'accept_{summ}_{message.from_user.id}', style='success')],
        [InlineKeyboardButton(text='Отклонить', callback_data=f'decline_{message.from_user.id}', style='danger')]
    ])
    for admin_id in admins:
        await bot.send_photo(
            chat_id=int(admin_id),
            photo=photo,
            caption=(f'Чек от @{message.from_user.username}\n'
                     f'ID: <code>{message.from_user.id}</code>\n'
                     f'Сумма: {summ}₽'),
            parse_mode='HTML',
            reply_markup=buttons
        )
    await state.clear()
    await message.answer("✅ Чек отправлен на проверку. Ожидайте активации.")


@dp.message(States.crypto_txid)
async def receive_crypto_txid(message: Message, state: FSMContext):
    txid = message.text.strip() if message.text else ''
    if not txid:
        await message.answer('Пришлите TxID транзакции.')
        return
    fsm_data = await state.get_data()
    summ = fsm_data.get('summ')
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='✅ Принять', callback_data=f'accept_{summ}_{message.from_user.id}')],
        [InlineKeyboardButton(text='❌ Отклонить', callback_data=f'decline_{message.from_user.id}')]
    ])
    for admin_id in admins:
        await bot.send_message(
            chat_id=int(admin_id),
            text=(f'🪙 <b>Крипта (USDT)</b> от @{message.from_user.username}\n'
                  f'🆔 ID: <code>{message.from_user.id}</code>\n'
                  f'💵 Сумма: {summ}₽\n'
                  f'🔖 TxID: <code>{txid}</code>'),
            parse_mode='HTML',
            reply_markup=buttons
        )
    await state.clear()
    await message.answer('✅ TxID отправлен на проверку. Ожидайте зачисления.')


@dp.message(States.admin_check_id)
async def admin_check_id_handler(message: Message, state: FSMContext):
    if str(message.from_user.id) not in admins:
        return
    if not message.text or not message.text.strip().lstrip('-').isdigit():
        await message.answer('❌ Введите корректный числовой ID:')
        return
    uid = int(message.text.strip())
    info = await get_user_info(uid)
    await state.clear()
    if not info:
        await message.answer('❌ Пользователь не найден.')
        return
    _, username, balance, ref_balance = info
    balance = int(balance) if balance else 0
    ref_balance = int(ref_balance) if ref_balance else 0
    uname_str = f'@{username}' if username else '—'
    text = (
        f'👤 Пользователь: {uname_str}\n'
        f'🆔 ID: <code>{uid}</code>\n'
        f'💰 Основной баланс: <code>{balance}₽</code>\n'
        f'🤝 Реферальный баланс: <code>{ref_balance}₽</code>'
    )
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='💸 Списать основной баланс', callback_data=f'admin_deduct_{uid}')],
        [InlineKeyboardButton(text='💸 Списать реф. баланс', callback_data=f'admin_deduct_ref_{uid}')],
        [InlineKeyboardButton(text='« Назад', callback_data='admin_return')]
    ])
    await message.answer(text, reply_markup=buttons, parse_mode='HTML')


@dp.message(States.admin_deduct_summ)
async def admin_deduct_summ_handler(message: Message, state: FSMContext):
    if str(message.from_user.id) not in admins:
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer('❌ Введите корректную сумму (только цифры):')
        return
    summ = int(message.text.strip())
    fsm_data = await state.get_data()
    uid = fsm_data.get('deduct_uid')
    current_balance = await get_user_balance(uid)
    if summ > current_balance:
        await message.answer(
            f'❌ Недостаточно средств. Баланс пользователя: <code>{current_balance}₽</code>',
            parse_mode='HTML'
        )
        return
    await add_balance(tg_id=uid, summ=-summ)
    await state.clear()
    new_balance = await get_user_balance(uid)
    await message.answer(
        f'✅ Списано <code>{summ}₽</code> у пользователя <code>{uid}</code>\n'
        f'Новый баланс: <code>{new_balance}₽</code>',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='« В панель', callback_data='admin_return')]
        ])
    )
    try:
        await bot.send_message(uid, f'❗️ Администратор списал <code>{summ}₽</code> с вашего баланса.', parse_mode='HTML')
    except Exception:
        pass


@dp.message(States.admin_deduct_ref_summ)
async def admin_deduct_ref_summ_handler(message: Message, state: FSMContext):
    if str(message.from_user.id) not in admins:
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer('❌ Введите корректную сумму (только цифры):')
        return
    summ = int(message.text.strip())
    fsm_data = await state.get_data()
    uid = fsm_data.get('deduct_uid')
    ref_balance, *_ = await get_ref_info(uid)
    if summ > ref_balance:
        await message.answer(
            f'❌ Недостаточно средств. Реф. баланс: <code>{ref_balance}₽</code>',
            parse_mode='HTML'
        )
        return
    await deduct_ref_balance(tg_id=uid, amount=summ)
    await state.clear()
    new_ref_balance, *_ = await get_ref_info(uid)
    await message.answer(
        f'✅ Списано <code>{summ}₽</code> с реф. баланса пользователя <code>{uid}</code>\n'
        f'Новый реф. баланс: <code>{new_ref_balance}₽</code>',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='« В панель', callback_data='admin_return')]
        ])
    )
    try:
        await bot.send_message(uid, f'<tg-emoji emoji-id="6039486778597970865">🔔</tg-emoji> Администратор списал <code>{summ}₽</code> с вашего реферального баланса.', parse_mode='HTML')
    except Exception:
        pass


@dp.message(States.admin_trafer_id)
async def admin_trafer_id_handler(message: Message, state: FSMContext):
    if str(message.from_user.id) not in admins:
        return
    if not message.text or not message.text.strip().lstrip('-').isdigit():
        await message.answer('❌ Введите корректный числовой ID:')
        return
    uid = int(message.text.strip())
    info = await get_user_info(uid)
    if not info:
        await message.answer('❌ Пользователь не найден в боте.')
        await state.clear()
        return
    _, username, _, _ = info
    await state.update_data(trafer_uid=uid, trafer_username=username)
    await state.set_state(States.admin_trafer_procent)
    uname_str = f'@{username}' if username else str(uid)
    await message.answer(
        f'✅ Пользователь найден: {uname_str}\n\n'
        f'Введите процент реферального вознаграждения (например: 70):',
        parse_mode='HTML'
    )


@dp.message(States.admin_trafer_procent)
async def admin_trafer_procent_handler(message: Message, state: FSMContext):
    if str(message.from_user.id) not in admins:
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer('❌ Введите корректное число от 1 до 100:')
        return
    procent = int(message.text.strip())
    if not 1 <= procent <= 100:
        await message.answer('❌ Процент должен быть от 1 до 100:')
        return
    fsm_data = await state.get_data()
    uid = fsm_data['trafer_uid']
    username = fsm_data['trafer_username']
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("UPDATE users SET ref_procent = ? WHERE tg_id = ?", (procent, uid))
    await state.clear()
    uname_str = f'@{username}' if username else str(uid)
    await message.answer(
        f'✅ Трафер <b>{uname_str}</b> назначен.\n'
        f'Реферальный процент: <b>{procent}%</b>',
        parse_mode='HTML',
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='« В панель', callback_data='admin_return')]
        ])
    )
    try:
        await bot.send_message(
            uid,
            f'🎉 Вам назначен реферальный процент <b>{procent}%</b>.\n'
            f'Теперь за каждую оплату вашего реферала вы получаете <b>{procent}%</b> на реферальный баланс.',
            parse_mode='HTML'
        )
    except Exception:
        pass


@dp.message(States.admin_give_sub_id)
async def admin_give_sub_id_handler(message: Message, state: FSMContext):
    if str(message.from_user.id) not in admins:
        return
    if not message.text or not message.text.strip().lstrip('-').isdigit():
        await message.answer('❌ Введите корректный числовой ID:')
        return
    uid = int(message.text.strip())
    info = await get_user_info(uid)
    if not info:
        await message.answer('❌ Пользователь не найден.')
        await state.clear()
        return
    await state.clear()
    _, username, _, _ = info
    is_active, end_date = await get_user_sub(uid)
    uname_str = f'@{username}' if username else str(uid)
    status = 'активна ✅' if is_active else 'не активна ❌'
    date_str = end_date.strftime('%d.%m.%Y') if end_date else '—'
    text = (
        f'👤 Пользователь: {uname_str}\n'
        f'🆔 ID: <code>{uid}</code>\n'
        f'📅 Подписка: {status} (до {date_str})\n\n'
        f'Выберите тариф для выдачи:'
    )
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='1 месяц (30 дней)', callback_data=f'admin_give_plan_1_{uid}')],
        [InlineKeyboardButton(text='3 месяца (90 дней)', callback_data=f'admin_give_plan_3_{uid}')],
        [InlineKeyboardButton(text='6 месяцев (180 дней)', callback_data=f'admin_give_plan_6_{uid}')],
        [InlineKeyboardButton(text='12 месяцев (360 дней)', callback_data=f'admin_give_plan_12_{uid}')],
        [InlineKeyboardButton(text='« В панель', callback_data='admin_return')]
    ])
    await message.answer(text, reply_markup=buttons, parse_mode='HTML')


async def check_expiring_subscriptions():
    now = datetime.datetime.now()
    window_start = now + datetime.timedelta(hours=23)
    window_end = now + datetime.timedelta(hours=25)
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute(
            "SELECT tg_id, username, end_of_sub FROM users "
            "WHERE end_of_sub IS NOT NULL "
            "AND (notified_expiry IS NULL OR notified_expiry != end_of_sub)"
        )
        candidates = cur.fetchall()
    notified = []
    for tg_id, username, end_of_sub_str in candidates:
        try:
            end_date = datetime.datetime.strptime(end_of_sub_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        if window_start <= end_date <= window_end:
            delta = end_date - now
            total_minutes = int(delta.total_seconds() // 60)
            hours_left = total_minutes // 60
            minutes_left = total_minutes % 60
            user_str = f'@{username}' if username else f'ID {tg_id}'
            text = (
                f'Уважаемый пользователь {user_str}!\n\n'
                f'<b><tg-emoji emoji-id="5420323339723881652">⚠️</tg-emoji></b>'
                f'<b>Ваш индивидуальный или пробный тариф скоро закончится.</b> \n\n'
                f'<tg-emoji emoji-id="5444856076954520455">🧾</tg-emoji>'
                f'Ваша подписка истекает через: \n'
                f'<b>{hours_left} часов {minutes_left} минут.</b> \n\n'
                f'Чтобы продлить, нажмите на кнопку ниже.'
            )
            try:
                await bot.send_message(
                    tg_id,
                    text,
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='Продлить подписку', callback_data='extend')]
                    ])
                )
                notified.append((end_of_sub_str, tg_id))
            except Exception as e:
                print(f'[expiry reminder] send failed to {tg_id}: {e}')
    if notified:
        with sqlite3.connect(USERS_DB) as db:
            cur = db.cursor()
            for end_of_sub_str, tg_id in notified:
                cur.execute(
                    "UPDATE users SET notified_expiry = ? WHERE tg_id = ?",
                    (end_of_sub_str, tg_id)
                )


async def expiry_reminder_loop():
    import asyncio
    while True:
        await asyncio.sleep(3600)
        try:
            await check_expiring_subscriptions()
        except Exception as e:
            print(f'[expiry reminder loop ERROR] {type(e).__name__}: {e}')


async def on_startup(*args, **kwargs):
    import asyncio
    await start_subscription_server()
    asyncio.create_task(expiry_reminder_loop())
    asyncio.create_task(payment_checker_loop())


dp.startup.register(on_startup)


if __name__ == '__main__':
    print('bot active')
    dp.run_polling(bot)
    print('bot stopped')
