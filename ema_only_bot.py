import os
import time
import pandas as pd
import numpy as np
from ta.trend import EMAIndicator
from dotenv import load_dotenv
from oandapyV20 import API
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.accounts as accounts

# ===== Load Environment Variables =====
load_dotenv()
OANDA_API_KEY = os.getenv("OANDA_API_KEY")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
client = API(access_token=OANDA_API_KEY, environment="practice")

PAIR = "GBP_USD"
GRANULARITY = "M15"
STOP_LOSS_PIPS = 30
TAKE_PROFIT_PIPS = 60
RISK_PERCENT = 0.05  # 5% risk per trade


# ===== Get Candle Data =====
def get_candles():
    params = {"granularity": GRANULARITY, "count": 200, "price": "M"}
    r = instruments.InstrumentsCandles(instrument=PAIR, params=params)
    data = client.request(r)

    prices = []
    for candle in data["candles"]:
        if candle["complete"]:
            prices.append([
                candle["time"],
                float(candle["mid"]["o"]),
                float(candle["mid"]["h"]),
                float(candle["mid"]["l"]),
                float(candle["mid"]["c"]),
            ])
    df = pd.DataFrame(prices, columns=["time", "open", "high", "low", "close"])
    df["EMA4"] = EMAIndicator(df["close"], window=4).ema_indicator()
    df["EMA50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    return df


# ===== Position Sizing =====
def calculate_units(balance, stop_loss_pips):
    risk_amount = balance * RISK_PERCENT
    pip_value = 0.0001  # GBPUSD pip value in USD for 1 standard lot (100k units)
    units = risk_amount / (stop_loss_pips * pip_value)
    return int(units)


# ===== Place Trade =====
def place_trade(side, price, balance):
    units = calculate_units(balance, STOP_LOSS_PIPS)
    if side == "sell":
        units *= -1

    sl_price = price - (STOP_LOSS_PIPS * 0.0001) if side == "buy" else price + (STOP_LOSS_PIPS * 0.0001)
    tp_price = price + (TAKE_PROFIT_PIPS * 0.0001) if side == "buy" else price - (TAKE_PROFIT_PIPS * 0.0001)

    order_data = {
        "order": {
            "instrument": PAIR,
            "units": str(units),
            "type": "MARKET",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{sl_price:.5f}"},
            "takeProfitOnFill": {"price": f"{tp_price:.5f}"},
        }
    }

    r = orders.OrderCreate(OANDA_ACCOUNT_ID, data=order_data)
    client.request(r)
    print(f"[TRADE] {side.upper()} order placed | SL={sl_price:.5f} | TP={tp_price:.5f}")


# ===== Get Balance =====
def get_balance():
    r = accounts.AccountDetails(OANDA_ACCOUNT_ID)
    details = client.request(r)
    return float(details["account"]["balance"])


# ===== Signal Logic (EMA Cross Only) =====
def check_signals(df):
    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Bullish crossover: EMA4 crosses above EMA50
    if prev["EMA4"] < prev["EMA50"] and last["EMA4"] > last["EMA50"]:
        print("[SIGNAL] BUY confirmed by EMA crossover ✅")
        balance = get_balance()
        place_trade("buy", last["close"], balance)

    # Bearish crossover: EMA4 crosses below EMA50
    elif prev["EMA4"] > prev["EMA50"] and last["EMA4"] < last["EMA50"]:
        print("[SIGNAL] SELL confirmed by EMA crossover 🔻")
        balance = get_balance()
        place_trade("sell", last["close"], balance)

    else:
        print("[WAIT] No valid EMA crossover yet...")


# ===== Main Loop =====
if __name__ == "__main__":
    print("[BOT ACTIVE] OANDA Demo | GBPUSD M15 | Cipher 313 EMA-Only System")
    while True:
        try:
            df = get_candles()
            check_signals(df)
            time.sleep(60 * 15)  # wait for next candle (15min)
        except Exception as e:
            print(f"[ERROR] {e}")
            time.sleep(60)
