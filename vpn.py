import base64
import datetime
import json
import os
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

import aiohttp
from aiohttp import web
from dotenv import load_dotenv


load_dotenv()

DB_DIR = os.getenv("DB_DIR", ".")
USERS_DB = os.path.join(DB_DIR, "users.db")
PUBLIC_SUB_URL = os.getenv("PUBLIC_SUB_URL", "").rstrip("/")
SUB_PORT = int(os.getenv("SUB_PORT", "8080"))


@dataclass
class XuiNode:
    key: str
    name: str
    profile_name: str
    panel_url: str
    username: str
    password: str
    inbound_id: int
    sub_base_url: str
    host: str
    port: int
    network: str
    security: str
    public_key: str
    short_id: str
    sni: str
    fingerprint: str
    flow: str
    spider_x: str
    path: str


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "y", "on")


def _node_sub_base(panel_url: str) -> str:
    parts = urlsplit(panel_url.rstrip("/"))
    return urlunsplit((parts.scheme, parts.netloc, "/sub", "", "")).rstrip("/")


def _int_env(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _load_nodes() -> list[XuiNode]:
    nodes = []
    for idx in range(1, 10):
        prefix = f"VPN_NODE_{idx}_"
        panel_url = os.getenv(prefix + "PANEL", "").rstrip("/")
        host = os.getenv(prefix + "HOST", "").strip()
        if not panel_url and not host:
            continue
        name = os.getenv(prefix + "NAME", f"Node {idx}")
        default_sub_base = _node_sub_base(panel_url) if panel_url else ""
        nodes.append(
            XuiNode(
                key=os.getenv(prefix + "KEY", str(idx)),
                name=name,
                profile_name=os.getenv(prefix + "PROFILE_NAME", name),
                panel_url=panel_url,
                username=os.getenv(prefix + "USERNAME", ""),
                password=os.getenv(prefix + "PASSWORD", ""),
                inbound_id=_int_env(prefix + "INBOUND_ID"),
                sub_base_url=os.getenv(prefix + "SUB_BASE_URL", default_sub_base).rstrip("/"),
                host=host,
                port=_int_env(prefix + "PORT"),
                network=os.getenv(prefix + "NETWORK", "tcp").strip().lower(),
                security=os.getenv(prefix + "SECURITY", "reality").strip().lower(),
                public_key=os.getenv(prefix + "PUBLIC_KEY", "").strip(),
                short_id=os.getenv(prefix + "SHORT_ID", "").strip(),
                sni=os.getenv(prefix + "SNI", "").strip(),
                fingerprint=os.getenv(prefix + "FINGERPRINT", os.getenv("VPN_FINGERPRINT", "chrome")).strip(),
                flow=os.getenv(prefix + "FLOW", os.getenv("VPN_FLOW", "xtls-rprx-vision")).strip(),
                spider_x=os.getenv(prefix + "SPIDER_X", "/").strip(),
                path=os.getenv(prefix + "PATH", "/").strip(),
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
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS vpn_node_accounts (
                tg_id INTEGER NOT NULL,
                node_key TEXT NOT NULL,
                uuid TEXT NOT NULL,
                email TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(tg_id, node_key)
            )
            """
        )


def _normalize_tg_username(tg_id: int, username: str | None = None) -> str:
    username = (username or _get_stored_username(tg_id) or "").strip()
    if username.startswith("@"):
        username = username[1:]
    return username or str(tg_id)


def _get_stored_username(tg_id: int) -> str | None:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT username FROM users WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
    return row[0] if row and row[0] else None


def _vpn_identity(tg_id: int, username: str | None = None) -> dict[str, str]:
    return {
        "uuid": str(tg_id),
        "email": _normalize_tg_username(tg_id, username),
    }


def _first_short_id(short_ids: str) -> str:
    return next((short_id.strip() for short_id in short_ids.split(",") if short_id.strip()), "")


def _get_or_create_account(tg_id: int, username: str | None = None) -> dict[str, str]:
    init_vpn_db()
    identity = _vpn_identity(tg_id, username)
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute("SELECT uuid, email, sub_token FROM vpn_accounts WHERE tg_id = ?", (tg_id,))
        row = cur.fetchone()
        if row:
            account = {"uuid": identity["uuid"], "email": identity["email"], "sub_token": row[2]}
            legacy: dict[str, str] = {}
            if row[0] != account["uuid"]:
                legacy["legacy_uuid"] = row[0]
            if row[1] != account["email"]:
                legacy["legacy_email"] = row[1]
            if legacy:
                cur.execute(
                    "UPDATE vpn_accounts SET uuid = ?, email = ? WHERE tg_id = ?",
                    (account["uuid"], account["email"], tg_id),
                )
                account.update(legacy)
            return account

        account = {
            "uuid": identity["uuid"],
            "email": identity["email"],
            "sub_token": secrets.token_urlsafe(24),
        }
        cur.execute(
            "INSERT INTO vpn_accounts (tg_id, uuid, email, sub_token) VALUES (?, ?, ?, ?)",
            (tg_id, account["uuid"], account["email"], account["sub_token"]),
        )
        return account


def _get_or_create_node_account(tg_id: int, node: XuiNode, username: str | None = None) -> dict[str, str]:
    base = _get_or_create_account(tg_id, username)
    init_vpn_db()
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute(
            "SELECT uuid, email FROM vpn_node_accounts WHERE tg_id = ? AND node_key = ?",
            (tg_id, node.key),
        )
        row = cur.fetchone()
        if row:
            account = {
                "uuid": base["uuid"],
                "email": base["email"],
                "sub_token": base["sub_token"],
            }
            legacy: dict[str, str] = {}
            if row[0] != account["uuid"]:
                legacy["legacy_uuid"] = row[0]
            if row[1] != account["email"]:
                legacy["legacy_email"] = row[1]
            if legacy:
                cur.execute(
                    """
                    UPDATE vpn_node_accounts
                    SET uuid = ?, email = ?
                    WHERE tg_id = ? AND node_key = ?
                    """,
                    (account["uuid"], account["email"], tg_id, node.key),
                )
                account.update(legacy)
            return account

        cur.execute(
            """
            INSERT INTO vpn_node_accounts (tg_id, node_key, uuid, email)
            VALUES (?, ?, ?, ?)
            """,
            (tg_id, node.key, base["uuid"], base["email"]),
        )
        return {
            "uuid": base["uuid"],
            "email": base["email"],
            "sub_token": base["sub_token"],
        }


def get_subscription_url(tg_id: int, username: str | None = None) -> str:
    account = _get_or_create_account(tg_id, username)
    return f"{PUBLIC_SUB_URL}/sub/{account['sub_token']}"


def get_happ_activation_url(tg_id: int, username: str | None = None) -> str:
    sub_url = get_subscription_url(tg_id, username)
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
        if not self.node.panel_url or not self.node.username or not self.node.password or not self.node.inbound_id:
            raise RuntimeError(f"{self.node.name}: 3x-ui panel is not fully configured")
        async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
            await self._login(session)
            inbound_data = await self._request(session, "GET", f"/panel/api/inbounds/get/{self.node.inbound_id}")
            inbound = _extract_obj(inbound_data)
            settings = _parse_json_field(inbound.get("settings"))
            clients = settings.get("clients", [])
            existing = next(
                (
                    client
                    for client in clients
                    if client.get("email") == account["email"]
                    or client.get("id") == account["uuid"]
                    or client.get("email") == account.get("legacy_email")
                    or client.get("id") == account.get("legacy_uuid")
                    or client.get("subId") == account["sub_token"]
                ),
                None,
            )
            flow = self.node.flow or (existing or clients[0] if clients else {}).get("flow", "")
            client = _client_payload(account, tg_id, end_date, flow=flow)
            body = {"id": self.node.inbound_id, "settings": json.dumps({"clients": [client]})}

            if existing:
                client_id = existing.get("id") or account["uuid"]
                await self._request(session, "POST", f"/panel/api/inbounds/updateClient/{client_id}", json=body)
            else:
                await self._request(session, "POST", "/panel/api/inbounds/addClient", json=body)


async def ensure_vpn_account(tg_id: int, end_date: datetime.datetime, username: str | None = None) -> str:
    if not PUBLIC_SUB_URL:
        raise RuntimeError("PUBLIC_SUB_URL is not set")
    _get_or_create_account(tg_id, username)
    nodes = _load_nodes()
    if not nodes:
        raise RuntimeError("VPN nodes are not configured")
    for node in nodes:
        node_account = _get_or_create_node_account(tg_id, node, username)
        if node.panel_url and node.username and node.password and node.inbound_id:
            await XuiClient(node).sync_client(tg_id, node_account, end_date)
        elif not _build_node_link(node, node_account):
            raise RuntimeError(f"{node.name}: 3x-ui panel is not configured and direct link fields are incomplete")
    return get_subscription_url(tg_id, username)


def _build_node_link(node: XuiNode, account: dict[str, str]) -> str | None:
    short_id = _first_short_id(node.short_id)
    if not node.host or not node.port or not node.public_key or not short_id:
        return None

    params = {
        "type": node.network,
        "encryption": "none",
        "security": node.security,
    }
    if node.security == "reality":
        params.update(
            {
                "pbk": node.public_key,
                "fp": node.fingerprint,
                "sni": node.sni,
                "sid": short_id,
            }
        )
        if node.flow:
            params["flow"] = node.flow
        if node.network == "tcp" and node.spider_x:
            params["spx"] = node.spider_x
    if node.network in ("xhttp", "splithttp") and node.path:
        params["path"] = node.path

    query = urlencode({k: v for k, v in params.items() if v}, quote_via=quote)
    label = quote(node.profile_name, safe="")
    return f"vless://{account['uuid']}@{node.host}:{node.port}?{query}#{label}"


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
    active, tg_id = _user_is_active_by_token(token)
    if not active or tg_id is None:
        return ""
    nodes = _load_nodes()
    links: list[str] = []
    fetch_nodes: list[XuiNode] = []
    for node in nodes:
        account = _get_or_create_node_account(tg_id, node)
        link = _build_node_link(node, account)
        if link:
            links.append(link)
        elif node.sub_base_url:
            fetch_nodes.append(node)

    async with aiohttp.ClientSession() as session:
        for node in fetch_nodes:
            links.extend(await _fetch_node_links(session, node, token))

    deduped_links = list(dict.fromkeys(links))
    return base64.b64encode("\n".join(deduped_links).encode()).decode()


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
