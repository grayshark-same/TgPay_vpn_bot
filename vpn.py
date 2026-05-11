import base64
import datetime
import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import aiohttp
from aiohttp import web
from dotenv import load_dotenv


load_dotenv()

DB_DIR = os.getenv("DB_DIR", ".")
USERS_DB = os.path.join(DB_DIR, "users.db")
PUBLIC_SUB_URL = os.getenv("PUBLIC_SUB_URL", "").rstrip("/")
SUB_PORT = int(os.getenv("SUB_PORT", os.getenv("WEBHOOK_PORT", 8090)))


@dataclass
class XuiNode:
    name: str
    panel_url: str
    username: str
    password: str
    inbound_id: int
    sub_base_url: str


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _node_sub_base(panel_url: str) -> str:
    parts = urlsplit(panel_url.rstrip("/"))
    return urlunsplit((parts.scheme, parts.netloc, "/sub", "", "")).rstrip("/")


def _load_nodes() -> list[XuiNode]:
    nodes = []
    for idx in range(1, 10):
        prefix = f"VPN_NODE_{idx}_"
        panel_url = os.getenv(prefix + "PANEL", "").rstrip("/")
        if not panel_url:
            continue
        nodes.append(
            XuiNode(
                name=os.getenv(prefix + "NAME", f"Node {idx}"),
                panel_url=panel_url,
                username=os.getenv(prefix + "USERNAME", ""),
                password=os.getenv(prefix + "PASSWORD", ""),
                inbound_id=int(os.getenv(prefix + "INBOUND_ID", "0")),
                sub_base_url=os.getenv(prefix + "SUB_BASE_URL", _node_sub_base(panel_url)).rstrip("/"),
            )
        )
    return nodes


def init_vpn_db() -> None:
    with sqlite3.connect(USERS_DB) as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS vpn_accounts (
                tg_id INTEGER UNIQUE NOT NULL,
                uuid TEXT NOT NULL,
                email TEXT NOT NULL,
                sub_token TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )


def _get_or_create_account(tg_id: int) -> dict[str, str]:
    init_vpn_db()
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT uuid, email, sub_token FROM vpn_accounts WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if row:
            return {"uuid": row[0], "email": row[1], "sub_token": row[2]}

        account = {
            "uuid": str(uuid.uuid4()),
            "email": f"tg_{tg_id}",
            "sub_token": secrets.token_urlsafe(24),
        }
        cur.execute(
            "INSERT INTO vpn_accounts (tg_id, uuid, email, sub_token) VALUES (?, ?, ?, ?)",
            (tg_id, account["uuid"], account["email"], account["sub_token"]),
        )
        return account


def get_subscription_url(tg_id: int) -> str:
    account = _get_or_create_account(tg_id)
    return f"{PUBLIC_SUB_URL}/sub/{account['sub_token']}"


def get_happ_activation_url(tg_id: int) -> str:
    sub_url = get_subscription_url(tg_id)
    encoded_sub_url = quote(base64.b64encode(sub_url.encode()).decode(), safe="")
    happ_url = f"happ://crypt3/{encoded_sub_url}"
    return f"{PUBLIC_SUB_URL}/redirect?to={quote(happ_url, safe='')}"


def _user_is_active_by_token(token: str) -> tuple[bool, int | None]:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT users.tg_id, users.end_of_sub
            FROM vpn_accounts
            JOIN users ON users.tg_id = vpn_accounts.tg_id
            WHERE vpn_accounts.sub_token = ?
            """,
            (token,),
        )
        row = cur.fetchone()
    if not row or not row[1]:
        return False, None
    end_date = datetime.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
    return end_date > datetime.datetime.now(), int(row[0])


def _parse_json_field(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value or "{}")
    return value or {}


def _extract_obj(data: dict[str, Any]) -> dict[str, Any]:
    obj = data.get("obj", data)
    if not isinstance(obj, dict):
        raise RuntimeError(f"Unexpected 3x-ui response: {data}")
    return obj


def _client_payload(account: dict[str, str], tg_id: int, end_date: datetime.datetime, flow: str = "") -> dict[str, Any]:
    return {
        "id": account["uuid"],
        "flow": flow,
        "email": account["email"],
        "limitIp": 3,
        "totalGB": 0,
        "expiryTime": int(end_date.timestamp() * 1000),
        "enable": True,
        "tgId": str(tg_id),
        "subId": account["sub_token"],
    }


class XuiClient:
    def __init__(self, node: XuiNode):
        self.node = node
        self.verify_ssl = _bool_env("VPN_VERIFY_SSL", False)

    async def _request(self, session: aiohttp.ClientSession, method: str, path: str, **kwargs) -> Any:
        url = f"{self.node.panel_url}{path}"
        async with session.request(method, url, ssl=self.verify_ssl, **kwargs) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise RuntimeError(f"{self.node.name}: {resp.status} {text[:300]}")
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def _login(self, session: aiohttp.ClientSession) -> None:
        payload = {"username": self.node.username, "password": self.node.password}
        data = await self._request(session, "POST", "/login", json=payload)
        if data.get("success") is False:
            data = await self._request(session, "POST", "/login", data=payload)
        if data.get("success") is False:
            raise RuntimeError(f"{self.node.name}: login failed: {data.get('msg')}")

    async def sync_client(self, tg_id: int, account: dict[str, str], end_date: datetime.datetime) -> None:
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            await self._login(session)
            inbound_data = await self._request(session, "GET", f"/panel/api/inbounds/get/{self.node.inbound_id}")
            inbound = _extract_obj(inbound_data)
            settings = _parse_json_field(inbound.get("settings"))
            clients = settings.get("clients", [])
            existing = next(
                (client for client in clients if client.get("email") == account["email"] or client.get("id") == account["uuid"]),
                None,
            )
            flow = (existing or clients[0] if clients else {}).get("flow", "")
            client = _client_payload(account, tg_id, end_date, flow=flow)
            body = {"id": self.node.inbound_id, "settings": json.dumps({"clients": [client]})}

            if existing:
                client_id = existing.get("id") or account["uuid"]
                await self._request(session, "POST", f"/panel/api/inbounds/updateClient/{client_id}", json=body)
            else:
                await self._request(session, "POST", "/panel/api/inbounds/addClient", json=body)


async def ensure_vpn_account(tg_id: int, end_date: datetime.datetime) -> str:
    if not PUBLIC_SUB_URL:
        raise RuntimeError("PUBLIC_SUB_URL is not set")
    account = _get_or_create_account(tg_id)
    nodes = _load_nodes()
    if not nodes:
        raise RuntimeError("VPN nodes are not configured")
    for node in nodes:
        await XuiClient(node).sync_client(tg_id, account, end_date)
    return get_subscription_url(tg_id)


def _decode_subscription(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if "://" in text:
        return [line.strip() for line in text.splitlines() if line.strip() and "://" in line]
    try:
        padded = text + "=" * (-len(text) % 4)
        decoded = base64.b64decode(padded).decode()
        return [line.strip() for line in decoded.splitlines() if line.strip() and "://" in line]
    except Exception:
        return []


async def _fetch_node_links(session: aiohttp.ClientSession, node: XuiNode, sub_token: str) -> list[str]:
    url = f"{node.sub_base_url}/{sub_token}"
    async with session.get(url, ssl=_bool_env("VPN_VERIFY_SSL", False)) as resp:
        if resp.status != 200:
            print(f"[sub] {node.name} returned {resp.status}")
            return []
        return _decode_subscription(await resp.text())


async def build_merged_subscription(token: str) -> str:
    active, _ = _user_is_active_by_token(token)
    if not active:
        return ""
    nodes = _load_nodes()
    async with aiohttp.ClientSession() as session:
        links: list[str] = []
        for node in nodes:
            links.extend(await _fetch_node_links(session, node, token))
    return base64.b64encode("\n".join(links).encode()).decode()


async def handle_subscription(request: web.Request) -> web.Response:
    body = await build_merged_subscription(request.match_info["token"])
    return web.Response(text=body, content_type="text/plain")


async def handle_redirect(request: web.Request) -> web.Response:
    target = request.query.get("to", "")
    if not target.startswith("happ://"):
        return web.Response(status=400, text="bad redirect")
    raise web.HTTPFound(target)


async def handle_health(_: web.Request) -> web.Response:
    return web.Response(text="ok", content_type="text/plain")


async def start_subscription_server() -> web.AppRunner:
    init_vpn_db()
    app = web.Application()
    app.router.add_get("/sub/{token}", handle_subscription)
    app.router.add_get("/redirect", handle_redirect)
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", SUB_PORT)
    await site.start()
    print(f"[sub] server started on 0.0.0.0:{SUB_PORT}")
    return runner
