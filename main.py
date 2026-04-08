from fastapi import FastAPI, Header, HTTPException
import requests
import os
import time
import hmac
import base64
import threading
import sqlite3

app = FastAPI()

OKX_BASE = "https://www.okx.com"

# ================================
# 🔐 ENV VARIABLES
# ================================
API_TOKEN = os.getenv("API_TOKEN")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")

# ================================
# ⚙️ CONFIG SCALPING
# ================================
PAIR = "BTC-USDT"
TAKE_PROFIT = 0.5   # %
STOP_LOSS = -0.5    # %

# ================================
# 🧠 ESTADO (memória)
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
# 🔐 AUTH
# ================================
def validate_token(auth):
    if auth != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

# ================================
# 🔐 OKX SIGNATURE
# ================================
def sign(message, secret):
    return base64.b64encode(
        hmac.new(secret.encode(), message.encode(), digestmod="sha256").digest()
    ).decode()

def get_headers(method, endpoint, body=""):
    timestamp = str(time.time())
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
# 🟢 HEALTH
# ================================
@app.get("/")
def home():
    return {"status": "running"}

# ================================
# 📊 MARKET
# ================================
@app.get("/market")
def get_market(instId: str):
    url = f"{OKX_BASE}/api/v5/market/ticker?instId={instId}"
    return requests.get(url).json()

# ================================
# 📊 CANDLES
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
@app.get("/scalp")
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
            position["open"] = True
            position["entry_price"] = current_price
            position["pair"] = PAIR

            log_trade(PAIR, "BUY", current_price, 0, "entry")

            return {
                "action": "BUY",
                "price": current_price,
                "reason": "micro uptrend + volume"
            }

        return {"action": "HOLD"}

    # ============================
    # 🔴 EXIT
    # ============================
    entry = position["entry_price"]
    pnl = ((current_price - entry) / entry) * 100

    if pnl >= TAKE_PROFIT:
        position["open"] = False

        log_trade(PAIR, "SELL", current_price, pnl, "take profit")

        return {
            "action": "SELL",
            "price": current_price,
            "pnl": round(pnl, 3),
            "reason": "take profit"
        }

    if pnl <= STOP_LOSS:
        position["open"] = False

        log_trade(PAIR, "SELL", current_price, pnl, "stop loss")

        return {
            "action": "SELL",
            "price": current_price,
            "pnl": round(pnl, 3),
            "reason": "stop loss"
        }

    if downtrend:
        position["open"] = False

        log_trade(PAIR, "SELL", current_price, pnl, "trend reversal")

        return {
            "action": "SELL",
            "price": current_price,
            "pnl": round(pnl, 3),
            "reason": "trend reversal"
        }

    return {
        "action": "HOLD",
        "pnl": round(pnl, 3)
    }

# ================================
# 📊 HISTÓRICO
# ================================
@app.get("/trades")
def get_trades():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 50")
    rows = cursor.fetchall()

    conn.close()

    return [
        {
            "id": r[0],
            "pair": r[1],
            "action": r[2],
            "price": r[3],
            "pnl": r[4],
            "reason": r[5],
            "created_at": r[6]
        }
        for r in rows
    ]

# ================================
# 📈 PNL TOTAL
# ================================
@app.get("/pnl")
def get_pnl():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    cursor.execute("SELECT SUM(pnl) FROM trades WHERE action = 'SELL'")
    total = cursor.fetchone()[0]

    conn.close()

    return {"total_pnl": round(total or 0, 4)}

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
