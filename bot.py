import os, time
import pandas as pd, numpy as np
from ta.trend import EMAIndicator
from oandapyV20 import API
from oandapyV20.endpoints import instruments
from dotenv import load_dotenv
load_dotenv()

PAIR = "GBP_USD"
STOP_PIPS = 30
TP_PIPS = 60
client = API(access_token=os.getenv("OANDA_API_KEY"))
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")

def get_candles(count=200):
    r = instruments.InstrumentsCandles(instrument=PAIR,
        params={"granularity":"M15","count":count})
    d = client.request(r)
    candles = [{"time":c["time"],"open":float(c["mid"]["o"]),
                "high":float(c["mid"]["h"]),"low":float(c["mid"]["l"]),
                "close":float(c["mid"]["c"])} for c in d["candles"]]
    return pd.DataFrame(candles)

def check_signals(df):
    df["ema4"] = EMAIndicator(df["close"],4).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"],50).ema_indicator()
    buy = (df["ema4"].iloc[-2] < df["ema50"].iloc[-2]) and (df["ema4"].iloc[-1] > df["ema50"].iloc[-1])
    sell = (df["ema4"].iloc[-2] > df["ema50"].iloc[-2]) and (df["ema4"].iloc[-1] < df["ema50"].iloc[-1])
    return buy, sell

def paper_trade(side):
    print(f"[{time.strftime('%H:%M:%S')}] {side} signal | SL={STOP_PIPS}p TP={TP_PIPS}p")

while True:
    df = get_candles()
    buy, sell = check_signals(df)
    if buy: paper_trade("BUY")
    elif sell: paper_trade("SELL")
    else: print(f"[{time.strftime('%H:%M:%S')}] Waiting...")
    time.sleep(60 * 15)
