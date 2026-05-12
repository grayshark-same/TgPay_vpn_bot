import base64
import datetime
import json
import os
import secrets
import sqlite3
import uuid as _uuid
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
    flag: str
    fixed_uuid: str


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
                flag=os.getenv(prefix + "FLAG", "").strip(),
                fixed_uuid=os.getenv(prefix + "FIXED_UUID", "").strip(),
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


_FISHVPN_NS = _uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _vpn_identity(tg_id: int, username: str | None = None) -> dict[str, str]:
    return {
        "uuid": str(_uuid.uuid5(_FISHVPN_NS, str(tg_id))),
        "email": _normalize_tg_username(tg_id, username),
    }


def _first_short_id(short_ids: str) -> str:
    return next((short_id.strip() for short_id in short_ids.split(",") if short_id.strip()), "")


def _resolve_uuid(node: "XuiNode", account: dict[str, str]) -> str:
    return node.fixed_uuid if node.fixed_uuid else account["uuid"]


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


async def _get_crypt5_url(sub_url: str) -> str:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://crypto.happ.su/api-v2.php",
                json={"url": sub_url},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    print(f"[crypt5] API response: {data}")
                    for field in ("link", "url", "href", "result", "encrypted"):
                        link = data.get(field, "")
                        if isinstance(link, str) and link.startswith("happ://"):
                            return link
    except Exception as e:
        print(f"[crypt5] {e}")
    return f"happ://add/{sub_url}"


async def get_happ_activation_url(tg_id: int, username: str | None = None) -> str:
    sub_url = get_subscription_url(tg_id, username)
    happ_url = await _get_crypt5_url(sub_url)
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
                raise RuntimeError(f"{self.node.name}: {resp.status} {method} {url} — {text[:300]}")
            if not text:
                return {}
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def _login(self, session: aiohttp.ClientSession) -> None:
        payload = {"username": self.node.username, "password": self.node.password}
        try:
            data = await self._request(session, "POST", "/login", json=payload)
            if data.get("success") is not False:
                return
        except RuntimeError:
            pass
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
        elif not _build_node_link(node, node_account) and not _build_node_json_config(node, node_account):
            raise RuntimeError(f"{node.name}: 3x-ui panel is not configured and direct link fields are incomplete")
    return get_subscription_url(tg_id, username)


def _build_node_link(node: XuiNode, account: dict[str, str]) -> str | None:
    if not node.host or not node.port:
        return None

    params = {
        "type": node.network,
        "encryption": "none",
        "security": node.security,
    }
    if node.security == "reality":
        short_id = _first_short_id(node.short_id)
        if not node.public_key or not short_id:
            return None
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
    elif node.security == "tls":
        params.update({"fp": node.fingerprint, "sni": node.sni})
        if node.flow:
            params["flow"] = node.flow

    if node.network in ("ws", "websocket"):
        if node.path:
            params["path"] = node.path
        if node.sni:
            params["host"] = node.sni
    elif node.network in ("xhttp", "splithttp") and node.path:
        params["path"] = node.path

    query = urlencode({k: v for k, v in params.items() if v}, quote_via=quote)
    display_name = f"{node.flag} {node.profile_name}" if node.flag else node.profile_name
    label = quote(display_name, safe="")
    return f"vless://{_resolve_uuid(node, account)}@{node.host}:{node.port}?{query}#{label}"


def _build_node_json_config(node: XuiNode, account: dict[str, str]) -> dict | None:
    if not node.host or not node.port:
        return None
    if node.security == "reality" and not node.public_key:
        return None
    short_id = _first_short_id(node.short_id)
    display_name = f"{node.flag} {node.profile_name}" if node.flag else node.profile_name
    proxy: dict[str, Any] = {
        "protocol": "vless",
        "tag": "proxy",
        "settings": {
            "vnext": [
                {
                    "address": node.host,
                    "port": node.port,
                    "users": [
                        {
                            "id": _resolve_uuid(node, account),
                            "flow": node.flow or "",
                            "encryption": "none",
                            "level": 0,
                        }
                    ],
                }
            ]
        },
        "streamSettings": {
            "network": node.network or "tcp",
            "security": node.security or "none",
        },
    }
    if node.security == "reality":
        proxy["streamSettings"]["realitySettings"] = {
            "serverName": node.sni,
            "fingerprint": node.fingerprint or "chrome",
            "publicKey": node.public_key,
            "shortId": short_id,
            "spiderX": node.spider_x or "/",
        }
    elif node.security == "tls":
        proxy["streamSettings"]["tlsSettings"] = {
            "serverName": node.sni,
            "allowInsecure": False,
            "fingerprint": node.fingerprint or "chrome",
            "alpn": ["http/1.1"],
        }
    net = node.network or "tcp"
    if net in ("ws", "websocket"):
        ws: dict[str, Any] = {"path": node.path or "/"}
        if node.sni:
            ws["headers"] = {"Host": node.sni}
        proxy["streamSettings"]["wsSettings"] = ws
    elif net in ("xhttp", "splithttp"):
        proxy["streamSettings"]["xhttpSettings"] = {"path": node.path or "/"}
    return {
        "remarks": display_name,
        "log": {"loglevel": "warning"},
        "dns": {
            "queryStrategy": "UseIP",
            "servers": ["1.1.1.1", "1.0.0.1", "8.8.8.8"],
        },
        "inbounds": [
            {
                "listen": "127.0.0.1",
                "port": 10808,
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
                "sniffing": {
                    "destOverride": ["http", "tls", "quic"],
                    "enabled": True,
                    "routeOnly": False,
                },
                "tag": "socks",
            },
            {
                "listen": "127.0.0.1",
                "port": 10809,
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": {
                    "destOverride": ["http", "tls", "quic"],
                    "enabled": True,
                    "routeOnly": False,
                },
                "tag": "http",
            },
        ],
        "outbounds": [
            proxy,
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
        ],
        "routing": {
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {"outboundTag": "direct", "protocol": ["bittorrent"], "type": "field"},
            ],
        },
    }


async def build_json_subscription(token: str) -> list[dict] | None:
    active, tg_id = _user_is_active_by_token(token)
    if not active or tg_id is None:
        return None
    nodes = _load_nodes()
    configs: list[dict] = []
    for node in nodes:
        account = _get_or_create_node_account(tg_id, node)
        cfg = _build_node_json_config(node, account)
        if cfg:
            configs.append(cfg)
    return configs if configs else None


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


def _get_sub_info_by_token(token: str) -> dict | None:
    with sqlite3.connect(USERS_DB) as db:
        cur = db.cursor()
        cur.execute(
            """
            SELECT users.end_of_sub, users.username
            FROM vpn_accounts
            JOIN users ON users.tg_id = vpn_accounts.tg_id
            WHERE vpn_accounts.sub_token = ?
            """,
            (token,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"end_of_sub": row[0], "username": row[1]}


async def handle_subscription(request: web.Request) -> web.Response:
    token = request.match_info["token"]
    headers: dict[str, str] = {}
    info = _get_sub_info_by_token(token)
    if info:
        username = (info["username"] or "").strip() or "User"
        headers["profile-title"] = f"FishVPN 🎣 | {username[:20]}"
        headers["profile-update-interval"] = "6"
        headers["support-url"] = os.getenv("SUPPORT_URL", "https://t.me/FishVPN_info")
        if info["end_of_sub"]:
            try:
                expire = int(
                    datetime.datetime.strptime(info["end_of_sub"], "%Y-%m-%d %H:%M:%S").timestamp()
                )
                headers["subscription-userinfo"] = f"upload=0; download=0; total=0; expire={expire}"
            except Exception:
                pass
    json_configs = await build_json_subscription(token)
    if json_configs is not None:
        body = json.dumps(json_configs, ensure_ascii=False)
        return web.Response(text=body, content_type="application/json", headers=headers)
    body = await build_merged_subscription(token)
    return web.Response(text=body, content_type="text/plain", headers=headers)


async def handle_redirect(request: web.Request) -> web.Response:
    target = request.query.get("to", "")
    if not target.startswith("happ://"):
        return web.Response(status=400, text="bad redirect")
    html = f"<script>window.location.href='{target}';</script>"
    return web.Response(text=html, content_type="text/html")


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
