import aiohttp
import hashlib
import hmac
import json
import os
import uuid

LAVATOP_BASE = "https://gate.lava.top"
LAVATOP_OFFER_ID = "b7e59827-e453-4082-9a2c-49c666efdeab"

_PLAN_PERIODICITY = {
    1: 'MONTHLY',
    3: 'period_90',
    6: 'period_180',
    12: 'period_year',
}


async def create_lavatop_subscription(plan: int, user_id: int, method: str = 'BANK_CARD') -> dict | None:
    """Создать инвойс подписки lava.top. method: BANK_CARD или SBP."""
    api_key = os.getenv('LAVATOP_API_KEY')
    if not api_key:
        print('[lavatop] LAVATOP_API_KEY not set')
        return None
    periodicity = _PLAN_PERIODICITY.get(plan)
    if not periodicity:
        return None
    payload = {
        'email': f'{user_id}@vpnbot.tg',
        'offerId': LAVATOP_OFFER_ID,
        'currency': 'RUB',
        'periodicity': periodicity,
        'paymentProvider': 'PAY2ME',
        'paymentMethod': method,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{LAVATOP_BASE}/api/v3/invoice',
                headers={'X-Api-Key': api_key, 'Content-Type': 'application/json'},
                json=payload,
            ) as resp:
                data = await resp.json()
                print(f'[lavatop] create subscription {resp.status}: {data}')
                if resp.status in (200, 201) and data.get('paymentUrl'):
                    return data
    except Exception as e:
        print(f'[lavatop] request error: {e}')
    return None


LAVA_BASE = "https://api.lava.ru"


def _sign(json_data: str, secret: str) -> str:
    return hmac.new(secret.encode(), json_data.encode(), hashlib.sha256).hexdigest()


async def create_lava_invoice(amount: int, order_id: str | None = None, user_id: int = 0) -> dict | None:
    shop_id = os.getenv("LAVA_SHOP_ID")
    secret = os.getenv("LAVA_SECRET_KEY")
    bot_url = os.getenv("BOT_URL", "https://t.me/")
    if not shop_id or not secret:
        print("[Lava] LAVA_SHOP_ID or LAVA_SECRET_KEY not set")
        return None
    if order_id is None:
        order_id = str(uuid.uuid4())
    payload = {
        "shopId": shop_id,
        "sum": amount,
        "orderId": order_id,
        "successUrl": bot_url,
        "failUrl": bot_url,
        "expire": 3600,
        "customFields": f"telegram_id:{user_id}",
        "comment": "Пополнение баланса",
        "includeService": ["card", "sbp"],
    }
    json_data = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{LAVA_BASE}/business/invoice/create",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Signature": _sign(json_data, secret),
                },
                data=json_data.encode(),
            ) as resp:
                data = await resp.json()
                print(f"[Lava] create response {resp.status}: {data}")
                if resp.status == 200:
                    return data
    except Exception as e:
        print(f"[Lava] request error: {e}")
    return None


async def check_lava_status(order_id: str) -> str | None:
    shop_id = os.getenv("LAVA_SHOP_ID")
    secret = os.getenv("LAVA_SECRET_KEY")
    payload = {"shopId": shop_id, "orderId": order_id}
    json_data = json.dumps(payload, separators=(',', ':'))
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{LAVA_BASE}/business/invoice/status",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Signature": _sign(json_data, secret),
                },
                data=json_data.encode(),
            ) as resp:
                data = await resp.json()
                print(f"[Lava] status response {resp.status}: {data}")
                if resp.status == 200:
                    code = data.get("data", {}).get("status")
                    if isinstance(code, str):
                        return code if code in ("success", "cancel", "expired") else "waiting"
                    return {1: "waiting", 2: "success", 3: "cancel", 4: "expired"}.get(code)
    except BaseException as e:
        import traceback
        print(f"[Lava] check status error: {type(e).__name__}: {e}")
        print(traceback.format_exc())
    return None
