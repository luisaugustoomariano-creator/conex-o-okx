from fastapi import FastAPI
import requests
import os
import time
import hmac
import base64
import threading
import sqlite3
import json
from datetime import datetime, timezone

app = FastAPI()

OKX_BASE = "https://www.okx.com"

# ================================
# 🔐 ENV VARIABLES
# ================================
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# ================================
# ⚙️ CONFIG
# ================================
PAIRS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

ORDER_SIZE_USDT = 12

TAKE_PROFIT = 0.50
STOP_LOSS = -0.25

DRY_RUN = False
MIN_SIZE = 0.0001

positions = {}

# ================================
# 🗄️ DATABASE
# ================================
DB_FILE = "trades.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair TEXT,
            action TEXT,
            price REAL,
            pnl REAL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

def log_trade(pair, action, price, pnl, reason):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO trades (pair, action, price, pnl, reason)
        VALUES (?, ?, ?, ?, ?)
    """, (pair, action, price, pnl, reason))

    conn.commit()
    conn.close()

# ================================
# 🔐 OKX AUTH
# ================================
def sign(message, secret):
    return base64.b64encode(
        hmac.new(secret.encode(), message.encode(), digestmod="sha256").digest()
    ).decode()

def get_headers(method, endpoint, body=""):
    timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    message = timestamp + method + endpoint + body
    signature = sign(message, OKX_SECRET_KEY)

    return {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json"
    }

# ================================
# 🔥 NOVO: SALDO REAL
# ================================
def get_balance(asset):
    endpoint = "/api/v5/account/balance"
    url = OKX_BASE + endpoint

    headers = get_headers("GET", endpoint)

    res = requests.get(url, headers=headers).json()

    try:
        for acc in res["data"][0]["details"]:
            if acc["ccy"] == asset:
                return float(acc["availBal"])
    except:
        return 0

    return 0

# ================================
# 💰 ORDER
# ================================
def place_order(side, price, pair):

    if DRY_RUN:
        print(f"🧪 DRY RUN: {side} {pair} @ {price}")
        return {"code": "0"}

    endpoint = "/api/v5/trade/order"
    url = OKX_BASE + endpoint

    if side.lower() == "buy":
        body = {
            "instId": pair,
            "tdMode": "cash",
            "side": "buy",
            "ordType": "market",
            "sz": str(ORDER_SIZE_USDT),
            "tgtCcy": "quote_ccy"
        }

    else:
        base_asset = pair.split("-")[0]
        balance = get_balance(base_asset)

        if balance <= 0:
            print(f"⚠️ Sem saldo para vender {pair}")
            return {"code": "1"}

        size = balance * 0.995
        size = f"{size:.6f}"

        body = {
            "instId": pair,
            "tdMode": "cash",
            "side": "sell",
            "ordType": "market",
            "sz": size
        }

    body_str = json.dumps(body)
    headers = get_headers("POST", endpoint, body_str)

    response = requests.post(url, headers=headers, data=body_str)
    res_json = response.json()

    print("📡 ORDER:", body)
    print("📡 RESPONSE:", res_json)

    return res_json

# ================================
# 📊 MARKET
# ================================
def get_candles(pair):
    url = f"{OKX_BASE}/api/v5/market/candles?instId={pair}&bar=1m&limit=5"
    res = requests.get(url).json()

    if not res.get("data"):
        return None

    candles = res["data"]
    closes = [float(c[4]) for c in candles[:3]]
    volumes = [float(c[5]) for c in candles[:3]]

    return closes, volumes

# ================================
# 🧠 GERENCIAR POSIÇÕES
# ================================
def manage_positions():

    global positions

    for pair in list(positions.keys()):

        data = get_candles(pair)
        if not data:
            continue

        closes, _ = data
        current_price = closes[0]

        entry = positions[pair]["entry_price"]
        pnl = ((current_price - entry) / entry) * 100

        print(f"📈 {pair} pnl={pnl:.4f}")

        if pnl >= TAKE_PROFIT:
            order = place_order("sell", current_price, pair)

            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, pnl, "tp")
                del positions[pair]
                print(f"💰 TP SELL {pair}")

        elif pnl <= STOP_LOSS:
            order = place_order("sell", current_price, pair)

            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, pnl, "sl")
                del positions[pair]
                print(f"🛑 SL SELL {pair}")

# ================================
# 🧠 SCANNER
# ================================
def scan_market():

    global positions

    for pair in PAIRS:

        if pair in positions:
            continue

        data = get_candles(pair)
        if not data:
            continue

        closes, volumes = data

        score = 0

        if closes[1] < closes[0]:
            score += 1

        if closes[2] < closes[1] < closes[0]:
            score += 2

        if volumes[1] < volumes[0]:
            score += 1

        if volumes[2] < volumes[1] < volumes[0]:
            score += 2

        print(f"📊 {pair} score={score}")

        if score >= 3:
            price = closes[0]

            order = place_order("buy", price, pair)

            if order.get("code") == "0":
                positions[pair] = {
                    "entry_price": price
                }

                log_trade(pair, "BUY", price, 0, f"score={score}")

                print(f"🚀 BUY {pair}")

# ================================
# 🔁 LOOP
# ================================
def trading_loop():
    while True:
        try:
            manage_positions()
            scan_market()

        except Exception as e:
            print("❌ ERRO:", str(e))

        time.sleep(15)

# ================================
# 🚀 START
# ================================
@app.on_event("startup")
def start_bot():
    init_db()

    thread = threading.Thread(target=trading_loop)
    thread.start()
