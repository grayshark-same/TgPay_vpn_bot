#!/usr/bin/env python3
"""Proverka VPN-nodov iz .env.

Reality-server na korrektnyy SNI namerenno "zavisaet" (zaschita ot probing),
a na chuzhoy SNI - proksiruet na dest i otdaet ego nastoyaschiy sertifikat.
Poetomu kazhdyy nod probuem DVAZHDY i sudim po pare otvetov, a ne po odnomu.

Zapuskat s mashiny s nastoyaschey setyu (luchshe - iz Ukrainy).
    python3 check_nodes.py
"""
import io
import re
import socket
import ssl
import sys
import time

CONTROL = ("192.0.2.1", 443)   # RFC 5737 TEST-NET-1: dolzhen byt nedostupen


def load_nodes(path=".env"):
    env = {}
    for line in io.open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"')

    ids = sorted({int(m.group(1)) for k in env if (m := re.match(r"VPN_NODE_(\d+)_HOST$", k))})
    nodes = []
    for i in ids:
        g = lambda s: env.get(f"VPN_NODE_{i}_{s}", "")
        if g("HOST") and g("PORT"):
            nodes.append({"id": i, "name": g("NAME") or g("KEY") or f"node{i}",
                          "host": g("HOST"), "port": int(g("PORT")),
                          "sni": g("SNI"), "net": g("NETWORK")})
    return nodes


def tls_probe(host, port, sni, timeout=8):
    """-> (state, detail, ms); state: 'cert' | 'stall' | 'down'"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    t = time.time()
    try:
        with socket.create_connection((host, port), timeout) as raw:
            with ctx.wrap_socket(raw, server_hostname=sni) as s:
                return "cert", f"{s.version()} cert {len(s.getpeercert(True))}B", (time.time() - t) * 1000
    except (socket.timeout, ssl.SSLError) as e:
        kind = "stall" if isinstance(e, socket.timeout) else "down"
        return kind, f"{type(e).__name__}: {str(e)[:45]}", (time.time() - t) * 1000
    except Exception as e:
        return "down", f"{type(e).__name__}: {str(e)[:45]}", (time.time() - t) * 1000


def verdict(match_state, neutral_state):
    if neutral_state == "cert" and match_state == "stall":
        return "OK", "Reality otvechaet pravilno (stall na svoy SNI, dest dostupen)"
    if neutral_state == "cert" and match_state == "cert":
        return "WARN", "oba SNI dayut sert - Reality ne stallit, proverit na servere"
    if neutral_state == "cert":
        return "WARN", "dest dostupen, no svoy SNI daet oshibku"
    if match_state == "cert":
        return "WARN", "svoy SNI otvechaet, no chuzhoy ne probrasyvaetsya na dest"
    if match_state == "stall" or neutral_state == "stall":
        return "WARN", "otvechaet, no dest nedostupen s servera"
    return "DOWN", "nod ne otvechaet ni na odin SNI"


def main():
    state, _, _ = tls_probe(CONTROL[0], CONTROL[1], "example.com", timeout=6)
    if state == "cert":
        print(f"!! KONTROL PROVALEN: {CONTROL[0]} otvetil na TLS - set perehvatyvaet")
        print("!! Rezultaty nizhe nedostoverny.\n")
    else:
        print(f"kontrol OK ({CONTROL[0]} nedostupen, kak i dolzhno byt)\n")

    nodes = load_nodes()
    if not nodes:
        print("Nodov v .env ne naydeno")
        return 1

    bad = 0
    for n in nodes:
        sni = n["sni"] or n["host"]
        m_state, m_info, m_ms = tls_probe(n["host"], n["port"], sni)
        # Neytralnyy SNI = sam IP: Reality ne naydet ego v serverNames
        # i probrosit na dest, otdav ego nastoyaschiy sertifikat.
        u_state, u_info, u_ms = tls_probe(n["host"], n["port"], n["host"])
        mark, why = verdict(m_state, u_state)
        if mark != "OK":
            bad += 1
        print(f"[{n['id']:>2}] {n['name']:<20} {n['host']}:{n['port']:<5} {n['net']:<5} {mark:<4} {why}")
        print(f"     svoy SNI [{sni}]: {m_state} {m_ms:.0f}ms | chuzhoy SNI: {u_state} {u_ms:.0f}ms")

    print(f"\nItogo: {len(nodes) - bad}/{len(nodes)} nodov bez zamechaniy")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
