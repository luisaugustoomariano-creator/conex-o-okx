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

# 🔥 AUMENTADO + MARGEM
ORDER_SIZE_USDT = 15

TAKE_PROFIT = 0.7
STOP_LOSS = -0.3
DRY_RUN = False

MIN_SIZE = 0.0001

# ================================
# 🧠 ESTADO
# ================================
position = {
    "open": False,
    "entry_price": 0,
    "pair": None
}

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
# 📊 SIZE INTELIGENTE (CORRIGIDO)
# ================================
def calculate_size(price, pair):

    base_usdt_map = {
        "BTC-USDT": 15,
        "ETH-USDT": 30,
        "SOL-USDT": 25
    }

    base_usdt = base_usdt_map.get(pair, 20)

    # margem segurança
    safe_usdt = base_usdt * 1.2

    raw_size = safe_usdt / price

    # 🔥 STEP SIZE SIMPLES (FUNCIONA NA PRÁTICA)
    if pair == "BTC-USDT":
        size = round(raw_size, 6)
    elif pair == "ETH-USDT":
        size = round(raw_size, 5)
    else:
        size = round(raw_size, 3)

    # mínimo absoluto
    if size < MIN_SIZE:
        size = MIN_SIZE

    return str(size)

# ================================
# 💰 EXECUÇÃO
# ================================
def place_order(side, price, pair):

    if DRY_RUN:
        print(f"🧪 DRY RUN: {side} {pair} @ {price}")
        return {"code": "0"}

    endpoint = "/api/v5/trade/order"
    url = OKX_BASE + endpoint

    size = calculate_size(price)

    body = {
        "instId": pair,
        "tdMode": "cash",
        "side": side.lower(),
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
# 🧠 GERENCIAMENTO
# ================================
def manage_position():

    global position

    data = get_candles(position["pair"])
    if not data:
        return {"action": "NO_DATA"}

    closes, _ = data
    current_price = closes[0]

    entry = position["entry_price"]
    pnl = ((current_price - entry) / entry) * 100

    if pnl >= TAKE_PROFIT:
        order = place_order("sell", current_price, position["pair"])

        if order.get("code") == "0":
            log_trade(position["pair"], "SELL", current_price, pnl, "tp")
            position["open"] = False
            return {"action": "SELL", "pnl": pnl}

    if pnl <= STOP_LOSS:
        order = place_order("sell", current_price, position["pair"])

        if order.get("code") == "0":
            log_trade(position["pair"], "SELL", current_price, pnl, "sl")
            position["open"] = False
            return {"action": "SELL", "pnl": pnl}

    return {"action": "HOLD", "pnl": pnl}

# ================================
# 🧠 SCANNER
# ================================
def scalp():

    global position

    if position["open"]:
        return manage_position()

    best_trade = None
    best_score = 0

    for pair in PAIRS:
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

        if score >= 3 and score > best_score:
            best_score = score
            best_trade = {
                "pair": pair,
                "price": closes[0],
                "score": score
            }

    if best_trade:
        order = place_order("buy", best_trade["price"], best_trade["pair"])

        if order.get("code") == "0":
            position["open"] = True
            position["entry_price"] = best_trade["price"]
            position["pair"] = best_trade["pair"]

            log_trade(best_trade["pair"], "BUY", best_trade["price"], 0, f"score={best_trade['score']}")

            return {
                "action": "BUY",
                "pair": best_trade["pair"],
                "score": best_trade["score"]
            }

    return {"action": "HOLD"}

# ================================
# 🔁 LOOP
# ================================
def trading_loop():
    while True:
        try:
            result = scalp()
            print("🤖 BOT:", result)

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
    thread.daemon = True
    thread.start()

    print("🚀 BOT INICIADO")
