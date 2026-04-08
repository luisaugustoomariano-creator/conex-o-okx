from fastapi import FastAPI, Header, HTTPException
import requests
import os
import time
import hmac
import base64

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
# 🧠 ESTADO (simples - memória)
# ================================
position = {
    "open": False,
    "entry_price": 0,
    "pair": None
}

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
# 📊 CANDLES 1M
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
# 🧠 SCALPING ENGINE
# ================================
@app.get("/scalp")
def scalp():

    global position

    data = get_candles(PAIR)

    if not data:
        return {"action": "NO_DATA"}

    closes, volumes = data

    current_price = closes[0]

    # 📈 tendência curta
    uptrend = closes[2] < closes[1] < closes[0]

    # 📉 tendência reversa
    downtrend = closes[2] > closes[1] > closes[0]

    # 💧 volume crescente
    volume_up = volumes[2] < volumes[1] < volumes[0]

    # ============================
    # 🟢 ENTRADA
    # ============================
    if not position["open"]:
        if uptrend and volume_up:
            position["open"] = True
            position["entry_price"] = current_price
            position["pair"] = PAIR

            return {
                "action": "BUY",
                "price": current_price,
                "reason": "micro uptrend + volume"
            }

        return {"action": "HOLD"}

    # ============================
    # 🔴 SAÍDA (GESTÃO)
    # ============================
    entry = position["entry_price"]

    pnl = ((current_price - entry) / entry) * 100

    # TAKE PROFIT
    if pnl >= TAKE_PROFIT:
        position["open"] = False

        return {
            "action": "SELL",
            "price": current_price,
            "pnl": round(pnl, 3),
            "reason": "take profit"
        }

    # STOP LOSS
    if pnl <= STOP_LOSS:
        position["open"] = False

        return {
            "action": "SELL",
            "price": current_price,
            "pnl": round(pnl, 3),
            "reason": "stop loss"
        }

    # REVERSÃO
    if downtrend:
        position["open"] = False

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
