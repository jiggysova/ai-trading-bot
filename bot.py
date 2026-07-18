import os
import time
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles


# =========================================================
# ENVIRONMENT AND OANDA CONNECTION
# =========================================================

load_dotenv()

OANDA_API_KEY = os.getenv("OANDA_API_KEY")

if not OANDA_API_KEY:
    raise RuntimeError(
        "OANDA_API_KEY is missing from your environment."
    )

client = API(
    access_token=OANDA_API_KEY,
    environment="practice",
)


# =========================================================
# BOT SETTINGS
# =========================================================

INSTRUMENT = "XAU_USD"

TREND_TIMEFRAME = "H4"
LIQUIDITY_TIMEFRAME = "H1"
ENTRY_TIMEFRAME = "M5"

CANDLE_COUNT = 300
SWING_STRENGTH = 3
CHECK_INTERVAL_SECONDS = 60


# =========================================================
# ACTIVE SETUP MEMORY
# =========================================================

active_setup = {
    "active": False,
    "direction": None,
    "level": None,
    "sweep_price": None,
    "sweep_time": None,
}


# =========================================================
# DATA FUNCTIONS
# =========================================================

def fetch_candles(
    timeframe: str,
    count: int = CANDLE_COUNT,
) -> pd.DataFrame:
    """
    Download completed candles from OANDA.
    """

    params = {
        "granularity": timeframe,
        "count": count,
        "price": "M",
    }

    request = InstrumentsCandles(
        instrument=INSTRUMENT,
        params=params,
    )

    response = client.request(request)

    candles = []

    for candle in response["candles"]:
        if not candle.get("complete", False):
            continue

        candles.append(
            {
                "time": pd.to_datetime(
                    candle["time"],
                    utc=True,
                ),
                "open": float(candle["mid"]["o"]),
                "high": float(candle["mid"]["h"]),
                "low": float(candle["mid"]["l"]),
                "close": float(candle["mid"]["c"]),
                "volume": int(candle["volume"]),
            }
        )

    dataframe = pd.DataFrame(candles)

    if dataframe.empty:
        raise RuntimeError(
            f"No completed {timeframe} candles received "
            f"for {INSTRUMENT}."
        )

    return dataframe


# =========================================================
# SWING DETECTION
# =========================================================

def identify_swings(
    dataframe: pd.DataFrame,
    strength: int = SWING_STRENGTH,
) -> pd.DataFrame:
    """
    Mark swing highs and swing lows.

    A swing high must be higher than the candles around it.
    A swing low must be lower than the candles around it.
    """

    df = dataframe.copy()

    df["swing_high"] = False
    df["swing_low"] = False

    for index in range(
        strength,
        len(df) - strength,
    ):
        current_high = float(
            df.at[index, "high"]
        )

        current_low = float(
            df.at[index, "low"]
        )

        left_highs = df.loc[
            index - strength:index - 1,
            "high",
        ]

        right_highs = df.loc[
            index + 1:index + strength,
            "high",
        ]

        left_lows = df.loc[
            index - strength:index - 1,
            "low",
        ]

        right_lows = df.loc[
            index + 1:index + strength,
            "low",
        ]

        surrounding_highs = pd.concat(
            [
                left_highs,
                right_highs,
            ]
        )

        surrounding_lows = pd.concat(
            [
                left_lows,
                right_lows,
            ]
        )

        if current_high > surrounding_highs.max():
            df.at[index, "swing_high"] = True

        if current_low < surrounding_lows.min():
            df.at[index, "swing_low"] = True

    return df


def latest_swing_levels(
    dataframe: pd.DataFrame,
) -> tuple[float | None, float | None]:
    """
    Return the latest confirmed swing high and swing low.
    """

    df = identify_swings(dataframe)

    swing_highs = df[
        df["swing_high"]
    ]

    swing_lows = df[
        df["swing_low"]
    ]

    latest_high = None
    latest_low = None

    if not swing_highs.empty:
        latest_high = float(
            swing_highs.iloc[-1]["high"]
        )

    if not swing_lows.empty:
        latest_low = float(
            swing_lows.iloc[-1]["low"]
        )

    return latest_high, latest_low


# =========================================================
# H4 TREND DETECTION
# =========================================================

def determine_trend(
    dataframe: pd.DataFrame,
) -> str:
    """
    Determine H4 market structure.

    Bullish:
    Higher high and higher low.

    Bearish:
    Lower high and lower low.

    Otherwise:
    Ranging.
    """

    df = identify_swings(dataframe)

    swing_highs = df[
        df["swing_high"]
    ].tail(2)

    swing_lows = df[
        df["swing_low"]
    ].tail(2)

    if (
        len(swing_highs) < 2
        or len(swing_lows) < 2
    ):
        return "UNCLEAR"

    previous_high = float(
        swing_highs.iloc[-2]["high"]
    )

    latest_high = float(
        swing_highs.iloc[-1]["high"]
    )

    previous_low = float(
        swing_lows.iloc[-2]["low"]
    )

    latest_low = float(
        swing_lows.iloc[-1]["low"]
    )

    bullish_structure = (
        latest_high > previous_high
        and latest_low > previous_low
    )

    bearish_structure = (
        latest_high < previous_high
        and latest_low < previous_low
    )

    if bullish_structure:
        return "BULLISH"

    if bearish_structure:
        return "BEARISH"

    return "RANGING"


# =========================================================
# LIQUIDITY SWEEP DETECTION
# =========================================================

def detect_liquidity_sweep(
    dataframe: pd.DataFrame,
) -> dict:
    """
    Detect an H1 liquidity sweep.

    Bullish sweep:
    Price trades below a previous swing low,
    then closes back above that level.

    Bearish sweep:
    Price trades above a previous swing high,
    then closes back below that level.
    """

    df = identify_swings(dataframe)

    latest_candle = df.iloc[-1]
    previous_data = df.iloc[:-1]

    previous_swing_highs = previous_data[
        previous_data["swing_high"]
    ]

    previous_swing_lows = previous_data[
        previous_data["swing_low"]
    ]

    latest_high = float(
        latest_candle["high"]
    )

    latest_low = float(
        latest_candle["low"]
    )

    latest_close = float(
        latest_candle["close"]
    )

    latest_time = latest_candle["time"]

    if not previous_swing_lows.empty:
        swing_low_level = float(
            previous_swing_lows.iloc[-1]["low"]
        )

        bullish_sweep = (
            latest_low < swing_low_level
            and latest_close > swing_low_level
        )

        if bullish_sweep:
            return {
                "type": "BULLISH",
                "level": swing_low_level,
                "sweep_price": latest_low,
                "candle_time": latest_time,
            }

    if not previous_swing_highs.empty:
        swing_high_level = float(
            previous_swing_highs.iloc[-1]["high"]
        )

        bearish_sweep = (
            latest_high > swing_high_level
            and latest_close < swing_high_level
        )

        if bearish_sweep:
            return {
                "type": "BEARISH",
                "level": swing_high_level,
                "sweep_price": latest_high,
                "candle_time": latest_time,
            }

    return {
        "type": "NONE",
        "level": None,
        "sweep_price": None,
        "candle_time": latest_time,
    }


# =========================================================
# SETUP MEMORY
# =========================================================

def save_sweep_to_memory(
    trend: str,
    sweep: dict,
) -> None:
    """
    Save a valid H1 sweep.

    Bullish sweeps are accepted only when H4 is bullish.
    Bearish sweeps are accepted only when H4 is bearish.
    """

    if active_setup["active"]:
        return

    valid_bullish_setup = (
        trend == "BULLISH"
        and sweep["type"] == "BULLISH"
    )

    valid_bearish_setup = (
        trend == "BEARISH"
        and sweep["type"] == "BEARISH"
    )

    if (
        not valid_bullish_setup
        and not valid_bearish_setup
    ):
        return

    active_setup["active"] = True
    active_setup["direction"] = sweep["type"]
    active_setup["level"] = sweep["level"]
    active_setup["sweep_price"] = sweep["sweep_price"]
    active_setup["sweep_time"] = sweep["candle_time"]

    print("\n[SETUP SAVED]")
    print(
        f"Direction:    "
        f"{active_setup['direction']}"
    )
    print(
        f"Swept level:  "
        f"{active_setup['level']:.3f}"
    )
    print(
        f"Sweep price:  "
        f"{active_setup['sweep_price']:.3f}"
    )
    print(
        f"Sweep time:   "
        f"{active_setup['sweep_time']}"
    )


def clear_active_setup(
    reason: str,
) -> None:
    """
    Clear the stored setup.
    """

    print(
        f"\n[SETUP CLEARED] {reason}"
    )

    active_setup["active"] = False
    active_setup["direction"] = None
    active_setup["level"] = None
    active_setup["sweep_price"] = None
    active_setup["sweep_time"] = None


# =========================================================
# BOT STATE
# =========================================================

def determine_bot_state(
    trend: str,
    sweep: dict,
) -> str:
    """
    Determine what the bot should wait for next.
    """

    if active_setup["active"]:
        if (
            active_setup["direction"]
            == "BULLISH"
        ):
            return "WAITING_FOR_M5_BULLISH_BREAK"

        if (
            active_setup["direction"]
            == "BEARISH"
        ):
            return "WAITING_FOR_M5_BEARISH_BREAK"

    if sweep["type"] == "NONE":
        return "WAITING_FOR_H1_SWEEP"

    if trend == "RANGING":
        return "SWEEP_FOUND_BUT_H4_IS_RANGING"

    if trend == "UNCLEAR":
        return "SWEEP_FOUND_BUT_H4_IS_UNCLEAR"

    return "SWEEP_OPPOSES_H4_TREND"


# =========================================================
# STATUS DISPLAY
# =========================================================

def display_status() -> None:
    """
    Fetch all timeframes and display the bot status.
    """

    h4 = fetch_candles(
        TREND_TIMEFRAME
    )

    h1 = fetch_candles(
        LIQUIDITY_TIMEFRAME
    )

    m5 = fetch_candles(
        ENTRY_TIMEFRAME
    )

    trend = determine_trend(h4)

    h1_swing_high, h1_swing_low = (
        latest_swing_levels(h1)
    )

    m5_swing_high, m5_swing_low = (
        latest_swing_levels(m5)
    )

    sweep = detect_liquidity_sweep(h1)

    save_sweep_to_memory(
        trend,
        sweep,
    )

    bot_state = determine_bot_state(
        trend,
        sweep,
    )

    latest_price = float(
        m5.iloc[-1]["close"]
    )

    latest_candle_time = (
        m5.iloc[-1]["time"]
    )

    current_time = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    print(
        "\n"
        + "=" * 62
    )

    print(
        "CIPHER 313 GOLD BOT"
    )

    print(
        "Structure and liquidity detection mode"
    )

    print(
        "=" * 62
    )

    print(
        f"System time:          "
        f"{current_time}"
    )

    print(
        f"Last completed M5:    "
        f"{latest_candle_time}"
    )

    print(
        f"Instrument:           "
        f"{INSTRUMENT}"
    )

    print(
        f"Current price:        "
        f"{latest_price:.3f}"
    )

    print(
        f"4H trend:             "
        f"{trend}"
    )

    print(
        f"Latest H1 swing high: "
        f"{h1_swing_high}"
    )

    print(
        f"Latest H1 swing low:  "
        f"{h1_swing_low}"
    )

    print(
        f"Latest M5 swing high: "
        f"{m5_swing_high}"
    )

    print(
        f"Latest M5 swing low:  "
        f"{m5_swing_low}"
    )

    print(
        "-" * 62
    )

    print(
        f"H1 liquidity sweep:   "
        f"{sweep['type']}"
    )

    if sweep["type"] != "NONE":
        print(
            f"Swept level:          "
            f"{sweep['level']:.3f}"
        )

        print(
            f"Extreme price:        "
            f"{sweep['sweep_price']:.3f}"
        )

        print(
            f"Sweep candle:         "
            f"{sweep['candle_time']}"
        )

    print(
        "-" * 62
    )

    if active_setup["active"]:
        print(
            "Active setup:         YES"
        )

        print(
            f"Setup direction:      "
            f"{active_setup['direction']}"
        )

        print(
            f"Stored swept level:   "
            f"{active_setup['level']:.3f}"
        )

        print(
            f"Stored sweep price:   "
            f"{active_setup['sweep_price']:.3f}"
        )

        print(
            f"Stored sweep time:    "
            f"{active_setup['sweep_time']}"
        )

    else:
        print(
            "Active setup:         NO"
        )

    print(
        f"Bot state:            "
        f"{bot_state}"
    )

    print(
        "Orders enabled:       NO"
    )

    print(
        "=" * 62
    )


# =========================================================
# MAIN LOOP
# =========================================================

def main() -> None:
    """
    Run the bot continuously.

    The bot checks for a newly completed M5 candle.
    It displays a fresh status only when a new M5 candle appears.
    """

    print(
        "[BOT STARTED] "
        "XAU/USD | H4/H1/M5 | "
        "Liquidity detection mode"
    )

    last_processed_candle = None

    while True:
        try:
            recent_m5 = fetch_candles(
                ENTRY_TIMEFRAME,
                count=10,
            )

            newest_candle = (
                recent_m5.iloc[-1]["time"]
            )

            if (
                newest_candle
                != last_processed_candle
            ):
                display_status()

                last_processed_candle = (
                    newest_candle
                )

            time.sleep(
                CHECK_INTERVAL_SECONDS
            )

        except KeyboardInterrupt:
            print(
                "\n[BOT STOPPED] "
                "Manual shutdown."
            )
            break

        except Exception as error:
            print(
                f"[ERROR] "
                f"{type(error).__name__}: "
                f"{error}"
            )

            time.sleep(
                CHECK_INTERVAL_SECONDS
            )


if __name__ == "__main__":
    main()