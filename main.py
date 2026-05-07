import os
import datetime
from aiogram import Dispatcher, Bot, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State, default_state
from aiogram.filters import Command, StateFilter
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from aiogram.utils.deep_linking import create_start_link, decode_payload
from dotenv import load_dotenv
from requests import *
from platega import create_platega_transaction, check_platega_status
import sqlite3

load_dotenv()

_proxy = os.getenv('PROXY')
bot = Bot(os.getenv('BOT_TOKEN'), session=AiohttpSession(proxy=_proxy) if _proxy else None)
dp = Dispatcher()

admins = os.getenv('ADMINS').split(', ')
admin = os.getenv('ADMIN')
card = os.getenv('CARD')
REF_PERCENT = int(os.getenv('REF_PERCENT', 70))

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
        ('devices',     'INTEGER DEFAULT 0'),
        ('ref_id',      'INTEGER DEFAULT 0'),
        ('ref_balance', 'INTEGER DEFAULT 0'),
        ('ref_procent', 'INTEGER DEFAULT 0'),
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

bot_balance = 0
plans = {1: 199, 3: 499, 6: 899, 12: 1499}
plan_days_map = {1: 30, 3: 90, 6: 180, 12: 360}
plan_names = {1: '1 месяц', 3: '3 месяца', 6: '6 месяцев', 12: '⚡️12 месяцев'}

admin_panel = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='Статистика', callback_data='statistic')],
    [InlineKeyboardButton(text='Баланс пользователя', callback_data='admin_balance')],
    [InlineKeyboardButton(text='Рассылка', callback_data='newsletter')]
])
admin_return_button = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text='Назад', callback_data='admin_return')]
])


class States(StatesGroup):
    summ = State()
    pay_receipt = State()
    # pay_way = State()
    newsletter_text = State()
    newsletter_photo = State()
    newsletter_buttons = State()
    admin_check_id = State()
    admin_deduct_summ = State()
    admin_deduct_ref_summ = State()

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


CHANNEL_ID = '@FishVPN_info'
CHANNEL_URL = 'https://t.me/FishVPN_info'

_sub_required_text = (
    '<tg-emoji emoji-id="6021418126061605425">📢</tg-emoji>  Для использования бота подпишитесь на <a href="http://t.me/FishVPN_info">наш канал</a>.\n\n'
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
    status = "активна ✅" if is_active else "не активна ❌"
    date_str = end_date.strftime("%d.%m.%Y") if end_date else "—"
    balance = await get_user_balance(user_id)

    text = (
        '<b>🎣 Fish VPN</b> – Стабильный, защищенный и анонимный VPN.\n\n'
        "• 🇳🇱 Нидерланды\n• 🇫🇮 Финляндия\n• 🇺🇸 США\n• 🇩🇪 Германия\n• 🇰🇿 Казахстан\n\n"
        f"<blockquote>📌 Ваша подписка:\n"
        f"Статус: <code>{status}</code>\n"
        f"Действует до: <code>{date_str}</code>\n"
        f"Лимит устройств: <code>3</code>\n"
        f"Ваш баланс: <code>{balance}</code></blockquote>"
        
    )
    buttons = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='Управление подпиской', callback_data='settings', icon_custom_emoji_id='6032742198179532882')],
        [InlineKeyboardButton(text='Пополнить баланс', callback_data='balance_0', icon_custom_emoji_id='5769126056262898415')],
        [InlineKeyboardButton(text='Реферальная система', callback_data='referral', icon_custom_emoji_id='6033125983572201397')],
        [InlineKeyboardButton(text='Продлить', callback_data='extend', icon_custom_emoji_id='5769126056262898415'),
         InlineKeyboardButton(text='Поддержка', callback_data='support', icon_custom_emoji_id='6030329749409108167')],
        [InlineKeyboardButton(text='Что это?', callback_data='about', icon_custom_emoji_id='6032594876506312598')]
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



async def poll_transaction(transaction_id: str, tg_id: int, summ: int):
    import asyncio
    for _ in range(90):  # 90 * 10s = 15 минут
        await asyncio.sleep(10)
        status = await check_platega_status(transaction_id)
        if status == 'CONFIRMED':
            await add_balance(tg_id=tg_id, summ=summ)
            await add_report(money=summ)
            try:
                await bot.send_message(
                    tg_id,
                    f'✅ Ваш баланс пополнен на <code>{summ}₽</code>!',
                    parse_mode='HTML',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_menu_btn()[0]]])
                )
            except Exception:
                pass
            return
        elif status in ('CANCELED', 'CHARGEBACKED'):
            try:
                await bot.send_message(tg_id, '❌ Платёж отменён.')
            except Exception:
                pass
            return


@dp.message(Command('start'))
async def start_handler(message: Message):
    if not await is_subscribed(message.from_user.id):
        await message.answer(
            _sub_required_text,
            reply_markup=_sub_required_markup,
            parse_mode='HTML'
        )
        return
    is_new = await add_user(message.from_user.id, message.from_user.username)
    args = message.text.split() if message.text else []
    if len(args) > 1 and args[1].isdigit():
        await set_ref_id(message.from_user.id, int(args[1]))
    await send_main_menu(message, message.from_user.id, message.from_user.username)
    if is_new:
        await message.answer(
            '<b>Добро пожаловать!</b> 👋\n\n'
            '📍Мы активировали для вас пробный доступ к VPN на <b>3 дня</b>. Спасибо, что выбрали нас!\n\n'
            'Подключиться очень просто:\n'
            '<blockquote>Нажмите:<b>"</b><b><tg-emoji emoji-id="5850309953293653168">⚙️</tg-emoji></b><b> Управление подпиской"</b> ниже, чтобы мгновенно получить доступ.</blockquote>',
            parse_mode='HTML'
        )




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
        count = await get_users_count()
        total_profit = 0
        todays_purchases = 0
        todays_profit = 0
        bot_balance = 0
        await message.answer(
            f'''---------ADMIN_PANEL---------
            
<tg-emoji emoji-id="5258204546391351475">💰</tg-emoji><b>Баланс бота</b>: <code>{bot_balance}</code>
<tg-emoji emoji-id="5890848474563352982">🪙</tg-emoji>Всего заработано: <code>{total_profit}</code>

<tg-emoji emoji-id="6032594876506312598">👥</tg-emoji>Всего юзеров: {count}
<tg-emoji emoji-id="5902206159095339799">🤑</tg-emoji>Прибыль сегодня: <code>{todays_profit}</code>
            ''',
            reply_markup=admin_panel, parse_mode='html'
        ),
    


@dp.callback_query()
async def callbacks(callback: CallbackQuery, state: FSMContext):
    data = callback.data
    user = callback.from_user

    if data == 'check_sub':
        if await is_subscribed(user.id):
            await callback.message.delete()
            await add_user(user.id, user.username)
            await send_main_menu(callback.message, user.id, user.username)
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
        text = (
            f"📍Главное меню » <tg-emoji emoji-id='6032742198179532882'>⚙️</tg-emoji> Управление подпиской » 🔗 Подключиться к VPN » <b>{name}</b>\n\n"
            "1. Нажмите «📥 Скачать приложение» и установите программу.\n\n"
            "2. Нажмите «🔗 Активировать VPN-профиль», чтобы добавить подключение.\n\n"
            "3. Готово! Выберите локацию и подключитесь!"
        )
        rows = []
        if download_url:
            rows.append([InlineKeyboardButton(text='📥 Скачать приложение', url=download_url)])
        rows.append([InlineKeyboardButton(text='🔗 Активировать VPN-профиль', callback_data=f'activate_{platform}')])
        rows.append([back_btn('connect')[0]])
        await edit_or_answer(callback, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    elif data.startswith('activate_'):
        await callback.answer("Функция в разработке", show_alert=True)

    elif data == 'devices':
        await callback.answer("Функция в разработке", show_alert=True)

    elif data == 'universal_link':
        await callback.answer("Функция в разработке", show_alert=True)

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
            [back_menu_btn()[0]]
        ])
        await edit_or_answer(callback, text, reply_markup=buttons)

    elif data.startswith('pay_sub_'):
        parts = data.split('_')
        summ, plan = int(parts[2]), int(parts[3])
        if summ <= await get_user_balance(user.id):
            await add_balance(summ=-summ, tg_id=user.id)
            await add_sub(tg_id=user.id, plan=plan)
            ref_id = await get_ref_id(user.id)
            if ref_id:
                *_, ref_procent = await get_ref_info(ref_id)
                try:
                    if ref_procent > 0:
                        reward = int(summ * ref_procent / 100)
                        if reward > 0:
                            await add_ref_balance(ref_id, reward)
                            await bot.send_message(
                                ref_id,
                                f'<tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> Ваш реферал оплатил подписку!\n'
                                f'<tg-emoji emoji-id="5890848474563352982">🪙</tg-emoji> Вам начислено <b>{reward}₽</b> на реферальный баланс.',
                                parse_mode='HTML'
                            )
                    else:
                        await add_days_to_sub(ref_id, 10)
                        await bot.send_message(
                            ref_id,
                            f'<tg-emoji emoji-id="6041731551845159060">🎉</tg-emoji> Ваш реферал оплатил подписку!\n'
                            f'🎁 Вам добавлено <b>10 дней</b> бесплатной подписки.',
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
        elif method in ('sbp', 'crypto'):
            from platega import METHOD_SBP, METHOD_CRYPTO
            pm = METHOD_SBP if method == 'sbp' else METHOD_CRYPTO
            method_label = 'СБП' if method == 'sbp' else 'Крипта'
            method_emoji = '🏦' if method == 'sbp' else '₿'
            transaction = await create_platega_transaction(summ, user.id, pm)
            pay_url = transaction.get('url') if transaction else None
            if pay_url:
                import asyncio
                asyncio.create_task(poll_transaction(transaction['transactionId'], user.id, summ))
                await edit_or_answer(
                    callback,
                    f'<tg-emoji emoji-id="5904359114531675993">💰</tg-emoji> Нажмите кнопку ниже для оплаты <b>{summ}₽</b> через <b>{method_label}</b>:\n\n'
                    f'<tg-emoji emoji-id="5778647930038653243">✨</tg-emoji> Ссылка действительна 15 минут.',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=f'{method_emoji} Оплатить {summ}₽', url=pay_url)],
                        [back_btn(f'balance_{summ}')[0]]
                    ])
                )
            else:
                await callback.answer("❌ Ошибка создания платежа. Попробуйте позже.", show_alert=True)
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
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='Политика Конфиденциальности', icon_custom_emoji_id="6037397706505195857", url='https://telegra.ph/Politika-konfidencialnosti-servisa-Fish-VPN-05-03')],
                                                               [InlineKeyboardButton(text='Пользовательское соглашение', icon_custom_emoji_id="6039422865189638057", url='https://telegra.ph/Polzovatelskoe-soglashenie-servisa-Fish-VPN-05-03')],
                                                               [back_menu_btn()[0]]])
        )

    elif str(user.id) in admins:
        if data == 'statistic':
            await callback.message.edit_text('statistic', reply_markup=admin_return_button)
        elif data == 'admin_return':
            count = await get_users_count()
            total_profit = 0
            todays_purchases = 0
            todays_profit = 0
            bot_balance = 0
            await callback.message.edit_text(
                f'''---------ADMIN_PANEL---------

<tg-emoji emoji-id="5258204546391351475">💰</tg-emoji><b>Баланс бота</b>: <code>{bot_balance}</code>
<tg-emoji emoji-id="5890848474563352982">🪙</tg-emoji>Всего заработано: <code>{total_profit}</code>

<tg-emoji emoji-id="6032594876506312598">👥</tg-emoji>Всего юзеров: {count}
<tg-emoji emoji-id="5902206159095339799">🤑</tg-emoji>Прибыль сегодня: <code>{todays_profit}</code>''',
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
            await bot.send_message(chat_id=uid, text=f'✅ Ваш баланс пополнен на {summ}!', reply_markup=InlineKeyboardMarkup(inline_keyboard=[[back_menu_btn()[0]]]))
        elif data.startswith('decline_'):
            uid = int(data.split('_')[1])
            await callback.message.delete()
            await bot.send_message(chat_id=uid, text=f'❌ Чек отклонён. Обратитесь в поддержку: @{admin.lstrip("@")}')
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
    await state.clear()
    from platega import METHOD_SBP, METHOD_CRYPTO
    pm = METHOD_SBP if method == 'sbp' else METHOD_CRYPTO
    method_label = 'СБП' if method == 'sbp' else 'Крипта'
    method_emoji = '🏦' if method == 'sbp' else '₿'
    transaction = await create_platega_transaction(summ, message.from_user.id, pm)
    pay_url = transaction.get('url') if transaction else None
    if pay_url:
        import asyncio
        asyncio.create_task(poll_transaction(transaction['transactionId'], message.from_user.id, summ))
        await message.answer(
            f"{method_emoji} Нажмите кнопку ниже для оплаты <b>{summ}₽</b> через <b>{method_label}</b>:\n\n"
            f"⏰ Ссылка действительна 15 минут.",
            parse_mode='HTML',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f'{method_emoji} Оплатить {summ}₽', url=pay_url)],
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


if __name__ == '__main__':
    print('bot active')
    dp.run_polling(bot)
    print('bot stopped')
