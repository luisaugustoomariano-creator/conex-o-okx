from fastapi import FastAPI
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
# 🧠 LOGGER PROFISSIONAL
# ================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(__name__)

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
# 🗄️ DATABASE
# ================================
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=os.getenv("PGHOST"),
            database=os.getenv("PGDATABASE"),
            user=os.getenv("PGUSER"),
            password=os.getenv("PGPASSWORD"),
            port=os.getenv("PGPORT")
        )
        logger.info("✅ Conectado ao PostgreSQL")
        return conn
    except Exception as e:
        logger.error(f"❌ ERRO DB CONNECTION: {e}")
        raise

def init_db():
    logger.info("🚀 Inicializando banco...")
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

    logger.info("✅ Tabela pronta")

def log_trade(pair, action, price, pnl, reason):
    logger.info(f"📊 TRADE → {pair} | {action} | price={price} | pnl={pnl} | {reason}")
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO trades (pair, action, price, pnl, reason)
            VALUES (%s, %s, %s, %s, %s)
        """, (pair, action, price, pnl, reason))

        conn.commit()
        cursor.close()
        conn.close()

        logger.info("✅ Trade salvo no banco")

    except Exception as e:
        logger.error(f"❌ ERRO AO SALVAR TRADE: {e}")

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
                balance = float(acc["availBal"])
                logger.info(f"💰 Balance {asset}: {balance}")
                return balance
    except Exception as e:
        logger.error(f"❌ ERRO BALANCE: {e}")

    return 0

# ================================
# 🧠 ORDER SIZE
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

    logger.info(f"💰 ORDER SIZE: {size}")
    return round(size, 2)

# ================================
# 💰 ORDER
# ================================
def place_order(side, price, pair):

    logger.info(f"📤 ORDER → {side} {pair} @ {price}")

    if DRY_RUN:
        logger.info("🧪 DRY RUN")
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
                logger.warning("⚠️ Sem saldo pra vender")
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

        logger.info(f"📡 RESPONSE: {res_json}")

        return res_json

    except Exception as e:
        logger.error(f"❌ ERRO ORDER: {e}")
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

        logger.info(f"📈 {pair} candles OK")
        return closes, volumes
    except Exception as e:
        logger.error(f"❌ ERRO CANDLES {pair}: {e}")
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

    trend = "up" if sma_short > sma_long else "down"
    logger.info(f"📊 Trend {pair}: {trend}")

    return trend

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
        max_price = pos.get("max_price", entry)

        if current_price > max_price:
            max_price = current_price
            pos["max_price"] = max_price

        profit = (current_price - entry) / entry

        logger.info(f"📊 {pair} | entry={entry} | current={current_price} | pnl={profit}")

        if current_price <= entry * (1 - STOP_LOSS):
            logger.warning(f"🛑 STOP LOSS {pair}")
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, "stop_loss")
                del positions[pair]

        elif current_price <= max_price * (1 - TRAILING_STOP):
            logger.warning(f"📉 TRAILING STOP {pair}")
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
        volume_boost = volumes[0] > volumes[1] * 1.1
        volatility = (max(closes) - min(closes)) / closes[-1]

        logger.info(f"🔎 {pair} | delta={delta} | vol={volume_boost} | volat={volatility}")

        if delta >= MIN_DELTA and volume_boost and volatility > 0.004:
            logger.info(f"🟢 COMPRA {pair}")

            order = place_order("buy", price, pair)

            if order.get("code") == "0":
                positions[pair] = {
                    "entry_price": price,
                    "max_price": price
                }

                log_trade(pair, "BUY", price, 0, "momentum")

# ================================
# LOOP
# ================================
def trading_loop():
    logger.info("🚀 BOT INICIADO")

    while True:
        try:
            manage_positions()
            scan_market()
        except Exception as e:
            logger.error(f"💥 LOOP ERROR: {e}")

        time.sleep(15)

# ================================
# START
# ================================
@app.on_event("startup")
def start_bot():
    logger.info("🔥 STARTUP")

    init_db()

    thread = threading.Thread(target=trading_loop, daemon=True)
    thread.start()
