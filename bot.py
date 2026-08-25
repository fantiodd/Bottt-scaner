import os
import json
import time
import math
import requests
from datetime import datetime, timezone


# ============================================================
# SOLANA MOMENTUM SCANNER v6.0
# ============================================================

VERSION = "6.0"

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 15

REQUEST_TIMEOUT = 15
MAX_RETRIES = 3

# ------------------------------------------------------------
# FILTERS
# ------------------------------------------------------------

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

# ------------------------------------------------------------
# SIGNAL THRESHOLDS
# ------------------------------------------------------------

EARLY_SCORE = 62
STRONG_SCORE = 72
VERY_STRONG_SCORE = 84

BREAKOUT_SCORE = 60

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MAX_SIGNAL_RISK = 55

DUMP_5M = -12
DUMP_1H = -25

EXTREME_5M = 35
EXTREME_1H = 150

# ------------------------------------------------------------
# BREAKOUT
# ------------------------------------------------------------

BREAKOUT_MIN_5M = 3
BREAKOUT_MIN_1H = 0

BREAKOUT_MIN_LIQUIDITY = 20_000

# ------------------------------------------------------------
# ALERTS
# ------------------------------------------------------------

ALERT_COOLDOWN = 30 * 60
RE_ALERT_SCORE_INCREASE = 12

# ------------------------------------------------------------
# HISTORY
# ------------------------------------------------------------

MAX_HISTORY = 60

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

# ------------------------------------------------------------
# SOURCES
# ------------------------------------------------------------

ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
]


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


def clamp(x, low=0, high=100):
    return max(low, min(high, x))


def safe_ratio(a, b):
    if b <= 0:
        return 0
    return a / b


def money(x):
    x = num(x)

    if x >= 1_000_000:
        return f"${x / 1_000_000:.2f}M"

    if x >= 1_000:
        return f"${x / 1_000:.1f}K"

    return f"${x:.0f}"


def pct(x):
    return f"{x:+.1f}%"


def load_json(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    tmp = filename + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(tmp, filename)


# ============================================================
# HTTP
# ============================================================

def request_json(url, timeout=REQUEST_TIMEOUT):

    last_error = None

    for attempt in range(MAX_RETRIES):

        try:

            response = requests.get(
                url,
                timeout=timeout
            )

            print(
                f"{url} -> "
                f"{response.status_code}"
            )

            if response.status_code == 200:
                return response.json()

            last_error = (
                f"HTTP {response.status_code}"
            )

        except Exception as e:

            last_error = str(e)

        if attempt < MAX_RETRIES - 1:
            time.sleep(1.5 * (attempt + 1))

    print(
        "REQUEST FAILED:",
        url,
        last_error
    )

    return None


# ============================================================
# ADDRESSES
# ============================================================

def get_addresses():

    addresses = set()

    for endpoint in ENDPOINTS:

        data = request_json(endpoint)

        if not isinstance(data, list):
            continue

        for item in data:

            if item.get("chainId") != "solana":
                continue

            address = item.get("tokenAddress")

            if address:
                addresses.add(address)

    return list(addresses)


# ============================================================
# BEST PAIR
# ============================================================

def get_pair(address):

    url = (
        "https://api.dexscreener.com/"
        f"token-pairs/v1/solana/{address}"
    )

    data = request_json(url)

    if not isinstance(data, list):
        return None

    valid = []

    for pair in data:

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
# PRICE / VOLUME / TX
# ============================================================

def extract(pair):

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

    p5 = num(change.get("m5"))
    p1h = num(change.get("h1"))

    v5 = num(volume.get("m5"))
    v1h = num(volume.get("h1"))
    v24 = num(volume.get("h24"))

    liquidity = num(
        liquidity_data.get("usd")
    )

    tx5 = txns.get(
        "m5",
        {}
    )

    buys = integer(
        tx5.get("buys")
    )

    sells = integer(
        tx5.get("sells")
    )

    total_tx = buys + sells

    buy_ratio = safe_ratio(
        buys,
        total_tx
    )

    turnover = safe_ratio(
        v24,
        liquidity
    )

    return {
        "p5": p5,
        "p1h": p1h,

        "v5": v5,
        "v1h": v1h,
        "v24": v24,

        "liquidity": liquidity,

        "buys5": buys,
        "sells5": sells,
        "total_tx": total_tx,

        "buy_ratio": buy_ratio,

        "turnover": turnover,
    }


# ============================================================
# HISTORY MOMENTUM
# ============================================================

def history_features(old):

    history = old.get(
        "history",
        []
    )

    if not history:
        return {
            "score_delta": 0,
            "price_acceleration": 0,
            "volume_acceleration": 1,
            "tx_acceleration": 1,
            "samples": 0,
        }

    last = history[-1]

    old_score = num(
        last.get("momentum")
    )

    return {
        "score_delta": 0,
        "price_acceleration": 0,
        "volume_acceleration": 1,
        "tx_acceleration": 1,
        "samples": len(history),
        "old_score": old_score,
    }


# ============================================================
# MOMENTUM
# ============================================================

def momentum_score(data, old):

    p5 = data["p5"]
    p1h = data["p1h"]

    v5 = data["v5"]
    v24 = data["v24"]

    liquidity = data["liquidity"]

    buys = data["buys5"]
    sells = data["sells5"]

    total_tx = data["total_tx"]
    buy_ratio = data["buy_ratio"]

    score = 0
    reasons = []

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    if 2 <= p5 < 5:
        score += 12
        reasons.append("ранний импульс 5м")

    elif 5 <= p5 < 10:
        score += 20
        reasons.append("хороший импульс 5м")

    elif 10 <= p5 < 20:
        score += 22
        reasons.append("сильный импульс 5м")

    elif 20 <= p5 < 30:
        score += 12
        reasons.append("сильное ускорение 5м")

    elif p5 >= 30:
        score -= 15
        reasons.append("экстремальный памп 5м")

    elif -5 < p5 < 0:
        score -= 4

    elif -12 <= p5 <= -5:
        score -= 12
        reasons.append("давление продавцов 5м")

    elif p5 < -12:
        score -= 25
        reasons.append("резкий дамп 5м")

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    if 3 <= p1h < 15:
        score += 15
        reasons.append("ранний тренд 1ч")

    elif 15 <= p1h < 30:
        score += 22
        reasons.append("здоровый тренд 1ч")

    elif 30 <= p1h < 60:
        score += 18
        reasons.append("сильный тренд 1ч")

    elif 60 <= p1h < 100:
        score += 8
        reasons.append("сильный рост 1ч")

    elif 100 <= p1h < 150:
        score -= 8
        reasons.append("рынок уже сильно вырос")

    elif p1h >= 150:
        score -= 25
        reasons.append("экстремальный рост 1ч")

    elif p1h <= -25:
        score -= 25
        reasons.append("негативный тренд 1ч")

    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

    if total_tx >= 10:

        if buy_ratio >= 0.75:
            score += 18
            reasons.append("сильное давление покупателей")

        elif buy_ratio >= 0.65:
            score += 12
            reasons.append("покупатели доминируют")

        elif buy_ratio >= 0.58:
            score += 6
            reasons.append("покупатели сильнее")

        elif buy_ratio <= 0.40:
            score -= 15
            reasons.append("продавцы доминируют")

        elif buy_ratio <= 0.30:
            score -= 20
            reasons.append("сильное давление продавцов")

    # --------------------------------------------------------
    # VOLUME / LIQUIDITY
    # --------------------------------------------------------

    turnover = safe_ratio(
        v24,
        liquidity
    )

    if 2 <= turnover <= 20:
        score += 7
        reasons.append("здоровый оборот")

    elif 20 < turnover <= 50:
        score += 3

    elif turnover > 50:
        score -= 12
        reasons.append("аномальный оборот")

    # --------------------------------------------------------
    # VOLUME ABSOLUTE
    # --------------------------------------------------------

    if v5 >= 100_000:
        score += 8

    elif v5 >= 50_000:
        score += 5

    elif v5 >= 10_000:
        score += 2

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity >= 250_000:
        score += 8

    elif liquidity >= 100_000:
        score += 7

    elif liquidity >= 50_000:
        score += 5

    elif liquidity >= 30_000:
        score += 3

    elif liquidity < 20_000:
        score -= 8
        reasons.append("низкая ликвидность")

    return clamp(score), reasons


# ============================================================
# QUALITY
# ============================================================

def quality_score(data):

    liquidity = data["liquidity"]
    v24 = data["v24"]
    total_tx = data["total_tx"]
    buy_ratio = data["buy_ratio"]

    quality = 0

    # Liquidity
    if liquidity >= 500_000:
        quality += 35

    elif liquidity >= 250_000:
        quality += 32

    elif liquidity >= 100_000:
        quality += 28

    elif liquidity >= 50_000:
        quality += 23

    elif liquidity >= 30_000:
        quality += 18

    elif liquidity >= 20_000:
        quality += 10

    # Volume
    if v24 >= 2_000_000:
        quality += 25

    elif v24 >= 1_000_000:
        quality += 22

    elif v24 >= 500_000:
        quality += 18

    elif v24 >= 100_000:
        quality += 13

    elif v24 >= 50_000:
        quality += 8

    # Transactions
    if total_tx >= 100:
        quality += 15

    elif total_tx >= 50:
        quality += 10

    elif total_tx >= 20:
        quality += 5

    # Balanced market
    if 0.40 <= buy_ratio <= 0.80:
        quality += 10

    # Liquidity / volume relation
    turnover = safe_ratio(
        v24,
        liquidity
    )

    if 1 <= turnover <= 30:
        quality += 10

    elif turnover > 100:
        quality -= 10

    return clamp(quality)


# ============================================================
# RISK
# ============================================================

def calculate_risk(data):

    p5 = data["p5"]
    p1h = data["p1h"]

    liquidity = data["liquidity"]

    buys = data["buys5"]
    sells = data["sells5"]

    turnover = data["turnover"]

    risk = 0
    reasons = []

    # Liquidity
    if liquidity < 10_000:
        risk += 40
        reasons.append("очень низкая ликвидность")

    elif liquidity < 20_000:
        risk += 25
        reasons.append("низкая ликвидность")

    elif liquidity < 30_000:
        risk += 12
        reasons.append("небольшая ликвидность")

    # Extreme growth
    if p1h >= 250:
        risk += 35
        reasons.append("экстремальный рост 1ч")

    elif p1h >= 180:
        risk += 25
        reasons.append("сильная перегретость")

    elif p1h >= 120:
        risk += 15
        reasons.append("перегретый 1ч тренд")

    # 5m pump
    if p5 >= 50:
        risk += 30
        reasons.append("экстремальный памп 5м")

    elif p5 >= 35:
        risk += 20
        reasons.append("сильный памп 5м")

    elif p5 >= 25:
        risk += 10
        reasons.append("ускорение 5м")

    # Dump
    if p5 <= -20:
        risk += 25
        reasons.append("резкий дамп")

    elif p5 <= -12:
        risk += 18
        reasons.append("сильное падение 5м")

    if p1h <= -30:
        risk += 20
        reasons.append("сильный негативный тренд")

    # Sellers
    total = buys + sells

    if total >= 20:

        ratio = safe_ratio(
            buys,
            total
        )

        if ratio < 0.30:
            risk += 20
            reasons.append("продавцы доминируют")

        elif ratio < 0.40:
            risk += 10
            reasons.append("давление продавцов")

        elif ratio > 0.92:
            risk += 8
            reasons.append("аномальный перевес покупок")

    # Turnover
    if turnover > 100:
        risk += 15
        reasons.append("аномальный оборот")

    return clamp(risk), reasons


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(data):

    p5 = data["p5"]
    p1h = data["p1h"]

    momentum = data["momentum"]
    quality = data["quality"]
    confidence = data["confidence"]
    risk = data["risk"]
    liquidity = data["liquidity"]

    # --------------------------------------------------------
    # DUMP
    # --------------------------------------------------------

    if (
        p5 <= -12
        or (
            p1h <= -25
            and p5 < 0
        )
    ):
        return "🔻 DUMPING"

    # --------------------------------------------------------
    # EXTREME PUMP
    # --------------------------------------------------------

    if (
        p5 >= 35
        and p1h > 0
    ):
        return "⚠️ PUMPING"

    # --------------------------------------------------------
    # OVEREXTENDED
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
    # REVERSAL
    # --------------------------------------------------------

    if (
        p5 >= 5
        and p1h < -15
        and risk < 60
    ):
        return "🔄 REVERSAL"

    # --------------------------------------------------------
    # VERY STRONG
    # --------------------------------------------------------

    if (
        momentum >= VERY_STRONG_SCORE
        and quality >= 50
        and confidence >= 65
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
        and liquidity >= 20_000
    ):
        return "🚨 VERY STRONG"

    # --------------------------------------------------------
    # STRONG
    # --------------------------------------------------------

    if (
        momentum >= STRONG_SCORE
        and quality >= 40
        and confidence >= 58
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
        and liquidity >= 20_000
    ):
        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        momentum >= EARLY_SCORE
        and quality >= 30
        and confidence >= 50
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
        and p1h < 100
        and liquidity >= 20_000
    ):
        return "🟢 EARLY"

    # --------------------------------------------------------
    # WATCH
    # --------------------------------------------------------

    if (
        momentum >= 35
        and p5 > 0
        and p1h > 0
        and risk < 70
    ):
        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ============================================================
# CONFIDENCE
# ============================================================

def confidence_score(
    momentum,
    quality,
    risk,
    data
):

    p5 = data["p5"]
    p1h = data["p1h"]

    total_tx = data["total_tx"]
    buy_ratio = data["buy_ratio"]

    confidence = 0

    # Momentum
    confidence += momentum * 0.40

    # Quality
    confidence += quality * 0.30

    # Risk
    confidence += (100 - risk) * 0.20

    # Transaction confirmation
    if total_tx >= 50:
        confidence += 5

    elif total_tx >= 20:
        confidence += 3

    # Buy pressure
    if 0.55 <= buy_ratio <= 0.80:
        confidence += 5

    # Trend alignment
    if p5 > 0 and p1h > 0:
        confidence += 5

    # Penalize disagreement
    if p5 > 5 and p1h < -10:
        confidence -= 10

    return round(
        clamp(confidence),
        1
    )


# ============================================================
# SIGNAL TYPE
# ============================================================

def signal_type(item):

    stage = item["stage"]

    momentum = item["momentum"]
    confidence = item["confidence"]
    risk = item["risk"]

    p5 = item["p5"]
    p1h = item["p1h"]

    liquidity = item["liquidity"]

    # Never buy these
    if stage in (
        "🔻 DUMPING",
        "🔴 OVEREXTENDED",
        "⚠️ PUMPING",
        "⚪ LOW MOMENTUM"
    ):
        return None

    # Breakout
    if (
        momentum >= BREAKOUT_SCORE
        and confidence >= 55
        and p5 >= BREAKOUT_MIN_5M
        and p1h > BREAKOUT_MIN_1H
        and liquidity >= BREAKOUT_MIN_LIQUIDITY
        and risk < MAX_SIGNAL_RISK
    ):
        return "🚀 BREAKOUT"

    if stage == "🚨 VERY STRONG":
        return "🚨 VERY STRONG"

    if stage == "🔥 STRONG":
        return "🔥 STRONG"

    if stage == "🟢 EARLY":
        return "🟢 EARLY"

    return None


# ============================================================
# ANALYZE
# ============================================================

def analyse(pair, old):

    data = extract(pair)

    momentum, momentum_reasons = momentum_score(
        data,
        old
    )

    quality = quality_score(
        data
    )

    risk, risk_reasons = calculate_risk(
        data
    )

    confidence = confidence_score(
        momentum,
        quality,
        risk,
        data
    )

    previous_momentum = num(
        old.get("momentum")
    )

    delta = (
        momentum
        - previous_momentum
    )

    data["momentum"] = momentum
    data["quality"] = quality
    data["risk"] = risk
    data["confidence"] = confidence

    data["momentum_delta"] = delta

    data["reasons"] = (
        momentum_reasons
        + risk_reasons
    )

    data["stage"] = classify(
        data
    )

    return data


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

        pair = get_pair(address)

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

        symbol = base.get(
            "symbol",
            "?"
        ).upper()

        if symbol in BLOCKED:
            filtered += 1
            continue

        data = extract(pair)

        liquidity = data["liquidity"]
        volume = data["v24"]

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

        item = analyse(
            pair,
            old
        )

        security_checked += 1

        item["name"] = name
        item["symbol"] = symbol
        item["address"] = address

        history = old.get(
            "history",
            []
        )

        history.append({
            "time": now_iso,
            "momentum": item["momentum"],
            "quality": item["quality"],
            "confidence": item["confidence"],
            "risk": item["risk"],
            "p5": item["p5"],
            "p1h": item["p1h"],
            "v5": item["v5"],
            "v24": item["v24"],
            "tx5": item["total_tx"],
        })

        history = history[-MAX_HISTORY:]

        new_state[address] = {
            "name": name,
            "symbol": symbol,

            "momentum": item["momentum"],
            "quality": item["quality"],
            "confidence": item["confidence"],
            "risk": item["risk"],

            "v5": item["v5"],
            "tx5": item["total_tx"],

            "last_alert": old.get(
                "last_alert",
                0
            ),

            "last_alert_score": old.get(
                "last_alert_score",
                0
            ),

            "last_alert_type": old.get(
                "last_alert_type",
                ""
            ),

            "history": history,

            "timestamp": now_iso,
        }

        results.append(item)

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    results.sort(
        key=lambda x: (
            x["confidence"],
            x["momentum"],
            x["quality"],
        ),
        reverse=True
    )

    print(
        f"Pairs received: {checked}"
    )

    print(
        f"Filtered: {filtered}"
    )

    print(
        f"Candidates: {len(results)}"
    )

    print(
        f"Security checked: "
        f"{security_checked}"
    )

    # --------------------------------------------------------
    # TOP
    # --------------------------------------------------------

    print("\nTOP CANDIDATES:")

    for item in results[:TOP_RESULTS]:

        print(
            f"{item['symbol']} | "
            f"Q {item['quality']}/100 | "
            f"M {item['momentum']}/100 | "
            f"C {item['confidence']}/100 | "
            f"Δ {item['momentum_delta']:+.0f} | "
            f"{item['stage']} | "
            f"5m {pct(item['p5'])} | "
            f"1h {pct(item['p1h'])} | "
            f"liq {money(item['liquidity'])} | "
            f"risk {item['risk']}/100"
        )

    # --------------------------------------------------------
    # SIGNALS
    # --------------------------------------------------------

    signal_items = []

    for item in results:

        stype = signal_type(
            item
        )

        if not stype:
            continue

        old = state.get(
            item["address"],
            {}
        )

        last_alert = num(
            old.get(
                "last_alert"
            )
        )

        last_score = num(
            old.get(
                "last_alert_score"
            )
        )

        last_type = old.get(
            "last_alert_type",
            ""
        )

        cooldown_passed = (
            last_alert == 0
            or
            now_ts - last_alert
            >= ALERT_COOLDOWN
        )

        score_jump = (
            item["momentum"]
            >= last_score
            + RE_ALERT_SCORE_INCREASE
        )

        changed_type = (
            stype != last_type
        )

        should_alert = (
            last_alert == 0
            or cooldown_passed
            or score_jump
            or changed_type
        )

        if not should_alert:
            continue

        item["signal_type"] = stype

        signal_items.append(
            item
        )

        new_state[
            item["address"]
        ]["last_alert"] = now_ts

        new_state[
            item["address"]
        ]["last_alert_score"] = (
            item["momentum"]
        )

        new_state[
            item["address"]
        ]["last_alert_type"] = stype

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_json(
        STATE_FILE,
        new_state
    )

    for item in signal_items:

        signals.append({
            "timestamp": now_iso,
            "address": item["address"],
            "symbol": item["symbol"],
            "signal": item["signal_type"],
            "momentum": item["momentum"],
            "quality": item["quality"],
            "confidence": item["confidence"],
            "risk": item["risk"],
            "p5": item["p5"],
            "p1h": item["p1h"],
            "liquidity": item["liquidity"],
        })

    signals = signals[-1000:]

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

    reasons = ", ".join(
        item["reasons"][:7]
    )

    risk = item["risk"]

    if risk >= 70:
        risk_text = "🔴 HIGH"

    elif risk >= 40:
        risk_text = "🟡 MEDIUM"

    else:
        risk_text = "🟢 LOW"

    address = item["address"]

    dex_url = (
        "https://dexscreener.com/solana/"
        + address
    )

    return (
        f"{item['signal_type']}\n\n"

        f"🚀 {item['name']} "
        f"({item['symbol']})\n\n"

        f"⚡ Momentum: "
        f"{item['momentum']}/100\n"

        f"⭐ Quality: "
        f"{item['quality']}/100\n"

        f"🎯 Confidence: "
        f"{item['confidence']}/100\n"

        f"📊 Momentum Δ: "
        f"{item['momentum_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{pct(item['p5'])}\n"

        f"📈 1h: "
        f"{pct(item['p1h'])}\n\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n"

        f"🔄 TX 5m: "
        f"{item['total_tx']}\n"

        f"🟢 Buys: "
        f"{item['buys5']}\n"

        f"🔴 Sells: "
        f"{item['sells5']}\n\n"

        f"⚠️ Risk: "
        f"{risk}/100 "
        f"({risk_text})\n\n"

        f"🧠 {reasons}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n\n"

        f"📋 `{address}`\n\n"

        f"⚠️ Алгоритмический сигнал. "
        f"Не гарантия роста."
    )


def send_telegram(text):

    if not CHAT_ID:
        print(
            "CHAT_ID is not configured"
        )
        return

    url = (
        "https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=15
        )

        print(
            "Telegram:",
            response.status_code,
            response.text[:300]
        )

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            e
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 40)
    print(
        f"   SOLANA MOMENTUM SCANNER v{VERSION}"
    )
    print("=" * 40)

    signals = scan()

    print(
        f"Strong signals to Telegram: "
        f"{len(signals)}"
    )

    for item in signals:

        send_telegram(
            format_signal(item)
        )


if __name__ == "__main__":
    main()
