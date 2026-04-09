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

DRY_RUN = False

MIN_ORDER_USDT = 10
RISK_PER_TRADE = 0.02

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
# 💰 BALANCE
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
# 🧠 POSITION SIZE
# ================================
def calculate_order_size():
    balance = get_balance("USDT")

    if balance <= 0:
        return 0

    size = balance * RISK_PER_TRADE

    if size < MIN_ORDER_USDT:
        size = MIN_ORDER_USDT

    if size > balance:
        size = balance * 0.99

    return round(size, 2)

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

        size = calculate_order_size()

        body = {
            "instId": pair,
            "tdMode": "cash",
            "side": "buy",
            "ordType": "market",
            "sz": str(size),
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
def get_candles(pair, timeframe="1m", limit=5):
    url = f"{OKX_BASE}/api/v5/market/candles?instId={pair}&bar={timeframe}&limit={limit}"
    res = requests.get(url).json()

    if not res.get("data"):
        return None

    candles = res["data"]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    return closes, volumes

# ================================
# 📈 TREND
# ================================
def get_trend(pair):
    data = get_candles(pair, "5m", 20)
    if not data:
        return "range"

    closes, _ = data

    sma_short = sum(closes[:5]) / 5
    sma_long = sum(closes[:20]) / 20

    if sma_short > sma_long:
        return "up"
    elif sma_short < sma_long:
        return "down"
    return "range"

# ================================
# 📊 ATR
# ================================
def calculate_atr(closes):
    trs = [abs(closes[i] - closes[i+1]) for i in range(len(closes)-1)]
    return sum(trs) / len(trs)

# ================================
# 🧠 MANAGE POSITIONS
# ================================
def manage_positions():
    global positions

    for pair in list(positions.keys()):

        data = get_candles(pair)
        if not data:
            continue

        closes, _ = data
        current_price = closes[0]

        pos = positions[pair]
        entry = pos["entry_price"]

        pnl = ((current_price - entry) / entry) * 100

        # update max price (trailing)
        if current_price > pos["max_price"]:
            pos["max_price"] = current_price

        drawdown = ((current_price - pos["max_price"]) / pos["max_price"]) * 100

        print(f"📈 {pair} pnl={pnl:.4f} drawdown={drawdown:.4f}")

        # trailing stop
        if drawdown <= -0.3:
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, pnl, "trailing_stop")
                del positions[pair]
                print(f"🔻 TRAILING STOP {pair}")

# ================================
# 🧠 SCANNER
# ================================
def scan_market():
    global positions

    for pair in PAIRS:

        if pair in positions:
            continue

        trend = get_trend(pair)

        if trend != "up":
            continue

        data = get_candles(pair)
        if not data:
            continue

        closes, volumes = data[:3]

        # filtro de ruído
        if abs(closes[0] - closes[1]) / closes[1] < 0.002:
            continue

        score = 0

        delta1 = closes[0] - closes[1]
        delta2 = closes[1] - closes[2]

        if delta1 > 0:
            score += 1

        if delta1 > delta2:
            score += 1

        if volumes[0] > volumes[1] * 1.2:
            score += 2

        if (delta1 / closes[1]) > 0.002:
            score += 1

        print(f"📊 {pair} score={score} trend={trend}")

        if score >= 3:
            price = closes[0]

            order = place_order("buy", price, pair)

            if order.get("code") == "0":

                positions[pair] = {
                    "entry_price": price,
                    "max_price": price
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
