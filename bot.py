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
# Основные идеи:
# - несколько источников DexScreener
# - история токена между запусками
# - momentum / quality / confidence / risk
# - acceleration цены, объёма и транзакций
# - buy/sell pressure
# - breakout detection
# - early detection
# - reversal detection
# - overextended / dumping detection
# - защита от повторных Telegram-алертов
# - retry для API
# - безопасное сохранение state
#
# ВАЖНО:
# Это аналитический сканер, а не гарантия прибыли.
# ============================================================


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 15

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

# Минимум данных истории для более уверенных сигналов
MIN_HISTORY_FOR_CONFIRMATION = 2

# ------------------------------------------------------------
# SIGNAL THRESHOLDS
# ------------------------------------------------------------

EARLY_SCORE = 65
STRONG_SCORE = 75
VERY_STRONG_SCORE = 85

BREAKOUT_SCORE = 65
BREAKOUT_DELTA = 20

# ------------------------------------------------------------
# PRICE
# ------------------------------------------------------------

BREAKOUT_MIN_5M = 4
BREAKOUT_MIN_1H = 0

DUMP_5M = -12
DUMP_1H = -20

EXTREME_1H = 150
EXTREME_5M = 35

# ------------------------------------------------------------
# LIQUIDITY
# ------------------------------------------------------------

BREAKOUT_MIN_LIQUIDITY = 20_000
GOOD_LIQUIDITY = 50_000
EXCELLENT_LIQUIDITY = 100_000

# ------------------------------------------------------------
# ALERTS
# ------------------------------------------------------------

ALERT_COOLDOWN = 30 * 60
RE_ALERT_SCORE_INCREASE = 12

# ------------------------------------------------------------
# API
# ------------------------------------------------------------

REQUEST_TIMEOUT = 15
MAX_RETRIES = 3

ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
]

PAIR_ENDPOINT = (
    "https://api.dexscreener.com/"
    "token-pairs/v1/solana/{}"
)

# ------------------------------------------------------------
# BLOCKED
# ------------------------------------------------------------

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
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "Mozilla/5.0 "
        "SolanaMomentumScanner/6.0"
})


def request_json(url, method="GET", **kwargs):

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = SESSION.request(
                method,
                url,
                timeout=REQUEST_TIMEOUT,
                **kwargs
            )

            if response.status_code == 200:

                return response.json()

            if response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                wait = min(
                    2 ** attempt,
                    8
                )

                time.sleep(wait)
                continue

            print(
                f"HTTP {response.status_code}: "
                f"{url}"
            )

            return None

        except Exception as e:

            if attempt >= MAX_RETRIES:

                print(
                    f"REQUEST ERROR: {url} | {e}"
                )

                return None

            time.sleep(
                min(2 ** attempt, 8)
            )

    return None


# ============================================================
# UTILS
# ============================================================

def num(x):

    try:
        return float(x or 0)

    except Exception:
        return 0.0


def integer(x):

    try:
        return int(x or 0)

    except Exception:
        return 0


def clamp(value, low=0, high=100):

    return max(
        low,
        min(high, value)
    )


def money(x):

    x = num(x)

    if x >= 1_000_000:

        return (
            f"${x / 1_000_000:.2f}M"
        )

    if x >= 1_000:

        return (
            f"${x / 1_000:.1f}K"
        )

    return f"${x:.0f}"


def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"LOAD ERROR {filename}: {e}"
        )

        return default


def save_json(filename, data):

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

        os.replace(
            temp,
            filename
        )

    except Exception as e:

        print(
            f"SAVE ERROR {filename}: {e}"
        )


def safe_ratio(a, b):

    if b <= 0:
        return 0

    return a / b


def pct(value):

    return f"{num(value):+.1f}%"


# ============================================================
# TOKEN ADDRESSES
# ============================================================

def get_addresses():

    addresses = set()

    for endpoint in ENDPOINTS:

        data = request_json(endpoint)

        print(
            f"{endpoint} -> "
            f"{'OK' if data is not None else 'ERROR'}"
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
# BEST PAIR
# ============================================================

def get_pair(address):

    url = PAIR_ENDPOINT.format(address)

    data = request_json(url)

    if not isinstance(data, list):
        return None

    if not data:
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
        key=lambda p: num(
            p.get(
                "liquidity",
                {}
            ).get("usd")
        ),
        reverse=True
    )

    return valid[0]


# ============================================================
# HISTORY
# ============================================================

def history_stats(history):

    if not history:
        return {
            "samples": 0,
            "score_avg": 0,
            "score_min": 0,
            "score_max": 0,
            "score_trend": 0,
            "price_trend": 0,
            "risk_avg": 0,
        }

    scores = [
        num(x.get("score"))
        for x in history
    ]

    p5s = [
        num(x.get("p5"))
        for x in history
    ]

    risks = [
        num(x.get("risk"))
        for x in history
    ]

    score_trend = 0
    price_trend = 0

    if len(scores) >= 2:

        score_trend = (
            scores[-1]
            - scores[0]
        )

        price_trend = (
            p5s[-1]
            - p5s[0]
        )

    return {
        "samples": len(history),
        "score_avg": (
            sum(scores) / len(scores)
        ),
        "score_min": min(scores),
        "score_max": max(scores),
        "score_trend": score_trend,
        "price_trend": price_trend,
        "risk_avg": (
            sum(risks) / len(risks)
        ),
    }


# ============================================================
# RISK
# ============================================================

def calculate_risk(
    p5,
    p1h,
    liquidity,
    v24,
    buys,
    sells,
    turnover,
    volume_acceleration,
    tx_acceleration,
):

    risk = 0
    reasons = []

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity < 10_000:

        risk += 40

        reasons.append(
            "очень низкая ликвидность"
        )

    elif liquidity < 20_000:

        risk += 25

        reasons.append(
            "низкая ликвидность"
        )

    elif liquidity < 30_000:

        risk += 12

        reasons.append(
            "небольшая ликвидность"
        )

    # --------------------------------------------------------
    # EXTREME GROWTH
    # --------------------------------------------------------

    if p1h >= 250:

        risk += 35

        reasons.append(
            "экстремальный рост 1ч"
        )

    elif p1h >= 180:

        risk += 28

        reasons.append(
            "очень сильный рост 1ч"
        )

    elif p1h >= 120:

        risk += 20

        reasons.append(
            "перегретый рост 1ч"
        )

    elif p1h >= 80:

        risk += 10

        reasons.append(
            "сильный рост 1ч"
        )

    # --------------------------------------------------------
    # 5M PUMP
    # --------------------------------------------------------

    if p5 >= 50:

        risk += 30

        reasons.append(
            "экстремальный памп 5м"
        )

    elif p5 >= 35:

        risk += 25

        reasons.append(
            "резкий памп 5м"
        )

    elif p5 >= 25:

        risk += 15

        reasons.append(
            "сильный памп 5м"
        )

    # --------------------------------------------------------
    # DUMP
    # --------------------------------------------------------

    if p5 <= -20:

        risk += 30

        reasons.append(
            "экстремальное падение 5м"
        )

    elif p5 <= DUMP_5M:

        risk += 20

        reasons.append(
            "резкое падение 5м"
        )

    if p1h <= -30:

        risk += 25

        reasons.append(
            "сильный негативный тренд"
        )

    elif p1h <= DUMP_1H:

        risk += 15

        reasons.append(
            "негативный тренд 1ч"
        )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

    if turnover > 100:

        risk += 25

        reasons.append(
            "экстремальный оборот"
        )

    elif turnover > 50:

        risk += 15

        reasons.append(
            "аномальный оборот"
        )

    # --------------------------------------------------------
    # VOLUME ACCELERATION
    # --------------------------------------------------------

    if volume_acceleration >= 8:

        risk += 10

        reasons.append(
            "аномальное ускорение объёма"
        )

    # --------------------------------------------------------
    # TX ACCELERATION
    # --------------------------------------------------------

    if tx_acceleration >= 8:

        risk += 10

        reasons.append(
            "аномальное ускорение сделок"
        )

    # --------------------------------------------------------
    # BUY / SELL
    # --------------------------------------------------------

    total = buys + sells

    if total >= 20:

        ratio = safe_ratio(
            buys,
            total
        )

        if ratio < 0.25:

            risk += 25

            reasons.append(
                "сильное давление продавцов"
            )

        elif ratio < 0.35:

            risk += 15

            reasons.append(
                "продавцы доминируют"
            )

        elif ratio > 0.95:

            risk += 12

            reasons.append(
                "аномальный перевес покупок"
            )

    return clamp(risk), reasons


# ============================================================
# MOMENTUM SCORE
# ============================================================

def momentum_score(
    p5,
    p1h,
    liquidity,
    buys,
    sells,
    volume_acceleration,
    tx_acceleration,
    turnover,
):

    score = 0
    reasons = []

    # ========================================================
    # 5M
    # ========================================================

    if 1 <= p5 < 3:

        score += 8

        reasons.append(
            "начинается движение"
        )

    elif 3 <= p5 < 5:

        score += 15

        reasons.append(
            "импульс 5м"
        )

    elif 5 <= p5 < 10:

        score += 22

        reasons.append(
            "хороший импульс 5м"
        )

    elif 10 <= p5 < 20:

        score += 20

        reasons.append(
            "сильный импульс 5м"
        )

    elif 20 <= p5 < 30:

        score += 10

        reasons.append(
            "сильный памп 5м"
        )

    elif 30 <= p5 < 40:

        score += 2

        reasons.append(
            "движение уже перегревается"
        )

    elif p5 >= 40:

        score -= 10

        reasons.append(
            "экстремальный памп"
        )

    elif -5 < p5 < 0:

        score -= 3

    elif -10 <= p5 <= -5:

        score -= 12

        reasons.append(
            "падение 5м"
        )

    elif p5 < -10:

        score -= 25

        reasons.append(
            "сильное падение 5м"
        )

    # ========================================================
    # 1H
    # ========================================================

    if 2 <= p1h < 8:

        score += 12

        reasons.append(
            "ранний тренд 1ч"
        )

    elif 8 <= p1h < 20:

        score += 20

        reasons.append(
            "здоровый тренд 1ч"
        )

    elif 20 <= p1h < 40:

        score += 22

        reasons.append(
            "сильный тренд 1ч"
        )

    elif 40 <= p1h < 70:

        score += 16

        reasons.append(
            "сильное движение 1ч"
        )

    elif 70 <= p1h < 100:

        score += 8

        reasons.append(
            "сильный рост 1ч"
        )

    elif 100 <= p1h < 150:

        score -= 5

        reasons.append(
            "рост становится перегретым"
        )

    elif 150 <= p1h < 250:

        score -= 15

        reasons.append(
            "сильная перегретость"
        )

    elif p1h >= 250:

        score -= 25

        reasons.append(
            "экстремальная перегретость"
        )

    elif -10 < p1h <= 0:

        score -= 3

    elif -20 < p1h <= -10:

        score -= 10

        reasons.append(
            "слабый тренд 1ч"
        )

    elif p1h <= -20:

        score -= 25

        reasons.append(
            "негативный тренд 1ч"
        )

    # ========================================================
    # LIQUIDITY
    # ========================================================

    if liquidity >= 500_000:

        score += 15

        reasons.append(
            "очень высокая ликвидность"
        )

    elif liquidity >= 100_000:

        score += 14

        reasons.append(
            "высокая ликвидность"
        )

    elif liquidity >= 50_000:

        score += 12

        reasons.append(
            "хорошая ликвидность"
        )

    elif liquidity >= 30_000:

        score += 9

    elif liquidity >= 20_000:

        score += 5

    else:

        score -= 15

        reasons.append(
            "низкая ликвидность"
        )

    # ========================================================
    # BUY / SELL
    # ========================================================

    total = buys + sells

    if total >= 8:

        ratio = safe_ratio(
            buys,
            total
        )

        if ratio >= 0.80:

            score += 20

            reasons.append(
                "сильное давление покупателей"
            )

        elif ratio >= 0.70:

            score += 15

            reasons.append(
                "покупатели доминируют"
            )

        elif ratio >= 0.60:

            score += 8

            reasons.append(
                "покупатели преобладают"
            )

        elif ratio <= 0.35:

            score -= 15

            reasons.append(
                "продавцы доминируют"
            )

        elif ratio <= 0.45:

            score -= 8

    # ========================================================
    # VOLUME
    # ========================================================

    if volume_acceleration >= 4:

        score += 20

        reasons.append(
            f"объём ускорился "
            f"{volume_acceleration:.1f}x"
        )

    elif volume_acceleration >= 2.5:

        score += 15

        reasons.append(
            f"объём ускоряется "
            f"{volume_acceleration:.1f}x"
        )

    elif volume_acceleration >= 1.7:

        score += 10

        reasons.append(
            f"объём растёт "
            f"{volume_acceleration:.1f}x"
        )

    elif volume_acceleration >= 1.3:

        score += 5

    # ========================================================
    # TRANSACTIONS
    # ========================================================

    if tx_acceleration >= 4:

        score += 12

        reasons.append(
            "резко растёт число сделок"
        )

    elif tx_acceleration >= 2:

        score += 8

        reasons.append(
            "число сделок ускоряется"
        )

    elif tx_acceleration >= 1.5:

        score += 4

    # ========================================================
    # TURNOVER
    # ========================================================

    if 1 <= turnover <= 20:

        score += 5

        reasons.append(
            "здоровый оборот"
        )

    elif 20 < turnover <= 50:

        score += 2

    elif turnover > 50:

        score -= 8

        reasons.append(
            "аномальный оборот"
        )

    return clamp(score), reasons


# ============================================================
# QUALITY
# ============================================================

def quality_score(
    liquidity,
    v24,
    total_tx,
    buy_ratio,
    p1h,
):

    quality = 0

    # Liquidity
    if liquidity >= 500_000:
        quality += 30

    elif liquidity >= 250_000:
        quality += 28

    elif liquidity >= 100_000:
        quality += 25

    elif liquidity >= 50_000:
        quality += 20

    elif liquidity >= 30_000:
        quality += 15

    elif liquidity >= 20_000:
        quality += 10

    # Volume
    if v24 >= 5_000_000:
        quality += 25

    elif v24 >= 1_000_000:
        quality += 23

    elif v24 >= 500_000:
        quality += 20

    elif v24 >= 100_000:
        quality += 16

    elif v24 >= 50_000:
        quality += 10

    elif v24 >= 25_000:
        quality += 5

    # Transactions
    if total_tx >= 5000:
        quality += 20

    elif total_tx >= 2000:
        quality += 18

    elif total_tx >= 1000:
        quality += 15

    elif total_tx >= 500:
        quality += 12

    elif total_tx >= 100:
        quality += 8

    elif total_tx >= 50:
        quality += 5

    # Buy/sell balance
    if 0.40 <= buy_ratio <= 0.80:
        quality += 15

    elif 0.30 <= buy_ratio <= 0.90:
        quality += 8

    # Reasonable trend
    if 0 < p1h < 80:
        quality += 10

    elif 80 <= p1h < 120:
        quality += 5

    return clamp(quality)


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_confidence(
    score,
    quality,
    risk,
    p5,
    p1h,
    volume_acceleration,
    tx_acceleration,
    buy_ratio,
    history_stats_data,
):

    confidence = 0

    # Base
    confidence += score * 0.35
    confidence += quality * 0.25
    confidence -= risk * 0.25

    # Momentum alignment
    if p5 > 0 and p1h > 0:
        confidence += 12

    if p5 > 3 and p1h > 10:
        confidence += 8

    # Volume confirmation
    if volume_acceleration >= 2:
        confidence += 8

    elif volume_acceleration >= 1.5:
        confidence += 4

    # TX confirmation
    if tx_acceleration >= 2:
        confidence += 6

    elif tx_acceleration >= 1.5:
        confidence += 3

    # Buy pressure
    if buy_ratio >= 0.70:
        confidence += 7

    elif buy_ratio >= 0.60:
        confidence += 4

    # Historical confirmation
    samples = history_stats_data["samples"]

    if samples >= 3:

        if history_stats_data["score_trend"] > 10:
            confidence += 7

        elif history_stats_data["score_trend"] > 0:
            confidence += 3

    elif samples >= 1:

        confidence += 2

    # Penalize conflicting movement
    if p5 > 10 and p1h < 0:
        confidence -= 10

    if p5 < 0 and p1h > 50:
        confidence -= 8

    return clamp(
        round(confidence, 1)
    )


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(item):

    p5 = item["p5"]
    p1h = item["p1h"]

    score = item["score"]
    quality = item["quality"]
    risk = item["risk"]
    confidence = item["confidence"]

    # --------------------------------------------------------
    # DUMPING
    # --------------------------------------------------------

    if (
        p5 <= -12
        or (
            p1h <= -20
            and p5 < 0
        )
    ):
        return "🔻 DUMPING"

    # --------------------------------------------------------
    # EXTENDED
    # --------------------------------------------------------

    if (
        p1h >= 200
        or (
            p1h >= 120
            and p5 >= 5
        )
    ):
        return "🔴 OVEREXTENDED"

    # --------------------------------------------------------
    # VERY STRONG
    # --------------------------------------------------------

    if (
        score >= 85
        and quality >= 50
        and confidence >= 65
        and risk < 50
        and p5 > 0
        and p1h > 0
    ):
        return "🚨 VERY STRONG"

    # --------------------------------------------------------
    # STRONG
    # --------------------------------------------------------

    if (
        score >= 75
        and quality >= 45
        and confidence >= 58
        and risk < 50
        and p5 > 0
        and p1h > 0
    ):
        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        score >= 65
        and confidence >= 50
        and quality >= 30
        and risk < 55
        and p5 > 0
        and p1h > 0
        and p1h < 80
    ):
        return "🟢 EARLY"

    # --------------------------------------------------------
    # WATCH
    # --------------------------------------------------------

    if (
        score >= 40
        and confidence >= 35
        and p5 > 0
        and p1h > 0
        and risk < 70
    ):
        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ============================================================
# SIGNAL TYPE
# ============================================================

def get_signal_type(item):

    stage = item["stage"]

    score = item["score"]
    confidence = item["confidence"]

    delta = item["score_delta"]

    p5 = item["p5"]
    p1h = item["p1h"]

    liquidity = item["liquidity"]
    risk = item["risk"]

    volume_acceleration = (
        item["volume_acceleration"]
    )

    tx_acceleration = (
        item["tx_acceleration"]
    )

    buy_ratio = (
        item["buy_ratio"]
    )

    history_samples = (
        item["history_samples"]
    )

    # Never alert on these
    if stage in (
        "🔻 DUMPING",
        "🔴 OVEREXTENDED",
        "⚪ LOW MOMENTUM",
    ):
        return None

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    breakout = (

        delta >= BREAKOUT_DELTA

        and score >= BREAKOUT_SCORE

        and confidence >= 48

        and p5 >= BREAKOUT_MIN_5M

        and p1h > BREAKOUT_MIN_1H

        and liquidity >= BREAKOUT_MIN_LIQUIDITY

        and risk < 60

        and (
            volume_acceleration >= 1.3
            or tx_acceleration >= 1.3
            or buy_ratio >= 0.65
        )
    )

    if breakout:

        return "🚀 BREAKOUT"

    # --------------------------------------------------------
    # REVERSAL
    # --------------------------------------------------------

    if (
        history_samples >= 2
        and delta >= 15
        and p5 > 2
        and p1h > 0
        and score >= 55
        and confidence >= 45
        and risk < 60
        and buy_ratio >= 0.60
    ):

        return "🔄 REVERSAL"

    # --------------------------------------------------------
    # VERY STRONG
    # --------------------------------------------------------

    if (
        score >= 85
        and confidence >= 65
        and risk < 50
    ):
        return "🚨 VERY STRONG"

    # --------------------------------------------------------
    # STRONG
    # --------------------------------------------------------

    if (
        score >= 75
        and confidence >= 58
        and risk < 50
    ):
        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        score >= 65
        and confidence >= 50
        and risk < 55
        and p5 > 0
        and p1h > 0
    ):
        return "🟢 EARLY"

    return None


# ============================================================
# ANALYZE
# ============================================================

def analyse(pair, old):

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

    p5 = num(
        change.get("m5")
    )

    p1h = num(
        change.get("h1")
    )

    v5 = num(
        volume.get("m5")
    )

    v1h = num(
        volume.get("h1")
    )

    v24 = num(
        volume.get("h24")
    )

    liquidity = num(
        liquidity_data.get("usd")
    )

    tx5 = txns.get(
        "m5",
        {}
    )

    buys5 = integer(
        tx5.get("buys")
    )

    sells5 = integer(
        tx5.get("sells")
    )

    total_tx = (
        buys5 + sells5
    )

    buy_ratio = safe_ratio(
        buys5,
        total_tx
    )

    # --------------------------------------------------------
    # OLD VALUES
    # --------------------------------------------------------

    old_v5 = num(
        old.get("v5")
    )

    old_tx = integer(
        old.get("tx5")
    )

    old_p5 = num(
        old.get("p5")
    )

    # --------------------------------------------------------
    # ACCELERATION
    # --------------------------------------------------------

    volume_acceleration = 1.0

    if old_v5 > 0 and v5 > 0:

        volume_acceleration = (
            v5 / old_v5
        )

    tx_acceleration = 1.0

    if old_tx > 0 and total_tx > 0:

        tx_acceleration = (
            total_tx / old_tx
        )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

    turnover = 0

    if liquidity > 0:

        turnover = (
            v24 / liquidity
        )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    score, reasons = momentum_score(

        p5=p5,
        p1h=p1h,
        liquidity=liquidity,
        buys=buys5,
        sells=sells5,
        volume_acceleration=volume_acceleration,
        tx_acceleration=tx_acceleration,
        turnover=turnover,
    )

    # --------------------------------------------------------
    # QUALITY
    # --------------------------------------------------------

    quality = quality_score(

        liquidity=liquidity,
        v24=v24,
        total_tx=total_tx,
        buy_ratio=buy_ratio,
        p1h=p1h,
    )

    # --------------------------------------------------------
    # RISK
    # --------------------------------------------------------

    risk, risk_reasons = calculate_risk(

        p5=p5,
        p1h=p1h,
        liquidity=liquidity,
        v24=v24,
        buys=buys5,
        sells=sells5,
        turnover=turnover,
        volume_acceleration=volume_acceleration,
        tx_acceleration=tx_acceleration,
    )

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    history = old.get(
        "history",
        []
    )

    hs = history_stats(
        history
    )

    # --------------------------------------------------------
    # CONFIDENCE
    # --------------------------------------------------------

    confidence = calculate_confidence(

        score=score,
        quality=quality,
        risk=risk,
        p5=p5,
        p1h=p1h,
        volume_acceleration=volume_acceleration,
        tx_acceleration=tx_acceleration,
        buy_ratio=buy_ratio,
        history_stats_data=hs,
    )

    # --------------------------------------------------------
    # PRICE ACCELERATION
    # --------------------------------------------------------

    price_acceleration = (
        p5 - old_p5
    )

    return {

        "score": score,

        "quality": quality,

        "confidence": confidence,

        "risk": risk,

        "risk_reasons":
            risk_reasons,

        "p5": p5,

        "p1h": p1h,

        "v5": v5,

        "v1h": v1h,

        "v24": v24,

        "liquidity": liquidity,

        "buys5": buys5,

        "sells5": sells5,

        "total_tx": total_tx,

        "buy_ratio": buy_ratio,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "price_acceleration":
            price_acceleration,

        "turnover": turnover,

        "reasons": reasons,

        "history_samples":
            hs["samples"],

        "history_score_trend":
            hs["score_trend"],

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

    addresses = get_addresses()

    print(
        f"Unique Solana addresses: "
        f"{len(addresses)}"
    )

    results = []

    new_state = {}

    checked = 0
    filtered = 0
    security_checked = 0

    now = datetime.now(
        timezone.utc
    )

    now_iso = now.isoformat()
    now_ts = now.timestamp()

    for address in addresses:

        pair = get_pair(
            address
        )

        if not pair:
            continue

        checked += 1

        base = pair.get(
            "baseToken",
            {}
        )

        name = base.get(
            "name",
            "Unknown"
        )

        symbol = str(
            base.get(
                "symbol",
                "?"
            )
        ).upper()

        if symbol in BLOCKED:

            filtered += 1
            continue

        liquidity = num(
            pair.get(
                "liquidity",
                {}
            ).get("usd")
        )

        volume = num(
            pair.get(
                "volume",
                {}
            ).get("h24")
        )

        if liquidity < MIN_LIQUIDITY:

            filtered += 1
            continue

        if volume < MIN_VOLUME_24H:

            filtered += 1
            continue

        old = state.get(
            address,
            {}
        )

        result = analyse(
            pair,
            old
        )

        previous_score = num(
            old.get(
                "score",
                0
            )
        )

        # ----------------------------------------------------
        # NEW TOKEN FIX
        # ----------------------------------------------------
        #
        # Для нового токена нельзя считать весь текущий
        # score как "рост".
        #

        if address not in state:

            score_delta = 0

        else:

            score_delta = (
                result["score"]
                - previous_score
            )

        result["name"] = name
        result["symbol"] = symbol
        result["address"] = address
        result["pair"] = pair

        result["score_delta"] = (
            score_delta
        )

        result["stage"] = classify(
            result
        )

        security_checked += 1

        # ----------------------------------------------------
        # HISTORY
        # ----------------------------------------------------

        history = old.get(
            "history",
            []
        )

        history.append({

            "time":
                now_iso,

            "score":
                result["score"],

            "quality":
                result["quality"],

            "confidence":
                result["confidence"],

            "risk":
                result["risk"],

            "p5":
                result["p5"],

            "p1h":
                result["p1h"],

            "v5":
                result["v5"],

            "tx5":
                result["total_tx"],

        })

        history = history[-60:]

        # ----------------------------------------------------
        # STATE
        # ----------------------------------------------------

        new_state[address] = {

            "name":
                name,

            "symbol":
                symbol,

            "score":
                result["score"],

            "quality":
                result["quality"],

            "confidence":
                result["confidence"],

            "risk":
                result["risk"],

            "previous_score":
                previous_score,

            "score_delta":
                score_delta,

            "p5":
                result["p5"],

            "p1h":
                result["p1h"],

            "v5":
                result["v5"],

            "tx5":
                result["total_tx"],

            "last_alert":
                old.get(
                    "last_alert",
                    0
                ),

            "last_alert_score":
                old.get(
                    "last_alert_score",
                    0
                ),

            "last_alert_type":
                old.get(
                    "last_alert_type",
                    ""
                ),

            "history":
                history,

            "timestamp":
                now_iso,
        }

        results.append(
            result
        )

    # ========================================================
    # SORT
    # ========================================================

    results.sort(

        key=lambda x: (

            x["confidence"],

            x["score"],

            x["quality"],

            x["score_delta"]

        ),

        reverse=True
    )

    print(
        f"Pairs received: "
        f"{checked}"
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

    print(
        "\nTOP CANDIDATES:"
    )

    for item in results[
        :TOP_RESULTS
    ]:

        print(

            f"{item['symbol']} | "

            f"Q "
            f"{item['quality']}/100 | "

            f"M "
            f"{item['score']}/100 | "

            f"C "
            f"{item['confidence']}/100 | "

            f"Δ "
            f"{item['score_delta']:+.0f} | "

            f"{item['stage']} | "

            f"5m "
            f"{item['p5']:+.1f}% | "

            f"1h "
            f"{item['p1h']:+.1f}% | "

            f"liq "
            f"{money(item['liquidity'])} | "

            f"risk "
            f"{item['risk']}/100"

        )

    # ========================================================
    # SIGNALS
    # ========================================================

    signal_items = []

    for item in results:

        signal_type = get_signal_type(
            item
        )

        if not signal_type:
            continue

        old = state.get(
            item["address"],
            {}
        )

        last_alert = num(
            old.get(
                "last_alert",
                0
            )
        )

        last_alert_score = num(
            old.get(
                "last_alert_score",
                0
            )
        )

        last_alert_type = old.get(
            "last_alert_type",
            ""
        )

        cooldown_passed = (

            last_alert == 0

            or

            (
                now_ts
                - last_alert
                >= ALERT_COOLDOWN
            )
        )

        score_jump = (

            item["score"]

            >=

            last_alert_score
            + RE_ALERT_SCORE_INCREASE
        )

        new_signal_type = (

            signal_type
            !=
            last_alert_type
        )

        should_alert = (

            last_alert == 0

            or cooldown_passed

            or score_jump

            or new_signal_type
        )

        if not should_alert:
            continue

        item["signal_type"] = (
            signal_type
        )

        signal_items.append(
            item
        )

        new_state[
            item["address"]
        ][
            "last_alert"
        ] = now_ts

        new_state[
            item["address"]
        ][
            "last_alert_score"
        ] = item["score"]

        new_state[
            item["address"]
        ][
            "last_alert_type"
        ] = signal_type

    # ========================================================
    # SAVE
    # ========================================================

    save_json(
        STATE_FILE,
        new_state
    )

    for item in signal_items:

        signals.append({

            "timestamp":
                now_iso,

            "address":
                item["address"],

            "symbol":
                item["symbol"],

            "name":
                item["name"],

            "signal":
                item["signal_type"],

            "score":
                item["score"],

            "quality":
                item["quality"],

            "confidence":
                item["confidence"],

            "risk":
                item["risk"],

            "delta":
                item["score_delta"],

            "p5":
                item["p5"],

            "p1h":
                item["p1h"],

            "liquidity":
                item["liquidity"],

            "volume":
                item["v24"],

        })

    signals = signals[-2000:]

    save_json(
        SIGNALS_FILE,
        signals
    )

    print(
        f"Strong signals: "
        f"{len(signal_items)}"
    )

    return signal_items


# ============================================================
# TELEGRAM
# ============================================================

def format_signal(item):

    risk = item["risk"]

    if risk >= 70:
        risk_text = "🔴 HIGH"

    elif risk >= 40:
        risk_text = "🟡 MEDIUM"

    else:
        risk_text = "🟢 LOW"

    confidence = item[
        "confidence"
    ]

    if confidence >= 75:
        confidence_text = "🔥 VERY HIGH"

    elif confidence >= 60:
        confidence_text = "🟢 HIGH"

    elif confidence >= 45:
        confidence_text = "🟡 MEDIUM"

    else:
        confidence_text = "⚪ LOW"

    reasons = ", ".join(
        item["reasons"][:7]
    )

    risk_reasons = ", ".join(
        item["risk_reasons"][:4]
    )

    if not risk_reasons:
        risk_reasons = "существенных рисков не обнаружено"

    address = item[
        "address"
    ]

    dex_url = (
        "https://dexscreener.com/solana/"
        + address
    )

    return (

        f"{item['signal_type']}\n\n"

        f"🚀 "
        f"{item['name']} "
        f"({item['symbol']})\n\n"

        f"🎯 Confidence: "
        f"{confidence}/100 "
        f"{confidence_text}\n"

        f"⚡ Momentum: "
        f"{item['score']}/100\n"

        f"⭐ Quality: "
        f"{item['quality']}/100\n"

        f"📊 Score change: "
        f"{item['score_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n"

        f"⚡ Price acceleration: "
        f"{item['price_acceleration']:+.1f}%\n\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n"

        f"📈 Volume accel: "
        f"{item['volume_acceleration']:.1f}x\n"

        f"🔄 TX accel: "
        f"{item['tx_acceleration']:.1f}x\n"

        f"🔁 Turnover: "
        f"{item['turnover']:.1f}x\n\n"

        f"🟢 Buys 5m: "
        f"{item['buys5']}\n"

        f"🔴 Sells 5m: "
        f"{item['sells5']}\n"

        f"⚖️ Buy ratio: "
        f"{item['buy_ratio'] * 100:.0f}%\n\n"

        f"⚠️ Risk: "
        f"{risk}/100 "
        f"{risk_text}\n"

        f"🛡 Risk factors: "
        f"{risk_reasons}\n\n"

        f"🧠 Signals:\n"
        f"{reasons}\n\n"

        f"📚 History samples: "
        f"{item['history_samples']}\n"

        f"📈 Historical score trend: "
        f"{item['history_score_trend']:+.0f}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n\n"

        f"📋 `{address}`\n\n"

        f"⚠️ Алгоритмический сигнал. "
        f"Он не гарантирует рост цены."
    )


def send_telegram(text):

    if not CHAT_ID:

        print(
            "CHAT_ID is not configured"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:

        response = SESSION.post(

            url,

            json={

                "chat_id":
                    CHAT_ID,

                "text":
                    text,

                "parse_mode":
                    "Markdown",

                "disable_web_page_preview":
                    False,

            },

            timeout=15
        )

        print(
            "Telegram:",
            response.status_code,
            response.text[:300]
        )

        return (
            response.status_code == 200
        )

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            e
        )

        return False


# ============================================================
# SUMMARY TELEGRAM
# ============================================================

def send_scan_summary(
    results,
    signals
):

    if not CHAT_ID:
        return

    if not results:
        return

    strong = [
        x for x in results
        if x["score"] >= 65
        and x["confidence"] >= 45
    ]

    if not strong:
        return

    top = strong[:5]

    lines = [
        "📡 SCAN SUMMARY",
        "",
        f"🔎 Candidates: {len(results)}",
        f"🚨 Signals: {len(signals)}",
        "",
    ]

    for item in top:

        lines.append(

            f"{item['symbol']} — "
            f"M {item['score']} | "
            f"C {item['confidence']} | "
            f"5m {item['p5']:+.1f}% | "
            f"1h {item['p1h']:+.1f}%"
        )

    send_telegram(
        "\n".join(lines)
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "========================================"
    )

    print(
        "   SOLANA MOMENTUM SCANNER v6.0"
    )

    print(
        "========================================"
    )

    signals = scan()

    print(
        f"Strong signals to Telegram: "
        f"{len(signals)}"
    )

    for item in signals:

        send_telegram(
            format_signal(item)
        )

        # Небольшая пауза между сообщениями
        time.sleep(0.5)


if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\nStopped."
        )

    except Exception as e:

        print(
            "\nFATAL ERROR:",
            repr(e)
        )

        raise
