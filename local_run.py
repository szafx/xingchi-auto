#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""星驰公益站 每日签到 / 转盘抽奖 / 盲盒 本地执行器（也是 Cloudflare Worker 的逻辑原型）

用法：
    python local_run.py                 # 用 session.json 里的令牌跑一遍
    python local_run.py --dry-run       # 只看状态，不写操作
    LCS_SESSION=... python local_run.py # 直接给 access token

产出：把运行结果写进 state.json，并打印一份人类可读摘要。
"""
import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
SESSION_FILE = pathlib.Path(os.environ.get(
    "XC_SESSION_FILE",
    r"C:\Users\szafx\Desktop\CodeSauce\projects\2026-11-19_xingchi-jb-ios16.2\work\session.json"))
STATE_FILE = HERE / "state.json"

CONSOLE = "https://api.xingchiapi.com/api/v1"
RELAY = "https://api.xingchiapi.com/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"


def http(method, url, token=None, body=None, timeout=30):
    data = None
    headers = {"Accept": "application/json", "User-Agent": UA}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw[:500]}
    except Exception as e:
        return 0, {"error": str(e)}


def load_tokens():
    tok = os.environ.get("LCS_SESSION", "").strip()
    ref = os.environ.get("LCS_REFRESH", "").strip()
    if tok:
        return tok, ref
    if SESSION_FILE.exists():
        d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        st = d.get("storage", d)
        return st.get("auth_token", ""), st.get("refresh_token", "")
    return "", ""


def save_tokens(access, refresh):
    if not SESSION_FILE.exists():
        return
    d = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
    st = d.setdefault("storage", {})
    if access:
        st["auth_token"] = access
    if refresh:
        st["refresh_token"] = refresh
    SESSION_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    access, refresh = load_tokens()
    if not access:
        print("[x] 没有令牌：先跑 login 脚本，或设置 LCS_SESSION")
        return 2

    out = {"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "steps": []}

    def step(name, status, payload=None):
        rec = {"name": name, "status": status, "payload": payload}
        out["steps"].append(rec)
        print("\n== %s => %s" % (name, status))
        if payload is not None:
            print(json.dumps(payload, ensure_ascii=False)[:900])
        return payload

    # 1) 刷新令牌（旋转式：refresh_token 也会换新）
    st, r = http("POST", CONSOLE + "/auth/refresh", token=access, body={"refresh_token": refresh})
    if st == 200 and r.get("data", {}).get("access_token"):
        access = r["data"]["access_token"]
        refresh = r["data"].get("refresh_token", refresh)
        save_tokens(access, refresh)
        step("refresh", 200, {"expires_in": r["data"].get("expires_in")})
    else:
        step("refresh", st, r)

    # 2) 签到
    st, r = http("GET", CONSOLE + "/user/checkin", token=access)
    info = (r.get("data") or {}) if st == 200 else {}
    step("checkin.status", st, info)

    if st == 200 and info.get("enabled") and not info.get("claimed_today") and not args.dry_run:
        st2, r2 = http("POST", CONSOLE + "/user/checkin", token=access, body={})
        step("checkin.claim", st2, r2)
    elif st == 200 and info.get("claimed_today"):
        step("checkin.claim", 0, {"skipped": "already claimed today", "next": info.get("next_claim_at")})

    # 3) 转盘
    st, r = http("GET", CONSOLE + "/lottery", token=access)
    lot = (r.get("data") or {}) if st == 200 else {}
    step("lottery.status", st, {k: lot.get(k) for k in
                               ("enabled", "available_tickets", "available_blind_boxes",
                                "consume_per_ticket", "daily_limit", "draws_today", "reset_at")})

    draws = []
    if st == 200 and lot.get("enabled") and not args.dry_run:
        tickets = int(lot.get("available_tickets") or 0)
        for i in range(min(tickets, 20)):
            st2, r2 = http("POST", CONSOLE + "/lottery/draw", token=access, body={})
            draws.append({"status": st2, "resp": r2})
            print("   draw#%d -> %s %s" % (i + 1, st2, json.dumps(r2, ensure_ascii=False)[:160]))
            if st2 != 200:
                break
            time.sleep(1.2)
    out["draws"] = draws

    # 4) 盲盒
    st, r = http("GET", CONSOLE + "/lottery/blind-box", token=access)
    box = (r.get("data") or {}) if st == 200 else {}
    step("blindbox.status", st, {k: box.get(k) for k in ("available_blind_boxes", "enabled") if k in box} or box)

    boxes = int(box.get("available_blind_boxes") or 0) if isinstance(box, dict) else 0
    opened = []
    if boxes and not args.dry_run:
        for i in range(min(boxes, 10)):
            st2, r2 = http("POST", CONSOLE + "/lottery/blind-box/open", token=access, body={})
            opened.append({"status": st2, "resp": r2})
            print("   box#%d -> %s %s" % (i + 1, st2, json.dumps(r2, ensure_ascii=False)[:160]))
            if st2 != 200:
                break
    out["blind_boxes"] = opened

    # 5) 福利（消费返利阶梯）
    st, r = http("GET", CONSOLE + "/user/welfare", token=access)
    step("welfare", st, (r.get("data") or r))

    # 6) 余额（用 sk 走 relay，不需要控制台令牌）
    sk = os.environ.get("XC_API_KEY", "")
    if not sk:
        cfgp = HERE / "state.json"
        try:
            sk = json.loads(cfgp.read_text(encoding="utf-8")).get("api_key", "")
        except Exception:
            sk = ""
    if sk:
        st, r = http("GET", RELAY + "/usage", token=sk)
        step("balance", st, {k: r.get(k) for k in ("balance", "remaining", "unit", "isValid") if k in r})
        out["balance"] = r.get("remaining") or r.get("balance")

    out["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out["ok"] = True
    STATE_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n[✓] 完成，结果已写入 %s" % STATE_FILE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
