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
from typing import Any, Dict, List, Optional, Tuple

app = FastAPI(title="Jarvis Cripto Bot API", version="2.1.0")

# ====
# 🔥 CORS
# ====
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====
# LOGGER
# ====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

OKX_BASE = "https://www.okx.com"

OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# ====
# CONFIG BASE
# ====
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
ENTRY_LIMIT_OFFSET = 0.001
ENTRY_LIMIT_TIMEOUT_SECONDS = 10
ENTRY_LIMIT_POLL_INTERVAL_SECONDS = 1

DEFAULT_RISK_MODE = "medium"
RISK_MODES = {"low", "medium", "high"}

RISK_PROFILES: Dict[str, Dict[str, float]] = {
    # Mais conservador: menos entradas, maior confirmação, menor risco por posição
    "low": {
        "risk_per_trade": 0.02,
        "stop_loss": 0.009,
        "trailing_stop": 0.024,
        "min_delta": 0.012,
        "volume_multiplier": 1.25,
        "min_volatility": 0.0055,
        "max_volatility": 0.060,
        "trend_fast": 5,
        "trend_slow": 24,
        "min_trend_gap": 0.0015,
        "confirmation_window": 4,
        "required_green_candles": 3,
    },
    # Estratégia atual (balanceada)
    "medium": {
        "risk_per_trade": 0.04,
        "stop_loss": 0.010,
        "trailing_stop": 0.030,
        "min_delta": 0.010,
        "volume_multiplier": 1.10,
        "min_volatility": 0.0040,
        "max_volatility": 0.075,
        "trend_fast": 5,
        "trend_slow": 20,
        "min_trend_gap": 0.0008,
        "confirmation_window": 3,
        "required_green_candles": 2,
    },
    # Mais agressivo, mas ainda com filtros de qualidade (trend + momentum + volume + volatilidade controlada)
    "high": {
        "risk_per_trade": 0.06,
        "stop_loss": 0.013,
        "trailing_stop": 0.038,
        "min_delta": 0.007,
        "volume_multiplier": 1.03,
        "min_volatility": 0.0030,
        "max_volatility": 0.095,
        "trend_fast": 4,
        "trend_slow": 18,
        "min_trend_gap": 0.0003,
        "confirmation_window": 3,
        "required_green_candles": 2,
    },
}

positions: Dict[str, Dict[str, float]] = {}

# ====
# BOT STATE
# ====
bot_running = False
bot_thread: Optional[threading.Thread] = None
active_risk_mode = DEFAULT_RISK_MODE
state_lock = threading.Lock()


# ====
# HELPERS DE RESPOSTA (PADRÃO JSON)
# ====
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


# ====
# RISK MODE
# ====
def normalize_risk_mode(risk_mode: Optional[str]) -> str:
    mode = (risk_mode or DEFAULT_RISK_MODE).strip().lower()
    if mode not in RISK_MODES:
        raise ValueError(f"risk_mode inválido: {risk_mode}. Use low, medium ou high")
    return mode


def get_active_risk_mode() -> str:
    with state_lock:
        return active_risk_mode


def get_risk_profile(risk_mode: Optional[str] = None) -> Dict[str, float]:
    mode = normalize_risk_mode(risk_mode) if risk_mode else get_active_risk_mode()
    return RISK_PROFILES[mode]


def set_active_risk_mode(risk_mode: str) -> str:
    global active_risk_mode
    mode = normalize_risk_mode(risk_mode)
    with state_lock:
        active_risk_mode = mode
    logger.info(f"RISK MODE alterado para {mode}")
    return mode


def get_risk_mode_payload() -> Dict[str, Any]:
    current = get_active_risk_mode()
    return {
        "risk_mode": current,
        "available_modes": sorted(list(RISK_MODES)),
        "profile": RISK_PROFILES[current],
    }


# ====
# DATABASE
# ====
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


# ====
# OKX AUTH
# ====
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


# ====
# BALANCE
# ====
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


def okx_request(method: str, endpoint: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    payload = json.dumps(body) if body else ""
    headers = get_headers(method, endpoint, payload)
    url = OKX_BASE + endpoint

    if method == "GET":
        response = requests.get(url, headers=headers, timeout=20)
    elif method == "POST":
        response = requests.post(url, headers=headers, data=payload, timeout=20)
    else:
        raise ValueError(f"Método HTTP não suportado: {method}")

    return response.json()


# ====
# ORDER SIZE
# ====
def calculate_order_size(risk_mode: Optional[str] = None) -> float:
    profile = get_risk_profile(risk_mode)
    balance = get_balance("USDT")

    if balance <= 0:
        return 0

    size = balance * profile["risk_per_trade"]

    if size < MIN_ORDER_USDT:
        size = MIN_ORDER_USDT

    if size > balance:
        size = balance * 0.99

    return round(size, 2)


# ====
# ORDER
# ====
def calculate_entry_limit_price(side: str, current_price: float) -> float:
    if side == "buy":
        return round(current_price * (1 - ENTRY_LIMIT_OFFSET), 8)
    return round(current_price * (1 + ENTRY_LIMIT_OFFSET), 8)


def extract_order_data(order_response: Dict[str, Any]) -> Dict[str, Any]:
    data = order_response.get("data", [])
    if data and isinstance(data, list):
        return data[0]
    return {}


def get_order_state(pair: str, ord_id: str) -> Dict[str, Any]:
    endpoint = f"/api/v5/trade/order?instId={pair}&ordId={ord_id}"
    return okx_request("GET", endpoint)


def cancel_existing_order(pair: str, ord_id: str) -> Dict[str, Any]:
    endpoint = "/api/v5/trade/cancel-order"
    return okx_request("POST", endpoint, {"instId": pair, "ordId": ord_id})


def build_order_body(
    side: str,
    price: float,
    pair: str,
    risk_mode: Optional[str] = None,
    ord_type: str = "market",
) -> Dict[str, Any]:
    mode = normalize_risk_mode(risk_mode) if risk_mode else get_active_risk_mode()

    if side == "buy":
        size = calculate_order_size(mode)
        if ord_type == "limit":
            base_size = size / price if price else 0
            return {
                "instId": pair,
                "tdMode": "cash",
                "side": "buy",
                "ordType": "limit",
                "sz": f"{base_size:.6f}",
                "px": f"{price:.8f}",
            }

        return {
            "instId": pair,
            "tdMode": "cash",
            "side": "buy",
            "ordType": "market",
            "sz": str(size),
            "tgtCcy": "quote_ccy",
        }

    base_asset = pair.split("-")[0]
    balance = get_balance(base_asset)

    if balance <= 0:
        return {}

    body = {
        "instId": pair,
        "tdMode": "cash",
        "side": "sell",
        "ordType": ord_type,
        "sz": f"{balance * 0.995:.6f}",
    }

    if ord_type == "limit":
        body["px"] = f"{price:.8f}"

    return body


def submit_order(body: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = "/api/v5/trade/order"
    return okx_request("POST", endpoint, body)


def wait_for_limit_fill(pair: str, ord_id: str) -> Dict[str, Any]:
    deadline = time.monotonic() + ENTRY_LIMIT_TIMEOUT_SECONDS
    latest_state: Dict[str, Any] = {}

    while time.monotonic() < deadline:
        status_response = get_order_state(pair, ord_id)
        latest_state = extract_order_data(status_response)
        state = latest_state.get("state")

        if state == "filled":
            return latest_state

        if state in {"canceled", "mmp_canceled"}:
            return latest_state

        time.sleep(ENTRY_LIMIT_POLL_INTERVAL_SECONDS)

    return latest_state


def place_entry_order(side: str, price: float, pair: str, risk_mode: Optional[str] = None) -> Dict[str, Any]:
    mode = normalize_risk_mode(risk_mode) if risk_mode else get_active_risk_mode()
    started_at = time.monotonic()
    limit_price = calculate_entry_limit_price(side, price)
    limit_body = build_order_body(side, limit_price, pair, mode, ord_type="limit")

    if not limit_body:
        return {"code": "1", "msg": "saldo insuficiente para ordem"}

    logger.info(
        f"ORDER -> {side.upper()} {pair} type=LIMIT trigger_price={price} limit_price={limit_price} "
        f"fallback=False (risk_mode={mode})"
    )

    if DRY_RUN:
        logger.info(
            f"ORDER RESULT -> {side.upper()} {pair} type=LIMIT fallback=False execution_time={time.monotonic() - started_at:.2f}s"
        )
        return {"code": "0", "msg": "dry_run", "data": [{"ordType": "limit"}]}

    limit_response = submit_order(limit_body)
    limit_data = extract_order_data(limit_response)
    ord_id = limit_data.get("ordId")

    if limit_response.get("code") != "0" or not ord_id:
        logger.info(
            f"ORDER RESULT -> {side.upper()} {pair} type=LIMIT fallback=False execution_time={time.monotonic() - started_at:.2f}s"
        )
        return limit_response

    final_state = wait_for_limit_fill(pair, ord_id)
    if final_state.get("state") == "filled":
        logger.info(
            f"ORDER RESULT -> {side.upper()} {pair} type=LIMIT fallback=False execution_time={time.monotonic() - started_at:.2f}s"
        )
        return limit_response

    cancel_response = cancel_existing_order(pair, ord_id)
    if cancel_response.get("code") != "0":
        latest_state = extract_order_data(get_order_state(pair, ord_id))
        if latest_state.get("state") == "filled":
            logger.info(
                f"ORDER RESULT -> {side.upper()} {pair} type=LIMIT fallback=False execution_time={time.monotonic() - started_at:.2f}s"
            )
            return limit_response
        logger.error(f"Falha ao cancelar ordem LIMIT {ord_id} de {pair}: {cancel_response}")
        return cancel_response

    latest_state = extract_order_data(get_order_state(pair, ord_id))
    if latest_state.get("state") == "filled":
        logger.info(
            f"ORDER RESULT -> {side.upper()} {pair} type=LIMIT fallback=False execution_time={time.monotonic() - started_at:.2f}s"
        )
        return limit_response

    if latest_state.get("state") not in {"", None, "canceled", "mmp_canceled"}:
        logger.error(f"Ordem LIMIT {ord_id} de {pair} ainda está aberta após cancelamento: {latest_state}")
        return {"code": "1", "msg": "ordem limit ainda aberta após cancelamento", "data": [latest_state]}

    logger.info(f"ORDER -> {side.upper()} {pair} type=MARKET fallback=True (risk_mode={mode})")
    market_body = build_order_body(side, price, pair, mode, ord_type="market")
    market_response = submit_order(market_body)
    logger.info(
        f"ORDER RESULT -> {side.upper()} {pair} type=MARKET fallback=True execution_time={time.monotonic() - started_at:.2f}s"
    )
    return market_response


def place_order(
    side: str,
    price: float,
    pair: str,
    risk_mode: Optional[str] = None,
    is_exit: bool = False,
) -> Dict[str, Any]:
    mode = normalize_risk_mode(risk_mode) if risk_mode else get_active_risk_mode()
    started_at = time.monotonic()

    if not is_exit:
        return place_entry_order(side, price, pair, mode)
    logger.info(f"ORDER → {side.upper()} {pair} @ {price} (risk_mode={mode})")

    if DRY_RUN:
        logger.info(
            f"ORDER RESULT -> {side.upper()} {pair} type=MARKET fallback=False execution_time={time.monotonic() - started_at:.2f}s"
        )
        return {"code": "0", "msg": "dry_run", "data": [{"ordType": "market"}]}

    body = build_order_body(side, price, pair, mode, ord_type="market")
    if not body:
        return {"code": "1", "msg": "saldo insuficiente para venda"}

    result = submit_order(body)
    logger.info(
        f"ORDER RESULT -> {side.upper()} {pair} type=MARKET fallback=False execution_time={time.monotonic() - started_at:.2f}s"
    )
    return result


# ====
# MARKET
# ====
def get_candles(pair: str, timeframe: str = "1m", limit: int = 6) -> Tuple[List[float], List[float]]:
    url = f"{OKX_BASE}/api/v5/market/candles?instId={pair}&bar={timeframe}&limit={limit}"
    res = requests.get(url, timeout=20).json()

    candles = res.get("data", [])
    closes = [float(c[4]) for c in candles]
    volumes = [float(c[5]) for c in candles]

    return closes, volumes


# ====
# TREND
# ====
def get_trend(pair: str, fast: int, slow: int) -> Dict[str, float]:
    closes, _ = get_candles(pair, "5m", slow)

    if len(closes) < slow:
        return {"is_up": False, "gap": 0.0}

    closes = list(reversed(closes))

    sma_short = sum(closes[-fast:]) / fast
    sma_long = sum(closes[-slow:]) / slow

    gap = (sma_short - sma_long) / sma_long if sma_long else 0.0
    return {"is_up": sma_short > sma_long, "gap": gap}


def count_green_candles(closes: List[float], window: int) -> int:
    if len(closes) < window + 1:
        return 0

    recent_chronological = list(reversed(closes[: window + 1]))
    greens = 0

    for idx in range(1, len(recent_chronological)):
        if recent_chronological[idx] > recent_chronological[idx - 1]:
            greens += 1

    return greens


# ====
# MANAGE POSITIONS
# ====
def manage_positions(risk_mode: Optional[str] = None):
    global positions

    profile = get_risk_profile(risk_mode)
    stop_loss = profile["stop_loss"]
    trailing_stop = profile["trailing_stop"]

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

        if current_price <= entry * (1 - stop_loss):
            order = place_order("sell", current_price, pair, risk_mode, is_exit=True)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, f"stop_loss_{get_active_risk_mode()}")
                del positions[pair]

        elif current_price <= pos["max_price"] * (1 - trailing_stop):
            order = place_order("sell", current_price, pair, risk_mode, is_exit=True)
            if order.get("code") == "0":
                log_trade(pair, "SELL", current_price, profit, f"trailing_stop_{get_active_risk_mode()}")
                del positions[pair]


# ====
# SCAN MARKET
# ====
def scan_market(risk_mode: Optional[str] = None):
    global positions

    mode = normalize_risk_mode(risk_mode) if risk_mode else get_active_risk_mode()
    profile = get_risk_profile(mode)

    confirmation_window = int(profile["confirmation_window"])
    required_green_candles = int(profile["required_green_candles"])

    for pair in PAIRS:
        if pair in positions:
            continue

        trend_data = get_trend(
            pair,
            int(profile["trend_fast"]),
            int(profile["trend_slow"]),
        )

        if not trend_data["is_up"] or trend_data["gap"] < profile["min_trend_gap"]:
            continue

        candle_limit = max(confirmation_window + 2, 6)
        closes, volumes = get_candles(pair, "1m", candle_limit)
        if len(closes) < candle_limit or len(volumes) < candle_limit:
            continue

        price = closes[0]
        prev_price = closes[1]

        delta = (price - prev_price) / prev_price if prev_price else 0.0
        volume_reference = sum(volumes[1:]) / (len(volumes) - 1)
        volume_boost = volumes[0] >= (volume_reference * profile["volume_multiplier"])
        volatility = (max(closes) - min(closes)) / price if price else 0.0
        green_candles = count_green_candles(closes, confirmation_window)

        quality_filters_ok = (
            delta >= profile["min_delta"]
            and volume_boost
            and profile["min_volatility"] <= volatility <= profile["max_volatility"]
            and green_candles >= required_green_candles
        )

        if not quality_filters_ok:
            continue

        order = place_order("buy", price, pair, mode)

        if order.get("code") == "0":
            positions[pair] = {
                "entry_price": price,
                "max_price": price,
            }
            log_trade(pair, "BUY", price, 0, f"momentum_{mode}")


# ====
# LOOP CONTROLADO
# ====
def controlled_loop():
    global bot_running
    logger.info("BOT STARTED")

    while bot_running:
        try:
            mode = get_active_risk_mode()
            manage_positions(mode)
            scan_market(mode)
        except Exception as e:
            logger.error(f"Loop error: {e}")

        time.sleep(15)

    logger.info("BOT STOPPED")


# ====
# MÉTRICAS
# ====
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


# ====
# API
# ====
@app.get("/health")
def health():
    return api_success({"service": "jarvis-cripto-backend", "status": "online"})


@app.get("/dashboard")
def dashboard():
    mode = get_active_risk_mode()
    profile = get_risk_profile(mode)

    data = {
        "balance": get_balance("USDT"),
        "pnl": get_total_pnl(),
        "status": "ON" if bot_running else "OFF",
        "open_positions": len(positions),
        "risk_mode": mode,
        "risk_per_trade": profile["risk_per_trade"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return api_success(data)


@app.get("/bot/risk-mode")
def get_bot_risk_mode():
    return api_success(get_risk_mode_payload())


@app.post("/bot/risk-mode")
def update_bot_risk_mode(
    risk_mode: str = Query(..., pattern="^(low|medium|high)$")
):
    try:
        mode = set_active_risk_mode(risk_mode)
        return api_success(get_risk_mode_payload(), f"Jarvis: modo de risco atualizado para {mode}")
    except ValueError as e:
        return api_error("Jarvis: risk_mode inválido", {"detail": str(e)})


@app.post("/bot/start")
def start_bot(
    risk_mode: str = Query(default=DEFAULT_RISK_MODE, pattern="^(low|medium|high)$")
):
    global bot_running, bot_thread

    try:
        mode = set_active_risk_mode(risk_mode)
    except ValueError as e:
        return api_error("Jarvis: risk_mode inválido", {"detail": str(e)})

    if bot_running:
        return api_success(
            {"status": "ON", "risk_mode": mode},
            "Jarvis: bot já estava em execução (modo de risco atualizado)"
        )

    bot_running = True
    bot_thread = threading.Thread(target=controlled_loop, daemon=True)
    bot_thread.start()

    return api_success({"status": "ON", "risk_mode": mode}, "Jarvis: bot iniciado com sucesso")


@app.post("/bot/stop")
def stop_bot():
    global bot_running
    bot_running = False
    return api_success({"status": "OFF", "risk_mode": get_active_risk_mode()}, "Jarvis: bot parado")


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
        stats["risk_mode"] = get_active_risk_mode()
        stats["risk_profile"] = get_risk_profile()
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
        return api_success({"interval": interval, "items": history, "count": len(history), "risk_mode": get_active_risk_mode()})
    except Exception as e:
        logger.error(f"stats_pnl_history error: {e}")
        return api_error("Jarvis: erro ao carregar histórico de PNL", {"detail": str(e)})


@app.on_event("startup")
def startup():
    init_db()
    logger.info("Jarvis backend inicializado com sucesso")
