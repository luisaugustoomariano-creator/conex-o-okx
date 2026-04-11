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
from typing import Any, Dict, List, Optional

app = FastAPI(title="Jarvis Cripto Bot API", version="2.0.0")

# =========================================
# 🔥 CORS
# =========================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================
# LOGGER
# =========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

OKX_BASE = "https://www.okx.com"

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# =========================================
# CONFIG ORIGINAL (MANTIDA)
# =========================================
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

positions: Dict[str, Dict[str, float]] = {}

# =========================================
# BOT STATE
# =========================================
bot_running = False
bot_thread: Optional[threading.Thread] = None


# =========================================
# HELPERS DE RESPOSTA (PADRÃO JSON)
# =========================================
def api_success(data: Any = None, message: str = "ok") -> Dict[str, Any]:
    return {
        "ok": True,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def api_error(message: str, data: Any = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


# =========================================
# DATABASE
# =========================================
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

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades (created_at DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_pair ON trades (pair)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_action ON trades (action)")

    conn.commit()
    cursor.close()
    conn.close()


def log_trade(pair: str, action: str, price: float, pnl: float, reason: str):
    logger.info(f"TRADE → {pair} {action} price={price} pnl={pnl} reason={reason}")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO trades (pair, action, price, pnl, reason)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (pair, action, price, pnl, reason)
    )

    conn.commit()
    cursor.close()
    conn.close()


# =========================================
# OKX AUTH
# =========================================
def sign(message: str, secret: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), message.encode(), digestmod="sha256").digest()
    ).decode()


def get_headers(method: str, endpoint: str, body: str = "") -> Dict[str, str]:
    if not OKX_API_KEY or not OKX_SECRET_KEY or not OKX_PASSPHRASE:
        raise RuntimeError("Credenciais da OKX não configuradas no ambiente")

    timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    message = timestamp + method + endpoint + body
    signature = sign(message, OKX_SECRET_KEY)

    return {
        "OK-ACCESS-KEY": OKX_API_KEY,
        "OK-ACCESS-SIGN": signature,
        "OK-ACCESS-TIMESTAMP": timestamp,
        "OK-ACCESS-PASSPHRASE": OKX_PASSPHRASE,
        "Content-Type": "application/json",
    }


# =========================================
# BALANCE
# =========================================
def get_balance(asset: str) -> float:
    try:
        endpoint = "/api/v5/account/balance"
        url = OKX_BASE + endpoint
        headers = get_headers("GET", endpoint)
        res = requests.get(url, headers=headers, timeout=20).json()

        for acc in res.get("data", [{}])[0].get("details", []):
            if acc.get("ccy") == asset:
                return float(acc.get("availBal", 0) or 0)
    except Exception as e:
        logger.error(f"Balance error ({asset}): {e}")

    return 0.0


# =========================================
# ORDER SIZE
# =========================================
def calculate_order_size() -> float:
    balance = get_balance("USDT")

    if balance <= 0:
        return 0

    size = balance * RISK_PER_TRADE

    if size < MIN_ORDER_USDT:
        size = MIN_ORDER_USDT

    if size > balance:
        size = balance * 0.99

    return round(size, 2)


# =========================================
# ORDER
# =========================================
def place_order(side: str, price: float, pair: str) -> Dict[str, Any]:
    logger.info(f"ORDER → {side.upper()} {pair} @ {price}")

    if DRY_RUN:
        return {"code": "0", "msg": "dry_run"}

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
            "tgtCcy": "quote_ccy",
        }
    else:
        base_asset = pair.split("-")[0]
        balance = get_balance(base_asset)

        if balance <= 0:
            return {"code": "1", "msg": "saldo insuficiente para venda"}

        size = f"{balance * 0.995:.6f}"

        body = {
            "instId": pair,
            "tdMode": "cash",
            "side": "sell",
            "ordType": "market",
            "sz": size,
        }

    body_str = json.dumps(body)
    headers = get_headers("POST", endpoint, body_str)

    response = requests.post(url, headers=headers, data=body_str, timeout=20)
    return response.json()


# =========================================
# MARKET
# =========================================
def get_candles(pair: str, timeframe: str = "1m", limit: int = 5):
    url = f"{OKX_BASE}/api/v5/market/candles?instId={pair}&bar={timeframe}&limit={limit}"
    res = requests.get(url, timeout=20).json()

    candles = res.get("data", [])
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    return closes, volumes


# =========================================
# TREND
# =========================================
def get_trend(pair: str) -> str:
    closes, _ = get_candles(pair, "5m", 20)

    if len(closes) < 20:
        return "down"

    closes = list(reversed(closes))

    sma_short = sum(closes[-5:]) / 5
    sma_long = sum(closes) / 20

    return "up" if sma_short > sma_long else "down"


# =========================================
# MANAGE POSITIONS
# =========================================
def manage_positions():
    global positions

    for pair in list(positions.keys()):
        closes, _ = get_candles(pair)
        if not closes:
            continue

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


# =========================================
# SCAN MARKET
# =========================================
def scan_market():
    global positions

    for pair in PAIRS:
        if pair in positions:
            continue

        trend = get_trend(pair)
        if trend != "up":
            continue

        closes, volumes = get_candles(pair)
        if len(closes) < 2 or len(volumes) < 2:
            continue

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
                    "max_price": price,
                }
                log_trade(pair, "BUY", price, 0, "momentum")


# =========================================
# LOOP CONTROLADO
# =========================================
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


# =========================================
# MÉTRICAS
# =========================================
def get_total_pnl() -> float:
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT COALESCE(SUM(pnl), 0)
            FROM trades
            WHERE action = 'SELL'
            """
        )

        total = cursor.fetchone()[0] or 0

        cursor.close()
        conn.close()

        return float(total)
    except Exception as e:
        logger.error(f"PNL calc error: {e}")
        return 0.0


def get_trade_stats() -> Dict[str, Any]:
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT
            COUNT(*) AS total_trades,
            COUNT(*) FILTER (WHERE action = 'BUY') AS total_buys,
            COUNT(*) FILTER (WHERE action = 'SELL') AS total_sells,
            COUNT(*) FILTER (WHERE action = 'SELL' AND pnl > 0) AS winning_trades,
            COUNT(*) FILTER (WHERE action = 'SELL' AND pnl < 0) AS losing_trades,
            COALESCE(SUM(pnl) FILTER (WHERE action = 'SELL'), 0) AS total_pnl,
            COALESCE(AVG(pnl) FILTER (WHERE action = 'SELL'), 0) AS avg_pnl
        FROM trades
        """
    )

    row = cursor.fetchone()

    cursor.execute(
        """
        SELECT pair, pnl, created_at
        FROM trades
        WHERE action = 'SELL'
        ORDER BY pnl DESC
        LIMIT 1
        """
    )
    best = cursor.fetchone()

    cursor.execute(
        """
        SELECT pair, pnl, created_at
        FROM trades
        WHERE action = 'SELL'
        ORDER BY pnl ASC
        LIMIT 1
        """
    )
    worst = cursor.fetchone()

    cursor.close()
    conn.close()

    total_sells = int(row[2] or 0)
    winning = int(row[3] or 0)
    win_rate = (winning / total_sells * 100) if total_sells > 0 else 0.0

    return {
        "total_trades": int(row[0] or 0),
        "total_buys": int(row[1] or 0),
        "total_sells": total_sells,
        "winning_trades": winning,
        "losing_trades": int(row[4] or 0),
        "win_rate": round(win_rate, 2),
        "total_pnl": float(row[5] or 0),
        "avg_pnl": float(row[6] or 0),
        "best_trade": {
            "pair": best[0],
            "pnl": float(best[1]),
            "time": best[2].isoformat(),
        } if best else None,
        "worst_trade": {
            "pair": worst[0],
            "pnl": float(worst[1]),
            "time": worst[2].isoformat(),
        } if worst else None,
    }


def get_pnl_history(interval: str = "day", limit: int = 50) -> List[Dict[str, Any]]:
    if interval not in {"hour", "day"}:
        interval = "day"

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        f"""
        SELECT
            date_trunc('{interval}', created_at) AS bucket,
            COALESCE(SUM(pnl) FILTER (WHERE action = 'SELL'), 0) AS pnl_periodo,
            COALESCE(SUM(SUM(pnl) FILTER (WHERE action = 'SELL'))
                OVER (ORDER BY date_trunc('{interval}', created_at)), 0) AS pnl_acumulado
        FROM trades
        GROUP BY 1
        ORDER BY bucket DESC
        LIMIT %s
        """,
        (limit,),
    )

    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    rows.reverse()

    return [
        {
            "time": r[0].isoformat(),
            "pnl_period": float(r[1] or 0),
            "pnl_cumulative": float(r[2] or 0),
        }
        for r in rows
    ]


# =========================================
# API
# =========================================
@app.get("/health")
def health():
    return api_success({"service": "jarvis-cripto-backend", "status": "online"})


@app.get("/dashboard")
def dashboard():
    data = {
        "balance": get_balance("USDT"),
        "pnl": get_total_pnl(),
        "status": "ON" if bot_running else "OFF",
        "open_positions": len(positions),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return api_success(data)


@app.post("/bot/start")
def start_bot():
    global bot_running, bot_thread

    if bot_running:
        return api_success({"status": "ON"}, "Jarvis: bot já estava em execução")

    bot_running = True
    bot_thread = threading.Thread(target=controlled_loop, daemon=True)
    bot_thread.start()

    return api_success({"status": "ON"}, "Jarvis: bot iniciado com sucesso")


@app.post("/bot/stop")
def stop_bot():
    global bot_running
    bot_running = False
    return api_success({"status": "OFF"}, "Jarvis: bot parado")


@app.get("/logs")
def logs(
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    pair: Optional[str] = Query(default=None),
    action: Optional[str] = Query(default=None),
    reason: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=50),
):
    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT pair, action, price, pnl, reason, created_at
        FROM trades
    """

    conditions = []
    values: List[Any] = []

    if start:
        conditions.append("created_at >= %s")
        values.append(start)

    if end:
        conditions.append("created_at <= %s")
        values.append(end)

    if pair:
        conditions.append("pair = %s")
        values.append(pair)

    if action:
        conditions.append("action = %s")
        values.append(action.upper())

    if reason:
        conditions.append("reason ILIKE %s")
        values.append(f"%{reason}%")

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY created_at DESC LIMIT %s"
    values.append(limit)

    cursor.execute(query, values)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    payload = [
        {
            "pair": r[0],
            "action": r[1],
            "price": float(r[2]),
            "pnl": float(r[3]),
            "reason": r[4],
            "time": r[5].isoformat(),
        }
        for r in rows
    ]

    return api_success({"items": payload, "count": len(payload), "limit": limit})


@app.get("/stats/summary")
def stats_summary():
    try:
        stats = get_trade_stats()
        stats["bot_status"] = "ON" if bot_running else "OFF"
        stats["open_positions"] = len(positions)
        return api_success(stats)
    except Exception as e:
        logger.error(f"stats_summary error: {e}")
        return api_error("Jarvis: erro ao calcular estatísticas", {"detail": str(e)})


@app.get("/stats/pnl-history")
def stats_pnl_history(
    interval: str = Query(default="day", pattern="^(hour|day)$"),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        history = get_pnl_history(interval=interval, limit=limit)
        return api_success({"interval": interval, "items": history, "count": len(history)})
    except Exception as e:
        logger.error(f"stats_pnl_history error: {e}")
        return api_error("Jarvis: erro ao carregar histórico de PNL", {"detail": str(e)})


@app.on_event("startup")
def startup():
    init_db()
    logger.info("Jarvis backend inicializado com sucesso")

