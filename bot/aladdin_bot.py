#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ALADDIN GITHUB ACTIONS — TRIDENTE + HOTFIX V4
Motor 24/7 gratuito via GitHub Actions.
Secretos via variables de entorno (GitHub Secrets).
Estado persistido via archivos en el repo.
"""
import os, json, math, time, hmac, hashlib, traceback
from datetime import datetime, timezone
from pathlib import Path
import urllib.request, ssl

import numpy as np

RUN_MODE = "LIVE_GITHUB_ACTIONS"
BASE_DIR = Path(__file__).parent.parent / "state"
STATE_FILE = BASE_DIR / "live_state.json"
TRADES_FILE = BASE_DIR / "live_trades.csv"
HEARTBEAT_FILE = BASE_DIR / "live_heartbeat.txt"
LOG_FILE = BASE_DIR / "run.log"
BASE_DIR.mkdir(parents=True, exist_ok=True)

FEE = 0.001; SLIP = 0.001; INVESTMENT_PCT = 0.95
BASE_URL = "https://api.binance.com"

CONFIGS = [
    {"role":"PRIMARY",  "symbol":"COSUSDT", "tf":"1d","exit":"EMA20_CLOSE_EXIT","stop":-0.07,"trigger":35},
    {"role":"SECONDARY","symbol":"PEPEUSDT","tf":"1d","exit":"TRAIL_7_PCT",     "stop":-0.03,"trigger":40},
    {"role":"RESERVE",  "symbol":"MEMEUSDT","tf":"1d","exit":"TRAIL_7_PCT",     "stop":-0.03,"trigger":40},
]

API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

if not API_KEY or not API_SECRET:
    raise ValueError("BINANCE_API_KEY y BINANCE_API_SECRET deben estar en variables de entorno")

ctx = ssl.create_default_context()

def utc_now(): return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def log(msg):
    line = f"[{utc_now()}] {msg}"; print(line)
    with open(LOG_FILE, "a") as f: f.write(line + "\n")

def http_request(url, method="GET", headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.status, json.loads(resp.read().decode())

def http_post(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {}, method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return resp.status, json.loads(resp.read().decode())

def api_server_time():
    _, d = http_request(f"{BASE_URL}/api/v3/time")
    return d["serverTime"]

def sign_params(params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return f"{qs}&signature={sig}"

def signed_get(path, params):
    params = dict(params); params["timestamp"] = api_server_time(); params["recvWindow"] = 5000
    query = sign_params(params)
    url = f"{BASE_URL}{path}?{query}"
    return http_request(url, headers={"X-MBX-APIKEY": API_KEY})

def signed_post(path, params):
    params = dict(params); params["timestamp"] = api_server_time(); params["recvWindow"] = 5000
    query = sign_params(params)
    url = f"{BASE_URL}{path}?{query}"
    return http_post(url, headers={"X-MBX-APIKEY": API_KEY})

def get_balance(asset):
    c, d = signed_get("/api/v3/account", {})
    if c != 200: raise RuntimeError(f"account_fail({c}): {d}")
    for b in d.get("balances", []):
        if b["asset"] == asset: return float(b["free"])
    return 0.0

def get_symbol_filters(sym):
    _, d = http_request(f"{BASE_URL}/api/v3/exchangeInfo?symbol={sym}")
    info = d["symbols"][0]; ss = mq = mn = 0.0
    for f in info["filters"]:
        ft = f["filterType"]
        if ft == "LOT_SIZE": ss, mq = float(f["stepSize"]), float(f["minQty"])
        elif ft in ("MIN_NOTIONAL", "NOTIONAL"): mn = float(f.get("minNotional", f.get("notional", "0")))
    return ss, mq, mn

def adjust_quantity(qty, step):
    if step <= 0: return qty
    p = int(round(-math.log(step, 10), 0)); return float(math.floor(qty * 10**p) / 10**p)

def market_buy_quote(sym, q):
    return signed_post("/api/v3/order", {"symbol": sym, "side": "BUY", "type": "MARKET", "quoteOrderQty": f"{q:.8f}"})

def market_sell_qty(sym, q):
    return signed_post("/api/v3/order", {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": f"{q:.8f}"})

def get_klines(sym, interval="1d", limit=250):
    for _ in range(3):
        try:
            _, d = http_request(f"{BASE_URL}/api/v3/klines?symbol={sym}&interval={interval}&limit={limit}")
            return d
        except: time.sleep(1)
    raise RuntimeError(f"klines_fail: {sym}")

def spot_price(sym):
    _, d = http_request(f"{BASE_URL}/api/v3/ticker/price?symbol={sym}")
    return float(d["price"])

def calc_indicators(cl, hi, lo):
    n = len(cl)
    ema20 = np.zeros(n); ema20[0] = cl[0]; k = 2.0/21.0
    for i in range(1, n): ema20[i] = cl[i]*k + ema20[i-1]*(1-k)
    rsi = np.full(n, 50.0)
    for i in range(14, n):
        d = np.diff(cl[i-14:i+1]); g = np.mean(np.where(d > 0, d, 0.0))
        ls = max(np.mean(np.where(d < 0, -d, 0.0)), 1e-10)
        rsi[i] = 100.0 - 100.0/(1.0 + g/ls)
    adx = np.full(n, 20.0); tr = np.zeros(n); pdm = np.zeros(n); mdm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
        um, dm = hi[i]-hi[i-1], lo[i-1]-lo[i]
        pdm[i] = um if (um > dm and um > 0) else 0.0
        mdm[i] = dm if (dm > um and dm > 0) else 0.0
    if n > 15:
        atr = np.mean(tr[1:15]); sp = np.mean(pdm[1:15]); sm = np.mean(mdm[1:15])
        pdi = np.zeros(n); mdi = np.zeros(n)
        for i in range(15, n):
            atr = (atr*13+tr[i])/14; sp = (sp*13+pdm[i])/14; sm = (sm*13+mdm[i])/14
            if atr > 0: pdi[i] = 100*sp/atr; mdi[i] = 100*sm/atr
        dx = np.zeros(n)
        for i in range(15, n):
            den = pdi[i]+mdi[i]
            if den > 0: dx[i] = 100*abs(pdi[i]-mdi[i])/den
        if n > 29:
            adx[28] = np.mean(dx[15:29])
            for i in range(29, n): adx[i] = (adx[i-1]*13+dx[i])/14
    return ema20, rsi, adx

def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, "r") as f: return json.load(f)
    return {"active_trade": False, "symbol": "", "role": "", "entry_px": 0.0,
            "qty": 0.0, "invested_usdt": 0.0, "max_px": 0.0, "trades_closed": 0,
            "wins": 0, "losses": 0, "consecutive_sl": 0, "initial_equity": 0.0,
            "peak_equity": 0.0, "current_mdd": 0.0, "kill_switch": False,
            "kill_reason": "", "entry_signal_candle_open_time": None,
            "last_entry_signal_open_time_by_symbol": {}}

def save_state(s):
    with open(STATE_FILE, "w") as f: json.dump(s, f, indent=2)

def append_trade(ts, sym, role, epx, xpx, reason, qty, notional, npnl, eq, mdd):
    exists = TRADES_FILE.exists()
    with open(TRADES_FILE, "a") as f:
        if not exists: f.write("timestamp,symbol,role,mode,entry_px,exit_px,reason,qty,notional_usdt,net_pnl_pct,equity_after,mdd_pct\n")
        f.write(f"{ts},{sym},{role},{RUN_MODE},{epx:.8f},{xpx:.8f},{reason},{qty:.8f},{notional:.2f},{npnl:.4f},{eq:.2f},{mdd:.2f}\n")

def write_heartbeat(state, usdt, coin, equity):
    npnl = ((equity-state["initial_equity"])/state["initial_equity"])*100.0 if state["initial_equity"] > 0 else 0.0
    lines = [
        "=== ALADDIN GITHUB ACTIONS (TRIDENTE+HOTFIX V4) ===",
        f"MODE       : {RUN_MODE}", f"TIMESTAMP  : {utc_now()}",
        f"STATUS     : {'KILLED('+state['kill_reason']+')' if state['kill_switch'] else 'ACTIVE'}"]
    if state["active_trade"]:
        unr = ((equity-state["invested_usdt"])/state["invested_usdt"])*100.0 if state["invested_usdt"] > 0 else 0.0
        lines.append(f"POSITION   : LONG {state['symbol']} ({state['role']}) | Entry:{state['entry_px']:.8f} | Unreal:{unr:+.2f}%")
    else: lines.append("POSITION   : FLAT (Vigilando COS,PEPE,MEME)")
    lines += [f"USDT REAL  : ${usdt:.2f}", f"EQUITY     : ${equity:.2f}", f"NET P&L    : {npnl:+.2f}%",
              f"TRADES     : {state['trades_closed']} (W:{state['wins']}/L:{state['losses']})",
              f"MAX DD     : {state['current_mdd']:.2f}%", f"CONSEC SL  : {state['consecutive_sl']}",
              f"KILL SWITCH: {'ON-'+state['kill_reason'] if state['kill_switch'] else 'OFF'}",
              "=" * 50]
    with open(HEARTBEAT_FILE, "w") as f: f.write("\n".join(lines) + "\n")

def current_equity(state, usdt):
    if state["active_trade"]:
        cb = get_balance(state["symbol"][:-4])
        if cb > 0: return usdt + cb * spot_price(state["symbol"])
    return usdt

def process_exit(state, cfg):
    sym = cfg["symbol"]; kl = get_klines(sym, "1d")
    cl = np.array([float(k[4]) for k in kl]); hi = np.array([float(k[2]) for k in kl])
    lo = np.array([float(k[3]) for k in kl]); ema20, _, _ = calc_indicators(cl, hi, lo)
    idx = len(cl)-2; lc = cl[idx]; lct = str(kl[idx][0]); cp = spot_price(sym)
    state["max_px"] = max(state["max_px"], cp); slpx = state["entry_px"]*(1.0+cfg["stop"])
    do_sell = False; reason = ""
    if cp <= slpx: do_sell, reason = True, "STOP_LOSS_INTRABAR"
    elif cfg["exit"] == "TRAIL_7_PCT" and cp <= state["max_px"]*0.93: do_sell, reason = True, "TRAIL_7_EXIT"
    elif cfg["exit"] == "EMA20_CLOSE_EXIT":
        ec = state.get("entry_signal_candle_open_time")
        if ec is not None and lct == str(ec):
            log(f"  HOTFIX V4: ignorando EMA20 exit misma vela {sym}"); do_sell = False
        elif lc < ema20[idx]: do_sell, reason = True, "EMA20_DAILY_EXIT"
    if not do_sell: return state
    ss, mq, _ = get_symbol_filters(sym); cb = get_balance(sym[:-4]); qts = adjust_quantity(cb, ss)
    if qts < mq:
        state["active_trade"] = False; state["symbol"] = state["role"] = ""
        state["qty"] = state["entry_px"] = state["invested_usdt"] = state["max_px"] = 0.0
        state["entry_signal_candle_open_time"] = None; return state
    log(f"  SELL {sym}: qty={qts}, reason={reason}")
    code, res = signed_post("/api/v3/order", {"symbol": sym, "side": "SELL", "type": "MARKET", "quantity": f"{qts:.8f}"})
    if code != 200: raise RuntimeError(f"sell_fail({code}): {res}")
    fpx = cp
    if res.get("fills"):
        qs = ps = 0.0
        for fl in res["fills"]: q, p = float(fl["qty"]), float(fl["price"]); qs += q; ps += q*p
        if qs > 0: fpx = ps/qs
    pnl = (fpx-state["entry_px"])/state["entry_px"]; npnl = pnl-(FEE*2)-(SLIP*2)
    time.sleep(2); usdt = get_balance("USDT"); eq = usdt
    state["trades_closed"] += 1
    if npnl > 0: state["wins"] += 1; state["consecutive_sl"] = 0
    else:
        state["losses"] += 1
        if reason.startswith("STOP"): state["consecutive_sl"] += 1
    state["peak_equity"] = max(state["peak_equity"], eq)
    if state["peak_equity"] > 0: state["current_mdd"] = max(state["current_mdd"], ((state["peak_equity"]-eq)/state["peak_equity"])*100.0)
    append_trade(utc_now(), sym, cfg["role"], state["entry_px"], fpx, reason, qts, state["invested_usdt"], npnl*100.0, eq, state["current_mdd"])
    log(f"  SOLD {sym}@{fpx:.8f}|P&L:{npnl*100:+.2f}%|{reason}")
    if state["consecutive_sl"] >= 5: state["kill_switch"], state["kill_reason"] = True, "5_consec_SL"
    elif state["current_mdd"] > 25.0: state["kill_switch"], state["kill_reason"] = True, "DD>25%"
    state["active_trade"] = False; state["symbol"] = state["role"] = ""
    state["qty"] = state["entry_px"] = state["invested_usdt"] = state["max_px"] = 0.0
    state["entry_signal_candle_open_time"] = None; return state

def process_entry(state, usdt):
    sm = state.setdefault("last_entry_signal_open_time_by_symbol", {})
    for cfg in CONFIGS:
        sym = cfg["symbol"]; kl = get_klines(sym, cfg["tf"])
        cl = np.array([float(k[4]) for k in kl]); hi = np.array([float(k[2]) for k in kl])
        lo = np.array([float(k[3]) for k in kl]); _, rsi, adx = calc_indicators(cl, hi, lo)
        idx = len(cl)-2; sot = str(kl[idx][0])
        if sm.get(sym) == sot: continue
        if not (adx[idx] > 25.0 and rsi[idx] < cfg["trigger"]): continue
        cp = spot_price(sym); spend = usdt * INVESTMENT_PCT; _, _, mn = get_symbol_filters(sym)
        if spend < max(mn, 5.5): continue
        log(f"  ENTRY: {sym}(ADX={adx[idx]:.1f},RSI={rsi[idx]:.1f})|${spend:.2f}")
        code, res = market_buy_quote(sym, spend)
        if code != 200: log(f"  BUY FAIL({code}): {res}"); continue
        eq = ec = 0.0; fpx = cp
        for fl in res.get("fills", []):
            q, p = float(fl["qty"]), float(fl["price"]); eq += q; ec += q*p
        if eq > 0: fpx = ec/eq
        state["active_trade"] = True; state["symbol"] = sym; state["role"] = cfg["role"]
        state["entry_px"] = fpx; state["qty"] = eq; state["invested_usdt"] = spend
        state["max_px"] = fpx; state["entry_signal_candle_open_time"] = sot; sm[sym] = sot
        log(f"  BOUGHT {sym}@{fpx:.8f}|Qty:{eq}|${spend:.2f}"); break
    return state

def main():
    log("="*50); log("ALADDIN GITHUB ACTIONS — Ejecucion"); log("="*50)
    state = load_state(); usdt = get_balance("USDT"); equity = current_equity(state, usdt)
    log(f"  USDT:${usdt:.2f}|Eq:${equity:.2f}|Pos:{state['symbol'] if state['active_trade'] else 'FLAT'}")
    if state["initial_equity"] == 0.0 and equity > 0:
        state["initial_equity"] = state["peak_equity"] = equity
    if state["kill_switch"]:
        log(f"  KILL:{state['kill_reason']}"); cb = get_balance(state["symbol"][:-4]) if state["active_trade"] else 0.0
        write_heartbeat(state, usdt, cb, equity); save_state(state); return
    if state["active_trade"]:
        if get_balance(state["symbol"][:-4]) == 0:
            log("  WARN:0 balance, cleaning"); state["active_trade"] = False
            state["symbol"] = state["role"] = ""; state["qty"] = 0.0
            state["entry_signal_candle_open_time"] = None
    if state["active_trade"]:
        cfg = next((c for c in CONFIGS if c["symbol"] == state["symbol"]), None)
        if cfg: state = process_exit(state, cfg)
    if not state["active_trade"] and not state["kill_switch"] and usdt > 10.0:
        state = process_entry(state, usdt)
    usdt = get_balance("USDT"); cb = get_balance(state["symbol"][:-4]) if state["active_trade"] else 0.0
    equity = current_equity(state, usdt)
    state["peak_equity"] = max(state["peak_equity"], equity)
    if state["peak_equity"] > 0: state["current_mdd"] = max(state["current_mdd"], ((state["peak_equity"]-equity)/state["peak_equity"])*100.0)
    save_state(state); write_heartbeat(state, usdt, cb, equity)
    log(f"  Ciclo OK. Pos:{state['symbol'] if state['active_trade'] else 'FLAT'}"); log("")

if __name__ == "__main__":
    try: main()
    except Exception as e:
        err = f"CRASH[{utc_now()}]:{e}\n{traceback.format_exc()}"; print(err)
        try:
            with open(HEARTBEAT_FILE, "w") as f: f.write(err)
            with open(LOG_FILE, "a") as f: f.write(err + "\n")
        except: pass
