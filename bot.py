import os
import time
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv
from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles


# =========================================================
# ENVIRONMENT
# =========================================================

load_dotenv()

OANDA_API_KEY = os.getenv("OANDA_API_KEY")

if not OANDA_API_KEY:
    raise RuntimeError(
        "OANDA_API_KEY is missing from your .env file."
    )


# =========================================================
# OANDA CONNECTION
# =========================================================

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

# Safety switch.
# The bot cannot place orders while this remains False.
ORDERS_ENABLED = False


# =========================================================
# ACTIVE SETUP MEMORY
# =========================================================

active_setup = {
    "active": False,
    "direction": None,
    "swept_level": None,
    "sweep_price": None,
    "sweep_time": None,
    "structure_break_found": False,
    "break_level": None,
    "break_price": None,
    "break_time": None,
}

# =========================================================
# DATA FUNCTIONS
# =========================================================

def fetch_candles(
    timeframe: str,
    count: int = CANDLE_COUNT,
) -> pd.DataFrame:
    """
    Download completed midpoint candles from OANDA.
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

    for candle in response.get("candles", []):
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

    dataframe = dataframe.sort_values(
        "time"
    ).reset_index(drop=True)

    return dataframe


def validate_market_data(
    dataframe: pd.DataFrame,
    timeframe: str,
) -> None:
    """
    Confirm that candle data contains everything the bot needs.
    """

    required_columns = {
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    missing_columns = (
        required_columns
        - set(dataframe.columns)
    )

    if missing_columns:
        raise RuntimeError(
            f"{timeframe} data is missing columns: "
            f"{sorted(missing_columns)}"
        )

    minimum_candles = (
        SWING_STRENGTH * 2
    ) + 10

    if len(dataframe) < minimum_candles:
        raise RuntimeError(
            f"Not enough {timeframe} candles. "
            f"Received {len(dataframe)}, "
            f"need at least {minimum_candles}."
        )

    price_columns = [
        "open",
        "high",
        "low",
        "close",
    ]

    if dataframe[price_columns].isnull().any().any():
        raise RuntimeError(
            f"{timeframe} candle data contains missing prices."
        )

# =========================================================
# SWING DETECTION
# =========================================================

def identify_swings(
    dataframe: pd.DataFrame,
    strength: int = SWING_STRENGTH,
) -> pd.DataFrame:
    """
    Mark confirmed swing highs and swing lows.
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
# H4 TREND
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

    if (
        latest_high > previous_high
        and latest_low > previous_low
    ):
        return "BULLISH"

    if (
        latest_high < previous_high
        and latest_low < previous_low
    ):
        return "BEARISH"

    return "RANGING"

# =========================================================
# H1 LIQUIDITY SWEEP
# =========================================================

def detect_liquidity_sweep(
    dataframe: pd.DataFrame,
) -> dict:
    """
    Detect an H1 liquidity sweep.

    Bullish sweep:
    Price trades below the latest swing low
    and closes back above it.

    Bearish sweep:
    Price trades above the latest swing high
    and closes back below it.
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

    Bullish sweep requires bullish H4 structure.
    Bearish sweep requires bearish H4 structure.
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

    if not (
        valid_bullish_setup
        or valid_bearish_setup
    ):
        return

    active_setup["active"] = True
    active_setup["direction"] = sweep["type"]
    active_setup["swept_level"] = sweep["level"]
    active_setup["sweep_price"] = sweep["sweep_price"]
    active_setup["sweep_time"] = sweep["candle_time"]

    active_setup["structure_break_found"] = False
    active_setup["break_level"] = None
    active_setup["break_price"] = None
    active_setup["break_time"] = None

    print("\n[H1 SWEEP SAVED]")
    print(
        f"Direction:   "
        f"{active_setup['direction']}"
    )
    print(
        f"Swept level: "
        f"{active_setup['swept_level']:.3f}"
    )
    print(
        f"Sweep price: "
        f"{active_setup['sweep_price']:.3f}"
    )
    print(
        f"Sweep time:  "
        f"{active_setup['sweep_time']}"
    )


def clear_active_setup(
    reason: str,
) -> None:
    """
    Erase the current setup.
    """

    print(
        f"\n[SETUP CLEARED] {reason}"
    )

    active_setup["active"] = False
    active_setup["direction"] = None
    active_setup["swept_level"] = None
    active_setup["sweep_price"] = None
    active_setup["sweep_time"] = None
    active_setup["structure_break_found"] = False
    active_setup["break_level"] = None
    active_setup["break_price"] = None
    active_setup["break_time"] = None

    # =========================================================
# M5 MARKET STRUCTURE BREAK
# =========================================================

def detect_m5_structure_break(
    dataframe: pd.DataFrame,
) -> dict:
    """
    Detect an M5 close through a confirmed swing.

    Bullish setup:
    M5 closes above the latest confirmed swing high.

    Bearish setup:
    M5 closes below the latest confirmed swing low.
    """

    no_break = {
        "found": False,
        "direction": None,
        "break_level": None,
        "break_price": None,
        "break_time": None,
    }

    if not active_setup["active"]:
        return no_break

    if active_setup["structure_break_found"]:
        return {
            "found": True,
            "direction": active_setup["direction"],
            "break_level": active_setup["break_level"],
            "break_price": active_setup["break_price"],
            "break_time": active_setup["break_time"],
        }

    df = identify_swings(dataframe)

    latest_candle = df.iloc[-1]
    previous_data = df.iloc[:-1]

    latest_close = float(
        latest_candle["close"]
    )

    latest_time = latest_candle["time"]
    sweep_time = active_setup["sweep_time"]

    if latest_time <= sweep_time:
        return no_break

    if active_setup["direction"] == "BULLISH":
        previous_swing_highs = previous_data[
            previous_data["swing_high"]
        ]

        if previous_swing_highs.empty:
            return no_break

        break_level = float(
            previous_swing_highs.iloc[-1]["high"]
        )

        if latest_close > break_level:
            return {
                "found": True,
                "direction": "BULLISH",
                "break_level": break_level,
                "break_price": latest_close,
                "break_time": latest_time,
            }

    if active_setup["direction"] == "BEARISH":
        previous_swing_lows = previous_data[
            previous_data["swing_low"]
        ]

        if previous_swing_lows.empty:
            return no_break

        break_level = float(
            previous_swing_lows.iloc[-1]["low"]
        )

        if latest_close < break_level:
            return {
                "found": True,
                "direction": "BEARISH",
                "break_level": break_level,
                "break_price": latest_close,
                "break_time": latest_time,
            }

    return no_break


def save_structure_break_to_memory(
    structure_break: dict,
) -> None:
    """
    Save the M5 structure break only once.
    """

    if not structure_break["found"]:
        return

    if active_setup["structure_break_found"]:
        return

    active_setup["structure_break_found"] = True
    active_setup["break_level"] = (
        structure_break["break_level"]
    )
    active_setup["break_price"] = (
        structure_break["break_price"]
    )
    active_setup["break_time"] = (
        structure_break["break_time"]
    )

    print("\n[M5 STRUCTURE BREAK FOUND]")
    print(
        f"Direction:   "
        f"{structure_break['direction']}"
    )
    print(
        f"Break level: "
        f"{structure_break['break_level']:.3f}"
    )
    print(
        f"Close price: "
        f"{structure_break['break_price']:.3f}"
    )
    print(
        f"Break time:  "
        f"{structure_break['break_time']}"
    )


# =========================================================
# BOT STATE
# =========================================================

def determine_bot_state(
    trend: str,
    sweep: dict,
) -> str:
    """
    Return the bot's current workflow state.
    """

    if active_setup["active"]:
        if active_setup["structure_break_found"]:
            return "WAITING_FOR_M5_RETEST"

        if active_setup["direction"] == "BULLISH":
            return "WAITING_FOR_M5_BULLISH_BREAK"

        if active_setup["direction"] == "BEARISH":
            return "WAITING_FOR_M5_BEARISH_BREAK"

    if sweep["type"] == "NONE":
        return "WAITING_FOR_H1_SWEEP"

    if trend == "RANGING":
        return "SWEEP_FOUND_BUT_H4_IS_RANGING"

    if trend == "UNCLEAR":
        return "SWEEP_FOUND_BUT_H4_IS_UNCLEAR"

    return "SWEEP_OPPOSES_H4_TREND"

# =========================================================
# DISPLAY HELPERS
# =========================================================

def format_price(
    price: float | None,
) -> str:
    """
    Format prices cleanly for terminal output.
    """

    if price is None:
        return "NONE"

    return f"{price:.3f}"


# =========================================================
# STATUS DISPLAY
# =========================================================

def display_status() -> None:
    """
    Fetch all required data, run the strategy,
    and print the current bot status.
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

    validate_market_data(
        h4,
        TREND_TIMEFRAME,
    )

    validate_market_data(
        h1,
        LIQUIDITY_TIMEFRAME,
    )

    validate_market_data(
        m5,
        ENTRY_TIMEFRAME,
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

    structure_break = (
        detect_m5_structure_break(m5)
    )

    save_structure_break_to_memory(
        structure_break
    )

    bot_state = determine_bot_state(
        trend,
        sweep,
    )

    latest_price = float(
        m5.iloc[-1]["close"]
    )

    latest_m5_time = (
        m5.iloc[-1]["time"]
    )

    current_time = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    print(
        "\n"
        + "=" * 66
    )

    print(
        "CIPHER 313 GOLD BOT"
    )

    print(
        "H4 Trend | H1 Liquidity | M5 Structure"
    )

    print(
        "=" * 66
    )

    print(
        f"System time:             "
        f"{current_time}"
    )

    print(
        f"Last completed M5:       "
        f"{latest_m5_time}"
    )

    print(
        f"Instrument:              "
        f"{INSTRUMENT}"
    )

    print(
        f"Current price:           "
        f"{latest_price:.3f}"
    )

    print(
        f"H4 candles loaded:       "
        f"{len(h4)}"
    )

    print(
        f"H1 candles loaded:       "
        f"{len(h1)}"
    )

    print(
        f"M5 candles loaded:       "
        f"{len(m5)}"
    )

    print(
        f"H4 trend:                "
        f"{trend}"
    )

    print(
        "-" * 66
    )

    print(
        f"Latest H1 swing high:    "
        f"{format_price(h1_swing_high)}"
    )

    print(
        f"Latest H1 swing low:     "
        f"{format_price(h1_swing_low)}"
    )

    print(
        f"Latest M5 swing high:    "
        f"{format_price(m5_swing_high)}"
    )

    print(
        f"Latest M5 swing low:     "
        f"{format_price(m5_swing_low)}"
    )

    print(
        "-" * 66
    )

    print(
        f"Current H1 sweep:        "
        f"{sweep['type']}"
    )

    if sweep["type"] != "NONE":
        print(
            f"Current swept level:     "
            f"{format_price(sweep['level'])}"
        )

        print(
            f"Current sweep price:     "
            f"{format_price(sweep['sweep_price'])}"
        )

        print(
            f"Current sweep time:      "
            f"{sweep['candle_time']}"
        )

    print(
        "-" * 66
    )

    if active_setup["active"]:
        print(
            "Active setup:            YES"
        )

        print(
            f"Setup direction:         "
            f"{active_setup['direction']}"
        )

        print(
            f"Stored swept level:      "
            f"{format_price(active_setup['swept_level'])}"
        )

        print(
            f"Stored sweep price:      "
            f"{format_price(active_setup['sweep_price'])}"
        )

        print(
            f"Stored sweep time:       "
            f"{active_setup['sweep_time']}"
        )

        print(
            f"M5 structure break:      "
            f"{'YES' if active_setup['structure_break_found'] else 'NO'}"
        )

        if active_setup["structure_break_found"]:
            print(
                f"Stored break level:      "
                f"{format_price(active_setup['break_level'])}"
            )

            print(
                f"Stored break price:      "
                f"{format_price(active_setup['break_price'])}"
            )

            print(
                f"Stored break time:       "
                f"{active_setup['break_time']}"
            )

    else:
        print(
            "Active setup:            NO"
        )

        print(
            "M5 structure break:      NO"
        )

    print(
        "-" * 66
    )

    print(
        f"Bot state:               "
        f"{bot_state}"
    )

    print(
        f"Orders enabled:          "
        f"{'YES' if ORDERS_ENABLED else 'NO'}"
    )

    print(
        "=" * 66
    )


# =========================================================
# STARTUP TEST
# =========================================================

def run_startup_test() -> None:
    """
    Test the OANDA connection and all required timeframes.
    """

    print(
        "[STARTUP TEST] Checking OANDA connection..."
    )

    timeframes = [
        TREND_TIMEFRAME,
        LIQUIDITY_TIMEFRAME,
        ENTRY_TIMEFRAME,
    ]

    for timeframe in timeframes:
        dataframe = fetch_candles(
            timeframe,
            count=50,
        )

        validate_market_data(
            dataframe,
            timeframe,
        )

        print(
            f"[PASS] {timeframe}: "
            f"{len(dataframe)} completed candles loaded."
        )

    print(
        "[STARTUP TEST PASSED]"
    )


# =========================================================
# MAIN LOOP
# =========================================================

def main() -> None:
    """
    Run the bot continuously.

    The bot checks for a newly completed M5 candle.
    Full analysis runs only when a new M5 candle appears.
    """

    run_startup_test()

    print(
        "\n[BOT STARTED] "
        "XAU/USD | H4/H1/M5 | Detection mode"
    )

    print(
        "[SAFETY] Practice account only. "
        "Order execution is disabled."
    )

    last_processed_candle = None

    while True:
        try:
            recent_m5 = fetch_candles(
                ENTRY_TIMEFRAME,
                count=20,
            )

            newest_candle = (
                recent_m5.iloc[-1]["time"]
            )

            if newest_candle != last_processed_candle:
                display_status()

                last_processed_candle = (
                    newest_candle
                )

            time.sleep(
                CHECK_INTERVAL_SECONDS
            )

        except KeyboardInterrupt:
            print(
                "\n[BOT STOPPED] Manual shutdown."
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