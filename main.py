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
PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT",
    "XRP-USDT", "DOGE-USDT", "AVAX-USDT",
    "LINK-USDT", "MATIC-USDT", "ADA-USDT",
    "ARB-USDT", "OP-USDT", "NEAR-USDT",
    "APT-USDT", "SUI-USDT", "ATOM-USDT",
    "LTC-USDT", "UNI-USDT", "FIL-USDT",
    "INJ-USDT", "PEPE-USDT"
]

DRY_RUN = False

MIN_ORDER_USDT = 10
RISK_PER_TRADE = 0.02

# ESTRATÉGIA
TAKE_PROFIT = 0.006    # 0.6%
STOP_LOSS   = -0.0025  # -0.25%
TRAILING    = -0.006   # -0.6%

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
    try:
        endpoint = "/api/v5/account/balance"
        url = OKX_BASE + endpoint

        headers = get_headers("GET", endpoint)
        res = requests.get(url, headers=headers).json()

        for acc in res["data"][0]["details"]:
            if acc["ccy"] == asset:
                return float(acc["availBal"])
    except Exception as e:
        print("❌ ERRO BALANCE:", e)

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

    print(f"💰 ORDER SIZE: {size}")
    return round(size, 2)

# ================================
# 💰 ORDER
# ================================
def place_order(side, price, pair):

    if DRY_RUN:
        print(f"🧪 DRY RUN: {side} {pair} @ {price}")
        return {"code": "0"}

    try:
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

            size = f"{balance * 0.995:.6f}"

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

    except Exception as e:
        print("❌ ERRO ORDER:", e)
        return {"code": "1"}

# ================================
# 📊 MARKET
# ================================
def get_candles(pair, timeframe="1m", limit=5):
    try:
        url = f"{OKX_BASE}/api/v5/market/candles?instId={pair}&bar={timeframe}&limit={limit}"
        res = requests.get(url).json()

        candles = res["data"]
        closes = [float(c[4]) for c in candles]
        volumes = [float(c[5]) for c in candles]

        return closes, volumes
    except Exception as e:
        print("❌ ERRO CANDLES:", e)
        return None

# ================================
# 📈 TREND
# ================================
def get_trend(pair):
    data = get_candles(pair, "5m", 20)
    if not data:
        return "range"

    closes, _ = data
    closes = list(reversed(closes))

    sma_short = sum(closes[-5:]) / 5
    sma_long = sum(closes) / 20

    return "up" if sma_short > sma_long else "down"

# ================================
# 🧠 MANAGE POSITIONS
# ================================
def manage_positions():
    global positions

    print("🧠 MANAGING POSITIONS:", positions)

    for pair in list(positions.keys()):
        data = get_candles(pair)
        if not data:
            continue

        closes, _ = data
        current_price = closes[0]

        pos = positions[pair]
        entry = pos["entry_price"]

        if current_price > pos["max_price"]:
            pos["max_price"] = current_price

        profit = (current_price - entry) / entry
        drawdown = (current_price - pos["max_price"]) / pos["max_price"]

        print(f"📊 {pair} profit={profit:.4f} drawdown={drawdown:.4f}")

        if profit >= TAKE_PROFIT:
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, "take_profit")
                del positions[pair]

        elif profit <= STOP_LOSS:
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, "stop_loss")
                del positions[pair]

        elif drawdown <= TRAILING:
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, "trailing_stop")
                del positions[pair]

# ================================
# 🧠 SCANNER
# ================================
def scan_market():
    global positions

    for pair in PAIRS:

        print(f"🔍 SCANNING {pair}")

        if pair in positions:
            continue

        trend = get_trend(pair)
        if trend != "up":
            continue

        data = get_candles(pair)
        if not data:
            continue

        closes, volumes = data

        price = closes[0]
        prev_price = closes[1]

        delta = (price - prev_price) / prev_price
        delta_pct = delta * 100

        volume_boost = volumes[0] > volumes[1] * 1.1

        volatility = (max(closes) - min(closes)) / closes[-1]

        print(f"""
📊 {pair}
TREND: {trend}
DELTA: {delta:.4f} ({delta_pct:.2f}%)
VOLUME BOOST: {volume_boost}
VOLATILITY: {volatility:.4f}
""")

        if volatility < 0.002:
            continue

        if delta > 0.001 and volume_boost:

            order = place_order("buy", price, pair)

            if order.get("code") == "0":
                positions[pair] = {
                    "entry_price": price,
                    "max_price": price
                }

                log_trade(pair, "BUY", price, 0, "trend_momentum")

# ================================
# 🔁 LOOP
# ================================
def trading_loop():
    print("🚀 BOT STARTED")

    while True:
        try:
            print("🔁 LOOP EXECUTANDO...")
            manage_positions()
            scan_market()

        except Exception as e:
            print("❌ ERRO LOOP:", str(e))

        time.sleep(15)

# ================================
# 🚀 START
# ================================
@app.on_event("startup")
def start_bot():
    print("🔥 STARTUP INICIADO")

    init_db()

    thread = threading.Thread(target=trading_loop, daemon=True)
    thread.start()
