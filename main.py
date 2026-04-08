from fastapi import FastAPI, Header, HTTPException
import requests
import os
import time
import hmac
import base64

app = FastAPI()

OKX_BASE = "https://www.okx.com"

# 🔐 ENV VARIABLES CORRETAS
API_TOKEN = os.getenv("API_TOKEN")
OKX_API_KEY = os.getenv("OKX_API_KEY")
OKX_SECRET_KEY = os.getenv("OKX_SECRET_KEY")
OKX_PASSPHRASE = os.getenv("OKX_PASSPHRASE")


def validate_token(auth):
    if auth != f"Bearer {API_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")


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


@app.get("/")
def home():
    return {"status": "running"}


@app.get("/market")
def get_market(instId: str):
    url = f"{OKX_BASE}/api/v5/market/ticker?instId={instId}"
    return requests.get(url).json()

    url = f"{OKX_BASE}/api/v5/market/ticker?instId={instId}"
    return requests.get(url).json()


@app.get("/account")
def get_account(authorization: str = Header(None)):
    validate_token(authorization)

    endpoint = "/api/v5/account/balance"
    url = OKX_BASE + endpoint

    headers = get_headers("GET", endpoint)

    response = requests.get(url, headers=headers)

    return response.json()


@app.post("/decision")
def decision(data: dict, authorization: str = Header(None)):
    validate_token(authorization)

    price = float(data["price"])
    change = float(data["change"])

    if change > 1:
        return {"action": "BUY"}
    elif change < -1:
        return {"action": "SELL"}
    else:
        return {"action": "HOLD"}
