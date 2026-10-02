#!/usr/bin/env python3
"""Podnyatie kievskogo UA-noda ot nachala do kontsa.

Chto delaet:
  1. gonit deploy_ua_node.sh na servere po ssh
  2. razbiraet napechatannyy im blok VPN_NODE_5_* / VPN_NODE_8_*
  3. pravit lokalnyy .env poklyuchevo (ostalnoe ne trogaet), s bekapom
  4. zapuskaet check_nodes.py

Primer:
    python3 apply_ua_node.py --target root@130.0.238.163
    python3 apply_ua_node.py --target root@130.0.238.163 --dry-run
"""
import argparse
import datetime
import io
import re
import subprocess
import sys

ENV_LINE = re.compile(r"^(VPN_NODE_(?:5|8)_[A-Z_]+)=(.*)$")


def run_deploy(target, script, ssh_opts):
    """Vypolnyaet skript na servere cherez stdin - scp ne nuzhen."""
    body = io.open(script, encoding="utf-8").read()
    cmd = ["ssh"] + ssh_opts + [target, "bash -s"]
    print(f"[1/4] deploy -> {target}", flush=True)
    # encoding yavno: v skripte est emodzi, a Windows po umolchaniyu beret cp1252
    p = subprocess.run(cmd, input=body, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = p.stdout
    sys.stdout.write(out)
    if p.stderr.strip():
        sys.stderr.write(p.stderr)
    if p.returncode != 0:
        sys.exit(f"!! ssh/deploy vernul {p.returncode} - .env ne tronut")
    if "[SUCCESS]" not in out:
        sys.exit("!! v vyvode net [SUCCESS] - Xray ne podnyalsya, .env ne tronut")
    return out


def parse_block(out):
    vals = {}
    for line in out.splitlines():
        m = ENV_LINE.match(line.strip())
        if m:
            vals[m.group(1)] = m.group(2)
    need = {"VPN_NODE_5_HOST", "VPN_NODE_5_PUBLIC_KEY", "VPN_NODE_5_SHORT_ID",
            "VPN_NODE_8_HOST", "VPN_NODE_8_PUBLIC_KEY", "VPN_NODE_8_SHORT_ID"}
    missing = need - vals.keys()
    if missing:
        sys.exit(f"!! v vyvode net klyuchey: {sorted(missing)} - .env ne tronut")
    return vals


def patch_env(path, vals, dry_run):
    # newline="" - inache CRLF-fayl budet perepisan v LF tselikom
    src = io.open(path, encoding="utf-8", newline="").read()
    lines = src.splitlines(keepends=True)
    seen, changed = set(), []

    for i, line in enumerate(lines):
        m = re.match(r"^([A-Z0-9_]+)=(.*?)(\r?\n|$)", line)
        if not m or m.group(1) not in vals:
            continue
        key, old, eol = m.group(1), m.group(2), m.group(3) or "\n"
        seen.add(key)
        if old != vals[key]:
            changed.append((key, old, vals[key]))
            lines[i] = f"{key}={vals[key]}{eol}"

    absent = [k for k in vals if k not in seen]
    print(f"[3/4] .env: {len(changed)} izmeneniy, {len(absent)} novyh klyuchey")
    for key, old, new in changed:
        show = lambda v: (v[:24] + "...") if len(v) > 27 else v
        print(f"      {key}: {show(old)} -> {show(new)}")
    for key in absent:
        print(f"      + {key} (budet dobavlen v konets)")

    if dry_run:
        print("      --dry-run: fayl ne zapisan")
        return

    if absent:
        if lines and not lines[-1].endswith("\n"):
            lines.append("\n")
        lines.append("\n# --- dobavleno apply_ua_node.py ---\n")
        lines += [f"{k}={vals[k]}\n" for k in absent]

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{path}.bak_{stamp}"
    io.open(backup, "w", encoding="utf-8", newline="").write(src)
    io.open(path, "w", encoding="utf-8", newline="").write("".join(lines))
    print(f"      bekap: {backup}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="root@130.0.238.163")
    ap.add_argument("--script", default="deploy_ua_node.sh")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ssh-opt", action="append", default=[],
                    help="dop. flag dlya ssh, naprimer --ssh-opt=-i --ssh-opt=~/.ssh/id_ed25519")
    a = ap.parse_args()

    out = run_deploy(a.target, a.script, a.ssh_opt)
    print("[2/4] razbor vyvoda")
    vals = parse_block(out)
    patch_env(a.env, vals, a.dry_run)

    print("[4/4] proverka")
    return subprocess.run([sys.executable, "check_nodes.py"]).returncode


if __name__ == "__main__":
    sys.exit(main())
