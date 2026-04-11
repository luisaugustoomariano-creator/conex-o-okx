from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
import os
import time
import hmac
import base64
import threading
import json
import psycopg2
import logging
from datetime import datetime, timezone

app = FastAPI()

# ================================
# 🔥 CORS
# ================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ================================
# LOGGER
# ================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

OKX_BASE = "https://www.okx.com"

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# ================================
# CONFIG ORIGINAL
# ================================
PAIRS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT",
    "XRP-USDT", "DOGE-USDT", "AVAX-USDT",
    "LINK-USDT", "MATIC-USDT", "ADA-USDT",
    "ARB-USDT", "OP-USDT", "NEAR-USDT",
    "APT-USDT", "SUI-USDT", "ATOM-USDT",
    "LTC-USDT", "UNI-USDT", "FIL-USDT",
    "INJ-USDT", "PEPE-USDT", "WIF-USDT",
    "BONK-USDT", "FLOKI-USDT",
]

DRY_RUN = False
MIN_ORDER_USDT = 20
RISK_PER_TRADE = 0.04

STOP_LOSS = 0.01
TRAILING_STOP = 0.03
MIN_DELTA = 0.01

positions = {}

# ================================
# BOT STATE
# ================================
bot_running = False
bot_thread = None

# ================================
# DATABASE
# ================================
def get_db_connection():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        database=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        port=os.getenv("PGPORT")
    )

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            pair VARCHAR(20),
            action VARCHAR(10),
            price NUMERIC,
            pnl NUMERIC,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()

def log_trade(pair, action, price, pnl, reason):
    logger.info(f"TRADE → {pair} {action} price={price} pnl={pnl} {reason}")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO trades (pair, action, price, pnl, reason)
        VALUES (%s, %s, %s, %s, %s)
    """, (pair, action, price, pnl, reason))

    conn.commit()
    cursor.close()
    conn.close()

# ================================
# OKX AUTH
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

def get_total_pnl():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COALESCE(SUM(pnl), 0)
            FROM trades
            WHERE action = 'SELL'
        """)

        total = cursor.fetchone()[0] or 0

        cursor.close()
        conn.close()

        return float(total)
    except Exception as e:
        logger.error(f"PNL calc error: {e}")
        return 0.0


@app.get("/dashboard")
def dashboard():
    return {
        "balance": get_balance("USDT"),
        "pnl": get_total_pnl(),
        "status": "ON" if bot_running else "OFF"
    }

# ================================
# BALANCE
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
        logger.error(f"Balance error: {e}")
    return 0

# ================================
# ORDER SIZE
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
# ORDER
# ================================
def place_order(side, price, pair):
    logger.info(f"ORDER → {side} {pair} @ {price}")

    if DRY_RUN:
        return {"code": "0"}

    endpoint = "/api/v5/trade/order"
    url = OKX_BASE + endpoint

    if side == "buy":
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
    return response.json()

# ================================
# MARKET
# ================================
def get_candles(pair, timeframe="1m", limit=5):
    url = f"{OKX_BASE}/api/v5/market/candles?instId={pair}&bar={timeframe}&limit={limit}"
    res = requests.get(url).json()

    candles = res["data"]
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    return closes, volumes

# ================================
# TREND
# ================================
def get_trend(pair):
    data = get_candles(pair, "5m", 20)
    closes = list(reversed(data[0]))

    sma_short = sum(closes[-5:]) / 5
    sma_long = sum(closes) / 20

    return "up" if sma_short > sma_long else "down"

# ================================
# MANAGE POSITIONS
# ================================
def manage_positions():
    global positions

    for pair in list(positions.keys()):
        closes, _ = get_candles(pair)
        current_price = closes[0]

        pos = positions[pair]
        entry = pos["entry_price"]
        max_price = pos.get("max_price", entry)

        if current_price > max_price:
            pos["max_price"] = current_price

        profit = (current_price - entry) / entry

        if current_price <= entry * (1 - STOP_LOSS):
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, "stop_loss")
                del positions[pair]

        elif current_price <= pos["max_price"] * (1 - TRAILING_STOP):
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, "trailing_stop")
                del positions[pair]

# ================================
# SCAN MARKET
# ================================
def scan_market():
    global positions

    for pair in PAIRS:
        if pair in positions:
            continue

        trend = get_trend(pair)
        if trend != "up":
            continue

        closes, volumes = get_candles(pair)

        price = closes[0]
        prev_price = closes[1]

        delta = (price - prev_price) / prev_price
        volume_boost = volumes[0] > volumes[1] * 1.1
        volatility = (max(closes) - min(closes)) / closes[-1]

        if delta >= MIN_DELTA and volume_boost and volatility > 0.004:
            order = place_order("buy", price, pair)

            if order.get("code") == "0":
                positions[pair] = {
                    "entry_price": price,
                    "max_price": price
                }

                log_trade(pair, "BUY", price, 0, "momentum")

# ================================
# LOOP CONTROLADO
# ================================
def controlled_loop():
    global bot_running

    logger.info("BOT STARTED")

    while bot_running:
        try:
            manage_positions()
            scan_market()
        except Exception as e:
            logger.error(f"Loop error: {e}")

        time.sleep(15)

    logger.info("BOT STOPPED")

# ================================
# API
# ================================
@app.get("/dashboard")
def dashboard():
    return {
        "balance": get_balance("USDT"),
        "status": "ON" if bot_running else "OFF"
    }

@app.post("/bot/start")
def start_bot():
    global bot_running, bot_thread

    if bot_running:
        return {"message": "already running"}

    bot_running = True
    bot_thread = threading.Thread(target=controlled_loop, daemon=True)
    bot_thread.start()

    return {"message": "started"}

@app.post("/bot/stop")
def stop_bot():
    global bot_running
    bot_running = False
    return {"message": "stopped"}

from fastapi import Query

@app.get("/logs")
def logs(start: str = Query(None), end: str = Query(None)):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT pair, action, price, pnl, reason, created_at
        FROM trades
    """

    conditions = []
    values = []

    if start:
        conditions.append("created_at >= %s")
        values.append(start)

    if end:
        conditions.append("created_at <= %s")
        values.append(end)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY created_at DESC LIMIT 50"

    cursor.execute(query, values)

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return [
        {
            "pair": r[0],
            "action": r[1],
            "price": float(r[2]),
            "pnl": float(r[3]),
            "reason": r[4],
            "time": r[5].isoformat()
        }
        for r in rows
    ]

@app.on_event("startup")
def startup():
    init_db()
