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
PAIR = "BTC-USDT"
ORDER_SIZE_USDT = 12
TAKE_PROFIT = 0.7
STOP_LOSS = -0.3
DRY_RUN = False

MIN_BTC_SIZE = 0.0001  # 🔥 mínimo OKX

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
# 🔐 AUTH OKX
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
# 📊 SIZE CORRETO
# ================================
def calculate_size(price):
    size = ORDER_SIZE_USDT / price

    # garante mínimo
    if size < MIN_BTC_SIZE:
        size = MIN_BTC_SIZE

    # força formato correto (sem notação científica)
    return "{:.6f}".format(size)

# ================================
# 💰 EXECUÇÃO
# ================================
def place_order(side, price):
    if DRY_RUN:
        print(f"🧪 DRY RUN: {side} @ {price}")
        return {"code": "0"}

    endpoint = "/api/v5/trade/order"
    url = OKX_BASE + endpoint

    size = calculate_size(price)

    body = {
        "instId": PAIR,
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
# 🧠 SCALPING
# ================================
def scalp():

    global position

    data = get_candles(PAIR)

    if not data:
        return {"action": "NO_DATA"}

    closes, volumes = data
    current_price = closes[0]

    uptrend = closes[2] < closes[1] < closes[0]
    downtrend = closes[2] > closes[1] > closes[0]
    volume_up = volumes[2] < volumes[1] < volumes[0]

    # ============================
    # 🟢 ENTRY
    # ============================
    if not position["open"]:
        if uptrend and volume_up:

            order = place_order("buy", current_price)

            if order.get("code") == "0":
                position["open"] = True
                position["entry_price"] = current_price
                position["pair"] = PAIR

                log_trade(PAIR, "BUY", current_price, 0, "entry")

                return {"action": "BUY", "price": current_price}

        return {"action": "HOLD"}

    # ============================
    # 🔴 EXIT
    # ============================
    entry = position["entry_price"]
    pnl = ((current_price - entry) / entry) * 100

    if pnl >= TAKE_PROFIT:
        order = place_order("sell", current_price)

        if order.get("code") == "0":
            position["open"] = False
            log_trade(PAIR, "SELL", current_price, pnl, "take profit")

            return {"action": "SELL", "pnl": round(pnl, 3)}

    if pnl <= STOP_LOSS:
        order = place_order("sell", current_price)

        if order.get("code") == "0":
            position["open"] = False
            log_trade(PAIR, "SELL", current_price, pnl, "stop loss")

            return {"action": "SELL", "pnl": round(pnl, 3)}

    if downtrend:
        order = place_order("sell", current_price)

        if order.get("code") == "0":
            position["open"] = False
            log_trade(PAIR, "SELL", current_price, pnl, "reversal")

            return {"action": "SELL", "pnl": round(pnl, 3)}

    return {"action": "HOLD", "pnl": round(pnl, 3)}

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
