import os
import uuid
import json
import base64
import hashlib
import aiohttp


# --- NicePay (карты РФ) ---

def _nicepay_cfg():
    return {
        "merchant_id": os.getenv("NICEPAY_MERCHANT_ID"),
        "secret": os.getenv("NICEPAY_SECRET"),
    }


async def create_nicepay_payment(amount: int, user_id: int, username: str | None = None) -> dict | None:
    """Создать платёж NicePay. amount в рублях (конвертируется в копейки)."""
    cfg = _nicepay_cfg()
    data = {
        **cfg,
        "order_id": f"{user_id}_{uuid.uuid4().hex[:8]}",
        "amount": amount * 100,
        "currency": "RUB",
        "customer": f"user_{user_id}",
        "success_url": os.getenv("NICEPAY_SUCCESS_URL", ""),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://nicepay.io/public/api/payment", json=data) as resp:
                result = await resp.json()
                print(f"[NicePay] create: {result}")
                return result
    except Exception as e:
        print(f"[NicePay] create error: {e}")
    return None


async def check_nicepay_payment(payment_id: str) -> dict | None:
    """Проверить статус платежа NicePay."""
    cfg = _nicepay_cfg()
    data = {**cfg, "payment": payment_id}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://nicepay.io/public/api/h2hPaymentInfo", json=data) as resp:
                return await resp.json()
    except Exception as e:
        print(f"[NicePay] check error: {e}")
    return None


# --- Cryptomus (крипта) ---

def _cryptomus_sign(data: dict) -> tuple[str, str]:
    api_key = os.getenv("CRYPTOMUS_API_KEY")
    merchant_id = os.getenv("CRYPTOMUS_MERCHANT_ID")
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    sign = hashlib.md5(f"{encoded}{api_key}".encode()).hexdigest()
    return merchant_id, sign


async def create_cryptomus_payment(amount: int, user_id: int, username: str | None = None) -> dict | None:
    """Создать счёт Cryptomus. Возвращает dict с url и uuid."""
    additional = json.dumps({"user_id": user_id, "price": amount, "username": username or ""})
    data = {
        "amount": str(amount),
        "currency": "RUB",
        "order_id": str(uuid.uuid4()),
        "additional_data": additional,
        "url_callback": os.getenv("CRYPTOMUS_CALLBACK_URL", ""),
    }
    merchant_id, sign = _cryptomus_sign(data)
    headers = {"merchant": merchant_id, "sign": sign}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.cryptomus.com/v1/payment", json=data, headers=headers) as resp:
                result = await resp.json()
                print(f"[Cryptomus] create: {result}")
                return result.get("result")
    except Exception as e:
        print(f"[Cryptomus] create error: {e}")
    return None
