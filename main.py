from fastapi import FastAPI, Query
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
# CONFIG
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

# Estratégia ajustada (mais oportunidades, mas com filtros de qualidade)
STOP_LOSS = 0.012              # 1.2%
TRAILING_STOP = 0.025          # 2.5%
MIN_DELTA = 0.0075             # 0.75%
MIN_VOLATILITY = 0.006         # 0.6%
MAX_VOLATILITY = 0.03          # 3.0%
VOLUME_MULTIPLIER = 1.25
BREAKOUT_LOOKBACK = 10
MAX_OPEN_POSITIONS = 3
COOLDOWN_MINUTES = 25
BREAKEVEN_TRIGGER = 0.01       # 1.0%

positions = {}
last_exit_times = {}

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

    # Índices simples para consultas mais rápidas
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_action ON trades(action)")

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
    }

# ================================
# BALANCE
# ================================
def get_balance(asset):
    try:
        endpoint = "/api/v5/account/balance"
        url = OKX_BASE + endpoint
        headers = get_headers("GET", endpoint)
        res = requests.get(url, headers=headers, timeout=10).json()

        for acc in res.get("data", [{}])[0].get("details", []):
            if acc.get("ccy") == asset:
                return float(acc.get("availBal", 0))
    except Exception as e:
        logger.error(f"Balance error: {e}")
    return 0.0

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
    logger.info(f"ORDER → {side.upper()} {pair} @ {price}")

    if DRY_RUN:
        return {"code": "0"}

    endpoint = "/api/v5/trade/order"
    url = OKX_BASE + endpoint

    if side == "buy":
        size = calculate_order_size()
        if size <= 0:
            return {"code": "1", "msg": "invalid size"}
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
            return {"code": "1", "msg": "no base balance to sell"}

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

    try:
        response = requests.post(url, headers=headers, data=body_str, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Order error: {e}")
        return {"code": "1", "msg": str(e)}

# ================================
# MARKET
# ================================
def get_candles(pair, timeframe="1m", limit=30):
    try:
        url = f"{OKX_BASE}/api/v5/market/candles?instId={pair}&bar={timeframe}&limit={limit}"
        res = requests.get(url, timeout=10).json()
        candles = res.get("data", [])

        if not candles:
            return [], []

        closes = [float(c[4]) for c in candles]
        volumes = [float(c[5]) for c in candles]
        return closes, volumes
    except Exception as e:
        logger.error(f"Candles error {pair} {timeframe}: {e}")
        return [], []

# ================================
# TREND HELPERS
# ================================
def sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n

def get_trend(pair, timeframe="5m"):
    closes, _ = get_candles(pair, timeframe=timeframe, limit=20)
    if len(closes) < 20:
        return "down"

    closes = list(reversed(closes))  # antigo -> novo
    sma_short = sma(closes, 5)
    sma_long = sma(closes, 20)

    if sma_short is None or sma_long is None:
        return "down"

    return "up" if sma_short > sma_long else "down"

def get_trend_multi_tf(pair):
    trend_5m = get_trend(pair, "5m")
    trend_15m = get_trend(pair, "15m")
    return trend_5m == "up" and trend_15m == "up"

def in_cooldown(pair):
    last_exit = last_exit_times.get(pair)
    if not last_exit:
        return False
    return (time.time() - last_exit) < (COOLDOWN_MINUTES * 60)

# ================================
# MANAGE POSITIONS
# ================================
def manage_positions():
    global positions, last_exit_times

    for pair in list(positions.keys()):
        closes, _ = get_candles(pair, limit=5)
        if not closes:
            continue

        current_price = closes[0]
        pos = positions[pair]
        entry = pos["entry_price"]
        max_price = pos.get("max_price", entry)

        if current_price > max_price:
            pos["max_price"] = current_price
            max_price = current_price

        profit = (current_price - entry) / entry

        # Arma breakeven quando bater alvo mínimo de lucro
        if (not pos.get("breakeven_armed")) and profit >= BREAKEVEN_TRIGGER:
            pos["breakeven_armed"] = True
            pos["stop_price"] = entry
            logger.info(f"{pair} breakeven armed at entry={entry}")

        base_stop = entry * (1 - STOP_LOSS)
        dynamic_stop = max(base_stop, pos.get("stop_price", 0))

        # Stop (normal ou breakeven)
        if current_price <= dynamic_stop:
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                reason = "breakeven" if pos.get("breakeven_armed") and current_price >= entry * 0.999 else "stop_loss"
                log_trade(pair, "SELL", current_price, profit, reason)
                del positions[pair]
                last_exit_times[pair] = time.time()
            continue

        # Trailing stop
        trailing_line = max_price * (1 - TRAILING_STOP)
        if current_price <= trailing_line:
            order = place_order("sell", current_price, pair)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, "trailing_stop")
                del positions[pair]
                last_exit_times[pair] = time.time()

# ================================
# SCAN MARKET
# ================================
def scan_market():
    global positions

    # Limite global de posições
    if len(positions) >= MAX_OPEN_POSITIONS:
        return

    for pair in PAIRS:
        if len(positions) >= MAX_OPEN_POSITIONS:
            break

        if pair in positions:
            continue

        if in_cooldown(pair):
            continue

        # Tendência alinhada em 5m e 15m
        if not get_trend_multi_tf(pair):
            continue

        closes, volumes = get_candles(pair, timeframe="1m", limit=max(30, BREAKOUT_LOOKBACK + 5))
        if len(closes) < BREAKOUT_LOOKBACK + 2 or len(volumes) < BREAKOUT_LOOKBACK + 2:
            continue

        price = closes[0]
        prev_price = closes[1]
        delta = (price - prev_price) / prev_price if prev_price > 0 else 0

        # Breakout: preço atual acima da máxima recente (sem contar candle atual)
        recent_high = max(closes[1:BREAKOUT_LOOKBACK + 1])
        is_breakout = price > recent_high

        # Volume forte
        vol_avg = sum(volumes[1:BREAKOUT_LOOKBACK + 1]) / BREAKOUT_LOOKBACK
        volume_boost = volumes[0] > (vol_avg * VOLUME_MULTIPLIER)

        # Volatilidade em faixa saudável
        local_high = max(closes[:BREAKOUT_LOOKBACK + 1])
        local_low = min(closes[:BREAKOUT_LOOKBACK + 1])
        volatility = (local_high - local_low) / price if price > 0 else 0
        volatility_ok = MIN_VOLATILITY <= volatility <= MAX_VOLATILITY

        should_buy = (
            delta >= MIN_DELTA
            and is_breakout
            and volume_boost
            and volatility_ok
        )

        if should_buy:
            order = place_order("buy", price, pair)
            if order.get("code") == "0":
                positions[pair] = {
                    "entry_price": price,
                    "max_price": price,
                    "breakeven_armed": False,
                    "stop_price": 0.0
                }
                log_trade(pair, "BUY", price, 0, "breakout_momentum")

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
        "pnl": get_total_pnl(),
        "status": "ON" if bot_running else "OFF",
        "open_positions": len(positions)
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

    query += " ORDER BY created_at DESC LIMIT 100"

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
