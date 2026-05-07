import aiohttp
import os


PLATEGA_BASE = "https://app.platega.io"


# 2 = СБП, 13 = Криптовалюта
METHOD_SBP = 2
METHOD_CRYPTO = 13


async def check_platega_status(transaction_id: str) -> str | None:
    merchant_id = os.getenv("PLATEGA_MERCHANT_ID")
    secret = os.getenv("PLATEGA_SECRET")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{PLATEGA_BASE}/transaction/{transaction_id}",
                headers={"X-MerchantId": merchant_id, "X-Secret": secret},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("status")
    except Exception as e:
        print(f"[Platega] check status error: {e}")
    return None


async def create_platega_transaction(amount: int, tg_id: int, payment_method: int = METHOD_SBP) -> dict | None:
    merchant_id = os.getenv("PLATEGA_MERCHANT_ID")
    secret = os.getenv("PLATEGA_SECRET")
    if not merchant_id or not secret:
        print("[Platega] PLATEGA_MERCHANT_ID or PLATEGA_SECRET not set")
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{PLATEGA_BASE}/transaction/process",
                headers={
                    "X-MerchantId": merchant_id,
                    "X-Secret": secret,
                    "Content-Type": "application/json",
                },
                json={
                    "paymentMethod": payment_method,
                    "paymentDetails": {"amount": amount, "currency": "RUB"},
                    "description": "Пополнение баланса FishVPN",
                    "payload": f"{tg_id}:{amount}",
                },
            ) as resp:
                data = await resp.json()
                print(f"[Platega] create response {resp.status}: {data}")
                if resp.status == 200:
                    # /transaction/process возвращает redirect, /v2/ возвращает url
                    if not data.get('url') and data.get('redirect'):
                        data['url'] = data['redirect']
                    return data
    except Exception as e:
        print(f"[Platega] request error: {e}")
    return None
