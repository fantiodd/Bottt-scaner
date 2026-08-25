import os
import json
import time
import math
import requests
from datetime import datetime, timezone


# ============================================================
# SOLANA MOMENTUM SCANNER v6.0
# ============================================================
#
# Цель:
#   находить ранние подтверждённые движения,
#   а не просто токены с высоким Q / 1h ростом.
#
# Архитектура:
#
#   SOURCE
#      ↓
#   PAIR VALIDATION
#      ↓
#   MARKET FILTER
#      ↓
#   SECURITY HEURISTICS
#      ↓
#   MOMENTUM
#      ↓
#   QUALITY
#      ↓
#   RISK
#      ↓
#   CONFIRMATION
#      ↓
#   SIGNAL ENGINE
#      ↓
#   TELEGRAM
#
# Важно:
#   DexScreener не даёт полноценной on-chain гарантии
#   отсутствия rug/scam. Security здесь является
#   дополнительным risk-фактором, а не гарантией безопасности.
#
# ============================================================


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 15
HISTORY_LENGTH = 60

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

# Минимум для нормального анализа.
# Очень маленькая ликвидность остаётся допустимой,
# но получает сильный risk penalty.
MIN_ANALYSIS_LIQUIDITY = 7_500

# ------------------------------------------------------------
# SIGNAL THRESHOLDS
# ------------------------------------------------------------

EARLY_MOMENTUM = 62
BREAKOUT_MOMENTUM = 68
STRONG_MOMENTUM = 75
VERY_STRONG_MOMENTUM = 84

MIN_CONFIDENCE = 58

EARLY_QUALITY = 30
BREAKOUT_QUALITY = 25
STRONG_QUALITY = 40

MAX_SIGNAL_RISK = 58

# ------------------------------------------------------------
# BREAKOUT
# ------------------------------------------------------------

BREAKOUT_MIN_5M = 4
BREAKOUT_MAX_5M = 35

BREAKOUT_MIN_1H = 3

# Не считать токен breakout, если он уже слишком сильно
# вырос за час.
BREAKOUT_MAX_1H = 120

# ------------------------------------------------------------
# OVEREXTENSION
# ------------------------------------------------------------

EXTREME_1H = 200
VERY_HIGH_1H = 120
HIGH_1H = 80

EXTREME_5M = 40
HIGH_5M = 25

# ------------------------------------------------------------
# DUMP
# ------------------------------------------------------------

DUMP_5M = -12
SEVERE_DUMP_5M = -20

DUMP_1H = -20

# ------------------------------------------------------------
# ALERTS
# ------------------------------------------------------------

ALERT_COOLDOWN = 30 * 60

RE_ALERT_MOMENTUM = 10
RE_ALERT_CONFIDENCE = 10

# ------------------------------------------------------------
# API
# ------------------------------------------------------------

REQUEST_TIMEOUT = 15
PAIR_TIMEOUT = 12

MAX_RETRIES = 3

RETRY_BACKOFF = 1.5

ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
]

PAIR_ENDPOINT = (
    "https://api.dexscreener.com/"
    "token-pairs/v1/solana/"
)

BLOCKED = {
    "SOL",
    "WSOL",
    "USDC",
    "USDT",
    "USD1",
    "USDE",
    "DAI",
}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "SolanaMomentumScanner/6.0"
    )
})


# ============================================================
# UTILS
# ============================================================

def num(value, default=0.0):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return float(value)

    except Exception:
        return default


def integer(value, default=0):
    try:
        return int(float(value or 0))
    except Exception:
        return default


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


def safe_ratio(a, b, default=0.0):
    if b <= 0:
        return default

    return a / b


def money(value):
    value = num(value)

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def pct(value):
    return f"{num(value):+.1f}%"


def now_ts():
    return time.time()


def now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# JSON STATE
# ============================================================

def load_json(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(
            f"JSON LOAD ERROR {filename}:",
            e
        )

        return default


def atomic_save_json(filename, data):
    """
    Атомарная запись.

    Если GitHub Actions / процесс прервётся
    во время записи, старый state не будет
    уничтожен наполовину.
    """

    temp = filename + ".tmp"

    try:
        with open(
            temp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

            f.flush()
            os.fsync(f.fileno())

        os.replace(
            temp,
            filename
        )

    except Exception as e:

        print(
            "SAVE ERROR:",
            filename,
            e
        )

        try:
            if os.path.exists(temp):
                os.remove(temp)
        except Exception:
            pass


# ============================================================
# HTTP
# ============================================================

def request_json(
    url,
    timeout=REQUEST_TIMEOUT,
    retries=MAX_RETRIES
):

    last_error = None

    for attempt in range(retries):

        try:

            response = SESSION.get(
                url,
                timeout=timeout
            )

            if response.status_code == 200:

                try:
                    return response.json()

                except Exception as e:

                    last_error = e

            elif response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                last_error = (
                    f"HTTP {response.status_code}"
                )

                if attempt < retries - 1:

                    delay = (
                        RETRY_BACKOFF
                        ** (attempt + 1)
                    )

                    time.sleep(delay)

                    continue

            else:

                print(
                    f"{url} -> "
                    f"HTTP {response.status_code}"
                )

                return None

        except Exception as e:

            last_error = e

        if attempt < retries - 1:

            time.sleep(
                RETRY_BACKOFF
                ** (attempt + 1)
            )

    print(
        "REQUEST ERROR:",
        url,
        last_error
    )

    return None


# ============================================================
# TOKEN SOURCES
# ============================================================

def get_addresses():

    addresses = set()

    for endpoint in ENDPOINTS:

        data = request_json(endpoint)

        if data is None:

            print(
                endpoint,
                "-> ERROR"
            )

            continue

        print(
            endpoint,
            "-> OK"
        )

        if not isinstance(data, list):
            continue

        for item in data:

            if not isinstance(item, dict):
                continue

            if item.get("chainId") != "solana":
                continue

            address = item.get(
                "tokenAddress"
            )

            if address:
                addresses.add(address)

    return list(addresses)


# ============================================================
# PAIR
# ============================================================

def get_pair(address):

    url = PAIR_ENDPOINT + address

    data = request_json(
        url,
        timeout=PAIR_TIMEOUT
    )

    if not isinstance(data, list):
        return None

    valid = []

    for pair in data:

        if not isinstance(pair, dict):
            continue

        if pair.get("chainId") != "solana":
            continue

        liquidity = num(
            pair.get(
                "liquidity",
                {}
            ).get("usd")
        )

        if liquidity <= 0:
            continue

        valid.append(pair)

    if not valid:
        return None

    valid.sort(
        key=lambda x: num(
            x.get(
                "liquidity",
                {}
            ).get("usd")
        ),
        reverse=True
    )

    return valid[0]


# ============================================================
# BASIC MARKET DATA
# ============================================================

def extract_market(pair):

    change = pair.get(
        "priceChange",
        {}
    )

    volume = pair.get(
        "volume",
        {}
    )

    liquidity_data = pair.get(
        "liquidity",
        {}
    )

    txns = pair.get(
        "txns",
        {}
    )

    tx5 = txns.get(
        "m5",
        {}
    )

    p5 = num(
        change.get("m5")
    )

    p1h = num(
        change.get("h1")
    )

    p6h = num(
        change.get("h6")
    )

    p24 = num(
        change.get("h24")
    )

    v5 = num(
        volume.get("m5")
    )

    v1h = num(
        volume.get("h1")
    )

    v6h = num(
        volume.get("h6")
    )

    v24 = num(
        volume.get("h24")
    )

    liquidity = num(
        liquidity_data.get("usd")
    )

    buys5 = integer(
        tx5.get("buys")
    )

    sells5 = integer(
        tx5.get("sells")
    )

    total5 = buys5 + sells5

    buy_ratio = safe_ratio(
        buys5,
        total5
    )

    price_usd = num(
        pair.get("priceUsd")
    )

    fdv = num(
        pair.get("fdv")
    )

    market_cap = num(
        pair.get("marketCap")
    )

    return {
        "p5": p5,
        "p1h": p1h,
        "p6h": p6h,
        "p24": p24,

        "v5": v5,
        "v1h": v1h,
        "v6h": v6h,
        "v24": v24,

        "liquidity": liquidity,

        "buys5": buys5,
        "sells5": sells5,
        "tx5": total5,

        "buy_ratio": buy_ratio,

        "price_usd": price_usd,

        "fdv": fdv,
        "market_cap": market_cap,
    }


# ============================================================
# HISTORY HELPERS
# ============================================================

def get_history(old):

    history = old.get(
        "history",
        []
    )

    if not isinstance(history, list):
        return []

    return history[-HISTORY_LENGTH:]


def previous_snapshot(old):

    history = get_history(old)

    if not history:
        return None

    return history[-1]


def history_count(old):

    return len(
        get_history(old)
    )


# ============================================================
# ACCELERATION
# ============================================================

def calculate_acceleration(
    current,
    previous,
    minimum=0
):

    if previous is None:
        return 0.0

    old = num(previous)

    if old <= minimum:
        return 0.0

    return current / old


def price_acceleration(
    market,
    previous
):

    if not previous:
        return 0.0

    old_p5 = num(
        previous.get("p5")
    )

    current_p5 = market["p5"]

    # Разница между текущим rolling 5m momentum
    # и предыдущим наблюдением.
    return current_p5 - old_p5


def volume_acceleration(
    market,
    previous
):

    if not previous:
        return 0.0

    old_v5 = num(
        previous.get("v5")
    )

    current_v5 = market["v5"]

    if old_v5 <= 0:
        return 0.0

    return current_v5 / old_v5


def transaction_acceleration(
    market,
    previous
):

    if not previous:
        return 0.0

    old_tx = num(
        previous.get("tx5")
    )

    current_tx = market["tx5"]

    if old_tx <= 0:
        return 0.0

    return current_tx / old_tx


# ============================================================
# QUALITY
# ============================================================

def calculate_quality(market):

    liquidity = market["liquidity"]
    v24 = market["v24"]
    tx5 = market["tx5"]
    buy_ratio = market["buy_ratio"]
    p1h = market["p1h"]

    score = 0
    reasons = []

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity >= 500_000:

        score += 30
        reasons.append(
            "очень высокая ликвидность"
        )

    elif liquidity >= 200_000:

        score += 27
        reasons.append(
            "высокая ликвидность"
        )

    elif liquidity >= 100_000:

        score += 24
        reasons.append(
            "хорошая ликвидность"
        )

    elif liquidity >= 50_000:

        score += 20
        reasons.append(
            "хорошая ликвидность"
        )

    elif liquidity >= 30_000:

        score += 16
        reasons.append(
            "нормальная ликвидность"
        )

    elif liquidity >= 20_000:

        score += 11

    elif liquidity >= 10_000:

        score += 6

    else:

        score -= 10

        reasons.append(
            "низкая ликвидность"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if v24 >= 2_000_000:

        score += 25

    elif v24 >= 1_000_000:

        score += 22

    elif v24 >= 500_000:

        score += 18

    elif v24 >= 100_000:

        score += 14

    elif v24 >= 50_000:

        score += 10

    elif v24 >= 20_000:

        score += 5

    # --------------------------------------------------------
    # TRANSACTIONS
    #
    # Важно:
    # tx5 маленький сам по себе не означает плохой токен,
    # поэтому здесь не делаем огромный штраф.
    # --------------------------------------------------------

    if tx5 >= 100:

        score += 20

    elif tx5 >= 60:

        score += 17

    elif tx5 >= 30:

        score += 13

    elif tx5 >= 15:

        score += 8

    elif tx5 >= 8:

        score += 4

    # --------------------------------------------------------
    # BUY/SELL BALANCE
    # --------------------------------------------------------

    if tx5 >= 10:

        if 0.48 <= buy_ratio <= 0.75:

            score += 15

        elif 0.75 < buy_ratio <= 0.85:

            score += 10

        elif buy_ratio > 0.90:

            # Слишком идеальный buy ratio может быть
            # подозрительным.
            score += 2

        elif buy_ratio < 0.35:

            score -= 10

            reasons.append(
                "слабая структура покупок"
            )

    # --------------------------------------------------------
    # TREND HEALTH
    # --------------------------------------------------------

    if 0 < p1h < 100:

        score += 10

    elif p1h >= 150:

        score -= 5

    quality = int(
        clamp(score)
    )

    return quality, reasons


# ============================================================
# RISK
# ============================================================

def calculate_risk(
    market,
    previous,
    security
):

    p5 = market["p5"]
    p1h = market["p1h"]

    liquidity = market["liquidity"]
    v24 = market["v24"]

    buys = market["buys5"]
    sells = market["sells5"]

    tx5 = market["tx5"]
    buy_ratio = market["buy_ratio"]

    risk = 0
    reasons = []

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity < 10_000:

        risk += 35
        reasons.append(
            "очень низкая ликвидность"
        )

    elif liquidity < 20_000:

        risk += 25
        reasons.append(
            "низкая ликвидность"
        )

    elif liquidity < 30_000:

        risk += 15
        reasons.append(
            "небольшая ликвидность"
        )

    elif liquidity < 50_000:

        risk += 5

    # --------------------------------------------------------
    # EXTREME GROWTH
    # --------------------------------------------------------

    if p1h >= 300:

        risk += 40
        reasons.append(
            "экстремальный рост 1ч"
        )

    elif p1h >= 200:

        risk += 30
        reasons.append(
            "очень сильный рост 1ч"
        )

    elif p1h >= 120:

        risk += 20
        reasons.append(
            "сильная перегретость"
        )

    elif p1h >= 80:

        risk += 10
        reasons.append(
            "сильный рост 1ч"
        )

    # --------------------------------------------------------
    # 5M SPIKE
    # --------------------------------------------------------

    if p5 >= 50:

        risk += 30
        reasons.append(
            "экстремальный памп 5м"
        )

    elif p5 >= 35:

        risk += 22
        reasons.append(
            "резкий памп 5м"
        )

    elif p5 >= 25:

        risk += 14
        reasons.append(
            "сильный памп 5м"
        )

    # --------------------------------------------------------
    # DUMP
    # --------------------------------------------------------

    if p5 <= -25:

        risk += 30
        reasons.append(
            "сильный dump 5м"
        )

    elif p5 <= -12:

        risk += 20
        reasons.append(
            "резкое падение 5м"
        )

    if p1h <= -30:

        risk += 25
        reasons.append(
            "сильный негативный тренд"
        )

    elif p1h <= -20:

        risk += 15
        reasons.append(
            "негативный тренд"
        )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

    turnover = safe_ratio(
        v24,
        liquidity
    )

    if turnover >= 100:

        risk += 20
        reasons.append(
            "аномально высокий оборот"
        )

    elif turnover >= 50:

        risk += 15
        reasons.append(
            "очень высокий оборот"
        )

    elif turnover >= 30:

        risk += 8

    # --------------------------------------------------------
    # SELL PRESSURE
    # --------------------------------------------------------

    if tx5 >= 15:

        if buy_ratio <= 0.30:

            risk += 25
            reasons.append(
                "сильное давление продавцов"
            )

        elif buy_ratio <= 0.40:

            risk += 15
            reasons.append(
                "продавцы доминируют"
            )

        elif buy_ratio >= 0.93:

            risk += 8
            reasons.append(
                "аномальный перевес покупок"
            )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    risk += security["risk_penalty"]

    reasons.extend(
        security["risk_reasons"]
    )

    return int(
        clamp(risk)
    ), reasons, turnover


# ============================================================
# SECURITY HEURISTICS
# ============================================================

def security_check(pair, market):

    penalty = 0
    reasons = []
    positive = []

    liquidity = market["liquidity"]

    base = pair.get(
        "baseToken",
        {}
    )

    quote = pair.get(
        "quoteToken",
        {}
    )

    address = base.get(
        "address",
        ""
    )

    name = base.get(
        "name",
        ""
    )

    symbol = base.get(
        "symbol",
        ""
    )

    # --------------------------------------------------------
    # Missing token address
    # --------------------------------------------------------

    if not address:

        penalty += 20

        reasons.append(
            "отсутствует token address"
        )

    # --------------------------------------------------------
    # Liquidity
    # --------------------------------------------------------

    if liquidity < 10_000:

        penalty += 20

        reasons.append(
            "очень низкая ликвидность"
        )

    elif liquidity >= 100_000:

        positive.append(
            "сильная ликвидность"
        )

    # --------------------------------------------------------
    # Token metadata
    # --------------------------------------------------------

    if not name:

        penalty += 5

        reasons.append(
            "нет имени токена"
        )

    if not symbol:

        penalty += 5

        reasons.append(
            "нет symbol"
        )

    # --------------------------------------------------------
    # Quote sanity
    # --------------------------------------------------------

    quote_symbol = str(
        quote.get(
            "symbol",
            ""
        )
    ).upper()

    if quote_symbol in {
        "SOL",
        "WSOL",
        "USDC",
        "USDT",
    }:

        positive.append(
            "нормальная quote-пара"
        )

    # --------------------------------------------------------
    # FDV / liquidity sanity
    # --------------------------------------------------------

    fdv = market["fdv"]

    if fdv > 0 and liquidity > 0:

        ratio = fdv / liquidity

        if ratio >= 10_000:

            penalty += 15

            reasons.append(
                "очень высокий FDV/liquidity"
            )

        elif ratio >= 5_000:

            penalty += 8

            reasons.append(
                "высокий FDV/liquidity"
            )

    # --------------------------------------------------------
    # Generic suspicious naming
    #
    # Это НЕ scam detector.
    # Только небольшой дополнительный penalty.
    # --------------------------------------------------------

    text = (
        f"{name} {symbol}"
    ).lower()

    suspicious_words = (
        "official",
        "claim",
        "airdrop",
        "free",
    )

    matches = 0

    for word in suspicious_words:

        if word in text:
            matches += 1

    if matches:

        penalty += min(
            8,
            matches * 4
        )

        reasons.append(
            "подозрительные metadata-паттерны"
        )

    return {
        "risk_penalty": int(
            clamp(penalty)
        ),
        "risk_reasons": reasons,
        "positive": positive,
        "checked": True,
    }


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(
    market,
    previous,
    volume_accel,
    tx_accel,
    price_accel
):

    p5 = market["p5"]
    p1h = market["p1h"]

    tx5 = market["tx5"]
    buy_ratio = market["buy_ratio"]

    score = 0
    reasons = []

    # ========================================================
    # 5M PRICE
    # ========================================================

    if 2 <= p5 < 5:

        score += 12

        reasons.append(
            "начало импульса 5м"
        )

    elif 5 <= p5 < 10:

        score += 24

        reasons.append(
            "хороший импульс 5м"
        )

    elif 10 <= p5 < 18:

        score += 28

        reasons.append(
            "сильный импульс 5м"
        )

    elif 18 <= p5 < 25:

        score += 24

        reasons.append(
            "сильное движение 5м"
        )

    elif 25 <= p5 < 35:

        score += 18

        reasons.append(
            "агрессивный импульс 5м"
        )

    elif 35 <= p5 < 50:

        score += 10

        reasons.append(
            "резкий price spike"
        )

    elif p5 >= 50:

        score += 3

        reasons.append(
            "экстремальный price spike"
        )

    elif -5 < p5 < 2:

        score += 0

    elif -12 < p5 <= -5:

        score -= 18

        reasons.append(
            "слабость 5м"
        )

    elif -20 < p5 <= -12:

        score -= 35

        reasons.append(
            "сильное падение 5м"
        )

    else:

        score -= 45

        reasons.append(
            "экстремальное падение 5м"
        )

    # ========================================================
    # 1H TREND
    # ========================================================

    if 3 <= p1h < 10:

        score += 10

        reasons.append(
            "положительный тренд 1ч"
        )

    elif 10 <= p1h < 25:

        score += 18

        reasons.append(
            "здоровый тренд 1ч"
        )

    elif 25 <= p1h < 50:

        score += 20

        reasons.append(
            "сильный тренд 1ч"
        )

    elif 50 <= p1h < 80:

        score += 15

        reasons.append(
            "сильный рост 1ч"
        )

    elif 80 <= p1h < 120:

        score += 8

        reasons.append(
            "поздний сильный рост 1ч"
        )

    elif 120 <= p1h < 200:

        score -= 5

        reasons.append(
            "движение уже зрелое"
        )

    elif p1h >= 200:

        score -= 25

        reasons.append(
            "экстремально зрелое движение"
        )

    elif p1h <= -20:

        score -= 30

        reasons.append(
            "негативный тренд 1ч"
        )

    # ========================================================
    # BUY PRESSURE
    # ========================================================

    if tx5 >= 8:

        if 0.62 <= buy_ratio < 0.72:

            score += 8

            reasons.append(
                "покупатели доминируют"
            )

        elif 0.72 <= buy_ratio < 0.85:

            score += 12

            reasons.append(
                "сильное давление покупателей"
            )

        elif 0.85 <= buy_ratio <= 0.92:

            score += 6

            reasons.append(
                "сильный перевес покупок"
            )

        elif buy_ratio > 0.92:

            score += 1

            reasons.append(
                "аномальный buy ratio"
            )

        elif buy_ratio < 0.40:

            score -= 15

            reasons.append(
                "продавцы доминируют"
            )

    # ========================================================
    # VOLUME ACCELERATION
    # ========================================================

    if volume_accel >= 5:

        score += 22

        reasons.append(
            f"объём ускорился {volume_accel:.1f}x"
        )

    elif volume_accel >= 3:

        score += 18

        reasons.append(
            f"объём ускорился {volume_accel:.1f}x"
        )

    elif volume_accel >= 2:

        score += 13

        reasons.append(
            f"объём ускорился {volume_accel:.1f}x"
        )

    elif volume_accel >= 1.5:

        score += 8

        reasons.append(
            f"объём растёт {volume_accel:.1f}x"
        )

    # ========================================================
    # TX ACCELERATION
    # ========================================================

    if tx_accel >= 4:

        score += 15

        reasons.append(
            f"TX ускорились {tx_accel:.1f}x"
        )

    elif tx_accel >= 2.5:

        score += 12

        reasons.append(
            f"TX ускорились {tx_accel:.1f}x"
        )

    elif tx_accel >= 1.5:

        score += 7

        reasons.append(
            f"TX растут {tx_accel:.1f}x"
        )

    # ========================================================
    # PRICE ACCELERATION
    # ========================================================

    if price_accel >= 10:

        score += 12

        reasons.append(
            "цена резко ускоряется"
        )

    elif price_accel >= 5:

        score += 8

        reasons.append(
            "цена ускоряется"
        )

    elif price_accel <= -10:

        score -= 15

        reasons.append(
            "импульс ослабевает"
        )

    # ========================================================
    # CONFIRMATION BONUS
    # ========================================================

    confirmations = 0

    if p5 > 4:
        confirmations += 1

    if p1h > 3:
        confirmations += 1

    if volume_accel >= 1.5:
        confirmations += 1

    if tx_accel >= 1.5:
        confirmations += 1

    if 0.60 <= buy_ratio <= 0.85 and tx5 >= 8:
        confirmations += 1

    if confirmations >= 4:

        score += 12

        reasons.append(
            "движение подтверждено несколькими метриками"
        )

    elif confirmations >= 3:

        score += 7

        reasons.append(
            "движение имеет подтверждение"
        )

    # ========================================================
    # FINAL
    # ========================================================

    return int(
        clamp(score)
    ), reasons


# ============================================================
# OVEREXTENSION
# ============================================================

def is_overextended(market):

    p5 = market["p5"]
    p1h = market["p1h"]

    if p1h >= EXTREME_1H:
        return True

    if (
        p1h >= VERY_HIGH_1H
        and p5 >= 5
    ):
        return True

    if (
        p1h >= 100
        and p5 >= 25
    ):
        return True

    return False


# ============================================================
# DUMP
# ============================================================

def is_dumping(market):

    p5 = market["p5"]
    p1h = market["p1h"]

    if p5 <= SEVERE_DUMP_5M:
        return True

    if (
        p5 <= DUMP_5M
        and p1h < 0
    ):
        return True

    if (
        p1h <= DUMP_1H
        and p5 < 0
    ):
        return True

    return False


# ============================================================
# SPIKE / FAKEOUT DETECTION
# ============================================================

def spike_quality(
    market,
    volume_accel,
    tx_accel
):

    p5 = market["p5"]
    tx5 = market["tx5"]

    if p5 < 20:
        return 0

    score = 0

    if volume_accel >= 2:
        score += 30

    elif volume_accel >= 1.5:
        score += 20

    elif volume_accel >= 1.2:
        score += 10

    if tx_accel >= 2:
        score += 30

    elif tx_accel >= 1.5:
        score += 20

    elif tx_accel >= 1.2:
        score += 10

    if tx5 >= 50:
        score += 25

    elif tx5 >= 25:
        score += 18

    elif tx5 >= 10:
        score += 10

    return int(
        clamp(score)
    )


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_confidence(
    market,
    momentum,
    quality,
    risk,
    previous,
    volume_accel,
    tx_accel,
    security
):

    score = 0

    p5 = market["p5"]
    p1h = market["p1h"]

    tx5 = market["tx5"]
    buy_ratio = market["buy_ratio"]

    history_exists = (
        previous is not None
    )

    # --------------------------------------------------------
    # DATA DEPTH
    # --------------------------------------------------------

    if history_exists:
        score += 15
    else:
        score += 5

    if tx5 >= 30:
        score += 15

    elif tx5 >= 15:
        score += 10

    elif tx5 >= 8:
        score += 5

    # --------------------------------------------------------
    # MOMENTUM
    #
    # Confidence не может сделать слабый momentum
    # сильным. Это ключевое отличие от старой модели.
    # --------------------------------------------------------

    if momentum >= 80:
        score += 25

    elif momentum >= 70:
        score += 20

    elif momentum >= 60:
        score += 15

    elif momentum >= 45:
        score += 8

    else:
        score += 0

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    confirmations = 0

    if p5 > 4:
        confirmations += 1

    if p1h > 3:
        confirmations += 1

    if volume_accel >= 1.5:
        confirmations += 1

    if tx_accel >= 1.5:
        confirmations += 1

    if 0.60 <= buy_ratio <= 0.85:
        confirmations += 1

    if confirmations >= 4:
        score += 25

    elif confirmations >= 3:
        score += 18

    elif confirmations >= 2:
        score += 10

    # --------------------------------------------------------
    # QUALITY
    #
    # Quality здесь только подтверждает данные,
    # а не создаёт momentum.
    # --------------------------------------------------------

    if quality >= 80:
        score += 10

    elif quality >= 65:
        score += 8

    elif quality >= 50:
        score += 5

    # --------------------------------------------------------
    # RISK
    # --------------------------------------------------------

    if risk >= 70:
        score -= 30

    elif risk >= 50:
        score -= 20

    elif risk >= 35:
        score -= 10

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    if security["risk_penalty"] >= 25:
        score -= 15

    elif security["risk_penalty"] >= 10:
        score -= 5

    return round(
        clamp(score),
        1
    )


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(
    market,
    momentum,
    quality,
    confidence,
    risk
):

    p5 = market["p5"]
    p1h = market["p1h"]

    # --------------------------------------------------------
    # DUMP
    # --------------------------------------------------------

    if is_dumping(market):

        return "🔻 DUMPING"

    # --------------------------------------------------------
    # OVEREXTENDED
    # --------------------------------------------------------

    if is_overextended(market):

        return "🔴 OVEREXTENDED"

    # --------------------------------------------------------
    # VERY STRONG
    # --------------------------------------------------------

    if (
        momentum >= VERY_STRONG_MOMENTUM
        and quality >= STRONG_QUALITY
        and confidence >= 70
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
    ):

        return "🚨 VERY STRONG"

    # --------------------------------------------------------
    # STRONG
    # --------------------------------------------------------

    if (
        momentum >= STRONG_MOMENTUM
        and quality >= STRONG_QUALITY
        and confidence >= MIN_CONFIDENCE
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
    ):

        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        momentum >= EARLY_MOMENTUM
        and quality >= EARLY_QUALITY
        and confidence >= MIN_CONFIDENCE
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
    ):

        return "🟢 EARLY"

    # --------------------------------------------------------
    # WATCH
    # --------------------------------------------------------

    if (
        momentum >= 38
        and p5 > 0
        and confidence >= 40
        and risk < 70
    ):

        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ============================================================
# SIGNAL ENGINE
# ============================================================

def get_signal_type(item):

    stage = item["stage"]

    if stage in (
        "🔻 DUMPING",
        "🔴 OVEREXTENDED",
        "⚪ LOW MOMENTUM"
    ):
        return None

    market = item["market"]

    p5 = market["p5"]
    p1h = market["p1h"]

    momentum = item["momentum"]
    quality = item["quality"]
    confidence = item["confidence"]
    risk = item["risk"]

    spike = item["spike_quality"]

    price_accel = item["price_accel"]
    volume_accel = item["volume_accel"]
    tx_accel = item["tx_accel"]

    # ========================================================
    # BREAKOUT
    # ========================================================

    if (
        momentum >= BREAKOUT_MOMENTUM
        and confidence >= MIN_CONFIDENCE
        and quality >= BREAKOUT_QUALITY
        and risk < MAX_SIGNAL_RISK
        and BREAKOUT_MIN_5M <= p5 <= BREAKOUT_MAX_5M
        and p1h >= BREAKOUT_MIN_1H
        and p1h < BREAKOUT_MAX_1H
        and (
            volume_accel >= 1.5
            or tx_accel >= 1.5
            or price_accel >= 5
        )
    ):

        return "🚀 BREAKOUT"

    # ========================================================
    # CONFIRMED SPIKE
    # ========================================================

    if (
        p5 >= 18
        and p5 < 40
        and momentum >= 60
        and spike >= 45
        and confidence >= 55
        and risk < 60
    ):

        return "⚡ MOMENTUM"

    # ========================================================
    # CONTINUATION
    # ========================================================

    if (
        previous_positive_trend(item)
        and momentum >= 60
        and confidence >= 55
        and p5 > 2
        and p1h > 5
        and risk < 55
    ):

        return "📈 CONTINUATION"

    # ========================================================
    # STRONG
    # ========================================================

    if (
        momentum >= 75
        and confidence >= 65
        and quality >= 40
        and risk < MAX_SIGNAL_RISK
    ):

        return "🔥 STRONG"

    # ========================================================
    # VERY STRONG
    # ========================================================

    if (
        momentum >= 84
        and confidence >= 70
        and risk < 45
    ):

        return "🚨 VERY STRONG"

    # ========================================================
    # EARLY
    # ========================================================

    if (
        momentum >= 62
        and confidence >= 58
        and quality >= 30
        and risk < 55
        and p5 > 0
        and p1h > 0
    ):

        return "🟢 EARLY"

    return None


def previous_positive_trend(item):

    history = item.get(
        "history",
        []
    )

    if len(history) < 2:
        return False

    previous = history[-1]

    return (
        num(previous.get("p1h")) > 0
        and num(previous.get("momentum")) >= 45
    )


# ============================================================
# ANALYSIS
# ============================================================

def analyse(
    pair,
    old
):

    market = extract_market(
        pair
    )

    previous = previous_snapshot(
        old
    )

    # --------------------------------------------------------
    # ACCELERATION
    # --------------------------------------------------------

    volume_accel = (
        volume_acceleration(
            market,
            previous
        )
    )

    tx_accel = (
        transaction_acceleration(
            market,
            previous
        )
    )

    price_accel = (
        price_acceleration(
            market,
            previous
        )
    )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    security = security_check(
        pair,
        market
    )

    # --------------------------------------------------------
    # QUALITY
    # --------------------------------------------------------

    quality, quality_reasons = (
        calculate_quality(
            market
        )
    )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    momentum, momentum_reasons = (
        calculate_momentum(
            market,
            previous,
            volume_accel,
            tx_accel,
            price_accel
        )
    )

    # --------------------------------------------------------
    # RISK
    # --------------------------------------------------------

    risk, risk_reasons, turnover = (
        calculate_risk(
            market,
            previous,
            security
        )
    )

    # --------------------------------------------------------
    # SPIKE QUALITY
    # --------------------------------------------------------

    spike = spike_quality(
        market,
        volume_accel,
        tx_accel
    )

    # --------------------------------------------------------
    # CONFIDENCE
    # --------------------------------------------------------

    confidence = calculate_confidence(
        market,
        momentum,
        quality,
        risk,
        previous,
        volume_accel,
        tx_accel,
        security
    )

    # --------------------------------------------------------
    # SCORE DELTA
    # --------------------------------------------------------

    previous_momentum = num(
        old.get(
            "momentum"
        )
    )

    if previous is None:
        momentum_delta = 0
    else:
        momentum_delta = (
            momentum
            - previous_momentum
        )

    # --------------------------------------------------------
    # STAGE
    # --------------------------------------------------------

    stage = classify(
        market,
        momentum,
        quality,
        confidence,
        risk
    )

    return {
        "market": market,

        "momentum": momentum,
        "quality": quality,
        "confidence": confidence,
        "risk": risk,

        "momentum_delta": momentum_delta,

        "volume_accel": volume_accel,
        "tx_accel": tx_accel,
        "price_accel": price_accel,

        "turnover": turnover,

        "spike_quality": spike,

        "quality_reasons": quality_reasons,
        "momentum_reasons": momentum_reasons,
        "risk_reasons": risk_reasons,

        "security": security,

        "stage": stage,
    }


# ============================================================
# SIGNAL ELIGIBILITY
# ============================================================

def should_alert(
    item,
    old,
    signal_type,
    timestamp
):

    if not signal_type:
        return False

    last_alert = num(
        old.get(
            "last_alert"
        )
    )

    last_momentum = num(
        old.get(
            "last_alert_momentum"
        )
    )

    last_confidence = num(
        old.get(
            "last_alert_confidence"
        )
    )

    last_type = old.get(
        "last_alert_type",
        ""
    )

    # --------------------------------------------------------
    # First signal
    # --------------------------------------------------------

    if last_alert <= 0:
        return True

    # --------------------------------------------------------
    # New signal type
    # --------------------------------------------------------

    if signal_type != last_type:
        return True

    # --------------------------------------------------------
    # Cooldown
    # --------------------------------------------------------

    cooldown_passed = (
        timestamp - last_alert
        >= ALERT_COOLDOWN
    )

    if cooldown_passed:
        return True

    # --------------------------------------------------------
    # Significant improvement
    # --------------------------------------------------------

    momentum_jump = (
        item["momentum"]
        >= last_momentum
        + RE_ALERT_MOMENTUM
    )

    confidence_jump = (
        item["confidence"]
        >= last_confidence
        + RE_ALERT_CONFIDENCE
    )

    if momentum_jump:
        return True

    if confidence_jump:
        return True

    return False


# ============================================================
# STATE SNAPSHOT
# ============================================================

def create_snapshot(
    item,
    timestamp
):

    market = item["market"]

    return {
        "time": timestamp,

        "price_usd":
            market["price_usd"],

        "p5":
            market["p5"],

        "p1h":
            market["p1h"],

        "p6h":
            market["p6h"],

        "p24":
            market["p24"],

        "v5":
            market["v5"],

        "v1h":
            market["v1h"],

        "v24":
            market["v24"],

        "liquidity":
            market["liquidity"],

        "tx5":
            market["tx5"],

        "buys5":
            market["buys5"],

        "sells5":
            market["sells5"],

        "buy_ratio":
            market["buy_ratio"],

        "momentum":
            item["momentum"],

        "quality":
            item["quality"],

        "confidence":
            item["confidence"],

        "risk":
            item["risk"],
    }


# ============================================================
# STATE UPDATE
# ============================================================

def update_state(
    old,
    item,
    timestamp
):

    history = get_history(
        old
    )

    snapshot = create_snapshot(
        item,
        timestamp
    )

    history.append(
        snapshot
    )

    history = history[
        -HISTORY_LENGTH:
    ]

    return {
        "name":
            item["name"],

        "symbol":
            item["symbol"],

        "address":
            item["address"],

        "momentum":
            item["momentum"],

        "quality":
            item["quality"],

        "confidence":
            item["confidence"],

        "risk":
            item["risk"],

        "last_alert":
            old.get(
                "last_alert",
                0
            ),

        "last_alert_momentum":
            old.get(
                "last_alert_momentum",
                0
            ),

        "last_alert_confidence":
            old.get(
                "last_alert_confidence",
                0
            ),

        "last_alert_type":
            old.get(
                "last_alert_type",
                ""
            ),

        "last_stage":
            item["stage"],

        "history":
            history,

        "timestamp":
            timestamp,
    }


# ============================================================
# SCAN
# ============================================================

def scan():

    state = load_json(
        STATE_FILE,
        {}
    )

    signals = load_json(
        SIGNALS_FILE,
        []
    )

    if not isinstance(state, dict):
        state = {}

    if not isinstance(signals, list):
        signals = []

    addresses = get_addresses()

    print(
        f"Unique Solana addresses: "
        f"{len(addresses)}"
    )

    results = []

    new_state = {}

    checked = 0
    pairs_received = 0
    filtered = 0
    security_checked = 0

    timestamp = now_ts()
    iso = now_iso()

    for address in addresses:

        pair = get_pair(
            address
        )

        if not pair:
            continue

        pairs_received += 1

        base = pair.get(
            "baseToken",
            {}
        )

        name = (
            base.get(
                "name"
            )
            or "Unknown"
        )

        symbol = (
            base.get(
                "symbol"
            )
            or "?"
        ).upper()

        # ----------------------------------------------------
        # BLOCKED
        # ----------------------------------------------------

        if symbol in BLOCKED:

            filtered += 1

            continue

        market = extract_market(
            pair
        )

        liquidity = market[
            "liquidity"
        ]

        volume = market[
            "v24"
        ]

        # ----------------------------------------------------
        # MARKET FILTER
        # ----------------------------------------------------

        if liquidity < MIN_ANALYSIS_LIQUIDITY:

            filtered += 1

            continue

        if volume < MIN_VOLUME_24H:

            filtered += 1

            continue

        checked += 1

        old = state.get(
            address,
            {}
        )

        item = analyse(
            pair,
            old
        )

        item["name"] = name
        item["symbol"] = symbol
        item["address"] = address

        security_checked += 1

        # ----------------------------------------------------
        # PREVIOUS HISTORY
        # ----------------------------------------------------

        item["history"] = get_history(
            old
        )

        # ----------------------------------------------------
        # SAVE STATE
        # ----------------------------------------------------

        new_state[address] = (
            update_state(
                old,
                item,
                iso
            )
        )

        results.append(
            item
        )

    # ========================================================
    # SORT
    #
    # НЕ сортируем только по confidence.
    #
    # Momentum является главным фактором.
    # ========================================================

    results.sort(
        key=lambda x: (
            x["momentum"],
            x["confidence"],
            x["quality"],
            -x["risk"]
        ),
        reverse=True
    )

    print(
        f"Pairs received: "
        f"{pairs_received}"
    )

    print(
        f"Filtered: "
        f"{filtered}"
    )

    print(
        f"Candidates: "
        f"{len(results)}"
    )

    print(
        f"Security checked: "
        f"{security_checked}"
    )

    # ========================================================
    # TOP
    # ========================================================

    print()
    print(
        "TOP CANDIDATES:"
    )

    for item in results[
        :TOP_RESULTS
    ]:

        market = item[
            "market"
        ]

        print(
            f"{item['symbol']} | "
            f"Q {item['quality']}/100 | "
            f"M {item['momentum']}/100 | "
            f"C {item['confidence']}/100 | "
            f"Δ {item['momentum_delta']:+.0f} | "
            f"{item['stage']} | "
            f"5m {pct(market['p5'])} | "
            f"1h {pct(market['p1h'])} | "
            f"liq {money(market['liquidity'])} | "
            f"risk {item['risk']}/100"
        )

    # ========================================================
    # SIGNALS
    # ========================================================

    signal_items = []

    for item in results:

        signal_type = (
            get_signal_type(
                item
            )
        )

        if not signal_type:
            continue

        old = state.get(
            item["address"],
            {}
        )

        if not should_alert(
            item,
            old,
            signal_type,
            timestamp
        ):
            continue

        item["signal_type"] = (
            signal_type
        )

        signal_items.append(
            item
        )

        # Update alert state
        if item["address"] in new_state:

            new_state[
                item["address"]
            ][
                "last_alert"
            ] = timestamp

            new_state[
                item["address"]
            ][
                "last_alert_momentum"
            ] = item[
                "momentum"
            ]

            new_state[
                item["address"]
            ][
                "last_alert_confidence"
            ] = item[
                "confidence"
            ]

            new_state[
                item["address"]
            ][
                "last_alert_type"
            ] = signal_type

    # ========================================================
    # SAVE STATE
    # ========================================================

    atomic_save_json(
        STATE_FILE,
        new_state
    )

    # ========================================================
    # SAVE SIGNAL HISTORY
    # ========================================================

    for item in signal_items:

        market = item[
            "market"
        ]

        signals.append({

            "timestamp":
                iso,

            "address":
                item["address"],

            "name":
                item["name"],

            "symbol":
                item["symbol"],

            "signal":
                item["signal_type"],

            "stage":
                item["stage"],

            "momentum":
                item["momentum"],

            "quality":
                item["quality"],

            "confidence":
                item["confidence"],

            "risk":
                item["risk"],

            "momentum_delta":
                item["momentum_delta"],

            "p5":
                market["p5"],

            "p1h":
                market["p1h"],

            "liquidity":
                market["liquidity"],

            "volume24":
                market["v24"],

            "buy_ratio":
                market["buy_ratio"],

            "volume_acceleration":
                item["volume_accel"],

            "tx_acceleration":
                item["tx_accel"],

            "price_acceleration":
                item["price_accel"],

            "risk_reasons":
                item["risk_reasons"],

            "momentum_reasons":
                item["momentum_reasons"],
        })

    signals = signals[
        -1500:
    ]

    atomic_save_json(
        SIGNALS_FILE,
        signals
    )

    print(
        f"Strong signals: "
        f"{len(signal_items)}"
    )

    return signal_items


# ============================================================
# TELEGRAM ESCAPE
# ============================================================

def md_escape(text):

    text = str(text)

    chars = (
        "_*[]()~`>#+-=|{}.!\\"
    )

    for char in chars:
        text = text.replace(
            char,
            "\\" + char
        )

    return text


# ============================================================
# TELEGRAM FORMAT
# ============================================================

def risk_label(risk):

    if risk >= 70:
        return "🔴 HIGH"

    if risk >= 45:
        return "🟡 MEDIUM"

    if risk >= 25:
        return "🟠 ELEVATED"

    return "🟢 LOW"


def format_signal(item):

    market = item[
        "market"
    ]

    signal = item[
        "signal_type"
    ]

    address = item[
        "address"
    ]

    dex_url = (
        "https://dexscreener.com/solana/"
        + address
    )

    # --------------------------------------------------------
    # Reasons
    # --------------------------------------------------------

    reasons = []

    for reason in item[
        "momentum_reasons"
    ][:5]:

        reasons.append(
            "• " + reason
        )

    if (
        item["volume_accel"] >= 1.5
    ):

        reasons.append(
            f"• volume "
            f"{item['volume_accel']:.1f}x"
        )

    if (
        item["tx_accel"] >= 1.5
    ):

        reasons.append(
            f"• transactions "
            f"{item['tx_accel']:.1f}x"
        )

    if (
        market["buy_ratio"] >= 0.65
    ):

        reasons.append(
            "• buyers dominate"
        )

    # --------------------------------------------------------
    # Risk reasons
    # --------------------------------------------------------

    risk_reasons = []

    for reason in item[
        "risk_reasons"
    ][:4]:

        risk_reasons.append(
            "• " + reason
        )

    if not risk_reasons:

        risk_reasons.append(
            "• критических risk-факторов не обнаружено"
        )

    reasons_text = "\n".join(
        reasons
    )

    risk_text = "\n".join(
        risk_reasons
    )

    return (
        f"{signal}\n\n"

        f"🚀 {md_escape(item['name'])} "
        f"\\({md_escape(item['symbol'])}\\)\n\n"

        f"⚡ Momentum: "
        f"*{item['momentum']}/100*\n"

        f"⭐ Quality: "
        f"{item['quality']}/100\n"

        f"🎯 Confidence: "
        f"{item['confidence']}/100\n"

        f"📊 Momentum Δ: "
        f"{item['momentum_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{market['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{market['p1h']:+.1f}%\n\n"

        f"💧 Liquidity: "
        f"{money(market['liquidity'])}\n"

        f"💰 Volume 24h: "
        f"{money(market['v24'])}\n"

        f"🔄 TX 5m: "
        f"{market['tx5']}\n"

        f"🟢 Buy ratio: "
        f"{market['buy_ratio'] * 100:.0f}%\n\n"

        f"⚡ Volume acceleration: "
        f"{item['volume_accel']:.1f}x\n"

        f"⚡ TX acceleration: "
        f"{item['tx_accel']:.1f}x\n"

        f"📈 Price acceleration: "
        f"{item['price_accel']:+.1f}\n\n"

        f"⚠️ Risk: "
        f"{item['risk']}/100 "
        f"{risk_label(item['risk'])}\n\n"

        f"🧠 WHY:\n"
        f"{reasons_text}\n\n"

        f"⚠️ RISK:\n"
        f"{risk_text}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n\n"

        f"📋 `{address}`\n\n"

        f"Это алгоритмический сигнал, "
        f"а не гарантия роста."
    )


# ============================================================
# TELEGRAM SEND
# ============================================================

def send_telegram(text):

    if not BOT_TOKEN:

        print(
            "BOT_TOKEN is not configured"
        )

        return False

    if not CHAT_ID:

        print(
            "CHAT_ID is not configured"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id":
            CHAT_ID,

        "text":
            text,

        "parse_mode":
            "MarkdownV2",

        "disable_web_page_preview":
            False,
    }

    for attempt in range(
        MAX_RETRIES
    ):

        try:

            response = SESSION.post(
                url,
                json=payload,
                timeout=15
            )

            print(
                "Telegram:",
                response.status_code
            )

            if response.status_code == 200:

                return True

            if response.status_code == 429:

                try:

                    retry_after = (
                        response
                        .json()
                        .get(
                            "parameters",
                            {}
                        )
                        .get(
                            "retry_after",
                            5
                        )
                    )

                except Exception:

                    retry_after = 5

                time.sleep(
                    retry_after
                )

                continue

            # Markdown parsing can fail because
            # Dex token names may contain symbols.
            # Retry once as plain text.

            if (
                response.status_code == 400
                and "parse" in response.text.lower()
            ):

                fallback = payload.copy()

                fallback.pop(
                    "parse_mode",
                    None
                )

                fallback["text"] = (
                    text
                    .replace("\\", "")
                )

                fallback_response = (
                    SESSION.post(
                        url,
                        json=fallback,
                        timeout=15
                    )
                )

                print(
                    "Telegram fallback:",
                    fallback_response.status_code
                )

                return (
                    fallback_response
                    .status_code == 200
                )

            print(
                "Telegram response:",
                response.text[:500]
            )

        except Exception as e:

            print(
                "TELEGRAM ERROR:",
                e
            )

        if attempt < MAX_RETRIES - 1:

            time.sleep(
                2 ** attempt
            )

    return False


# ============================================================
# CLEAN OLD STATE
# ============================================================

def cleanup_state(state):

    if not isinstance(
        state,
        dict
    ):

        return {}

    cutoff = (
        time.time()
        - 7 * 24 * 60 * 60
    )

    cleaned = {}

    for address, item in state.items():

        if not isinstance(
            item,
            dict
        ):
            continue

        timestamp = item.get(
            "timestamp",
            ""
        )

        keep = True

        try:

            dt = datetime.fromisoformat(
                timestamp
            )

            item_ts = dt.timestamp()

            if item_ts < cutoff:

                keep = False

        except Exception:

            # Если timestamp сломан,
            # не удаляем автоматически.
            keep = True

        if keep:

            cleaned[
                address
            ] = item

    return cleaned


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "========================================"
    )

    print(
        "   SOLANA MOMENTUM SCANNER v6.0"
    )

    print(
        "========================================"
    )

    state = load_json(
        STATE_FILE,
        {}
    )

    if isinstance(
        state,
        dict
    ):

        state = cleanup_state(
            state
        )

        atomic_save_json(
            STATE_FILE,
            state
        )

    signals = scan()

    print()

    print(
        f"Strong signals to Telegram: "
        f"{len(signals)}"
    )

    for item in signals:

        text = format_signal(
            item
        )

        send_telegram(
            text
        )

        # Маленькая пауза между сообщениями,
        # чтобы не создавать burst.
        time.sleep(
            0.4
        )

    print()
    print(
        "SCAN COMPLETE"
    )


if __name__ == "__main__":
    main()
