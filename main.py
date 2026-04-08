from fastapi import FastAPI, Header, HTTPException
import requests
import os
import time
import hmac
import base64

app = FastAPI()

OKX_BASE = "https://www.okx.com"

# 🔐 ENV VARIABLES
API_TOKEN = os.getenv("API_TOKEN")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")


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
# 🟢 HEALTH CHECK
# ================================
@app.get("/")
def home():
    return {"status": "running"}


# ================================
# 📊 MARKET (PÚBLICO)
# ================================
@app.get("/market")
def get_market(instId: str):
    url = f"{OKX_BASE}/api/v5/market/ticker?instId={instId}"
    return requests.get(url).json()


# ================================
# 📊 MULTI MARKET
# ================================
@app.get("/multi-market")
def get_multi_market():
    pairs = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

    results = []

    for pair in pairs:
        url = f"{OKX_BASE}/api/v5/market/ticker?instId={pair}"
        data = requests.get(url).json()

        if data.get("data"):
            price = float(data["data"][0]["last"])
            open24h = float(data["data"][0]["open24h"])

            change = ((price - open24h) / open24h) * 100

            results.append({
                "pair": pair,
                "price": price,
                "change_24h": round(change, 2)
            })

    return results


# ================================
# 🚀 TOP GAINERS
# ================================
@app.get("/top-gainers")
def top_gainers():
    pairs = ["BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT"]

    data = []

    for pair in pairs:
        url = f"{OKX_BASE}/api/v5/market/ticker?instId={pair}"
        res = requests.get(url).json()

        if res.get("data"):
            price = float(res["data"][0]["last"])
            open24h = float(res["data"][0]["open24h"])

            change = ((price - open24h) / open24h) * 100

            data.append({
                "pair": pair,
                "price": price,
                "change": round(change, 2)
            })

    sorted_data = sorted(data, key=lambda x: x["change"], reverse=True)

    return sorted_data


# ================================
# 🔐 ACCOUNT (PRIVADO)
# ================================
@app.get("/account")
def get_account(authorization: str = Header(None)):
    validate_token(authorization)

    endpoint = "/api/v5/account/balance"
    url = OKX_BASE + endpoint

    headers = get_headers("GET", endpoint)

    response = requests.get(url, headers=headers)

    return response.json()


# ================================
# 🧠 DECISION (BÁSICO)
# ================================
@app.post("/decision")
def decision(data: dict, authorization: str = Header(None)):
    validate_token(authorization)

    change = float(data["change"])

    if change > 1:
        return {"action": "BUY"}
    elif change < -1:
        return {"action": "SELL"}
    else:
        return {"action": "HOLD"}


# ================================
# 🧠 DECISION V2 (MELHORADO)
# ================================
@app.post("/decision-v2")
def decision_v2(data: dict, authorization: str = Header(None)):
    validate_token(authorization)

    change = float(data["change"])

    if change > 2:
        return {"action": "STRONG_BUY"}
    elif change > 0.5:
        return {"action": "BUY"}
    elif change < -2:
        return {"action": "STRONG_SELL"}
    elif change < -0.5:
        return {"action": "SELL"}
    else:
        return {"action": "HOLD"}
