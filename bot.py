import os
import json
import time
import requests

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 10

# ------------------------------------------------------------
# MARKET FILTERS
# ------------------------------------------------------------

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

# Не берём совсем мёртвые токены
MIN_TXNS_5M = 3

# ------------------------------------------------------------
# SIGNAL THRESHOLDS
# ------------------------------------------------------------

EARLY_SCORE = 65
STRONG_SCORE = 75
VERY_STRONG_SCORE = 85

# ------------------------------------------------------------
# BREAKOUT
# ------------------------------------------------------------

BREAKOUT_DELTA = 20
BREAKOUT_MIN_5M = 4
BREAKOUT_MIN_1H = 0
BREAKOUT_MIN_LIQUIDITY = 20_000
BREAKOUT_MAX_RISK = 55

# ------------------------------------------------------------
# ALERTS
# ------------------------------------------------------------

ALERT_COOLDOWN = 30 * 60
RE_ALERT_SCORE_INCREASE = 15

# ------------------------------------------------------------
# DUMP
# ------------------------------------------------------------

DUMP_5M = -12
DUMP_1H = -20

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MAX_SIGNAL_RISK = 55

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 12

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "SolanaMomentumScanner/3.0"
})


# ============================================================
# BLOCKED TOKENS
# ============================================================

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
# DEXSCREENER SOURCES
# ============================================================

ENDPOINTS = [

    "https://api.dexscreener.com/"
    "token-profiles/latest/v1",

    "https://api.dexscreener.com/"
    "token-boosts/latest/v1",

]


# ============================================================
# RUGCHECK
# ============================================================

RUGCHECK_BASE = (
    "https://api.rugcheck.xyz"
)


# ============================================================
# UTILS
# ============================================================

def num(value):

    try:
        return float(value or 0)

    except Exception:
        return 0.0


def integer(value):

    try:
        return int(value or 0)

    except Exception:
        return 0


def money(value):

    value = num(value)

    if value >= 1_000_000:

        return (
            f"${value / 1_000_000:.2f}M"
        )

    if value >= 1_000:

        return (
            f"${value / 1_000:.1f}K"
        )

    return f"${value:.0f}"


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

    except Exception:

        return default


def save_json(filename, data):

    try:

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            "SAVE ERROR:",
            filename,
            e
        )


def safe_get(url, **kwargs):

    try:

        return SESSION.get(
            url,
            timeout=HTTP_TIMEOUT,
            **kwargs
        )

    except Exception as e:

        print(
            "HTTP ERROR:",
            url,
            e
        )

        return None


# ============================================================
# TOKEN ADDRESSES
# ============================================================

def get_addresses():

    addresses = set()

    for endpoint in ENDPOINTS:

        response = safe_get(endpoint)

        if not response:
            continue

        print(
            f"{endpoint} -> "
            f"{response.status_code}"
        )

        if response.status_code != 200:
            continue

        try:

            data = response.json()

        except Exception:

            continue

        if not isinstance(data, list):
            continue

        for item in data:

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

    url = (
        "https://api.dexscreener.com/"
        f"token-pairs/v1/solana/{address}"
    )

    response = safe_get(url)

    if not response:
        return None

    if response.status_code != 200:
        return None

    try:

        data = response.json()

    except Exception:

        return None

    if not isinstance(data, list):
        return None

    if not data:
        return None

    # --------------------------------------------------------
    # Best pair = liquidity + volume
    # --------------------------------------------------------

    def pair_score(pair):

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

        return (
            liquidity * 0.7
            + min(volume, 1_000_000) * 0.3
        )

    data.sort(
        key=pair_score,
        reverse=True
    )

    return data[0]


# ============================================================
# RUGCHECK
# ============================================================

def get_rugcheck(address):

    """
    Дополнительный security слой.

    Если API недоступен:
    security_available = False

    Мы НЕ считаем отсутствие ответа
    доказательством безопасности.
    """

    url = (
        f"{RUGCHECK_BASE}/v1/tokens/"
        f"{address}/report"
    )

    response = safe_get(url)

    if not response:

        return {
            "available": False,
            "risk": 0,
            "risks": [],
            "raw": {}
        }

    if response.status_code != 200:

        return {
            "available": False,
            "risk": 0,
            "risks": [],
            "raw": {}
        }

    try:

        data = response.json()

    except Exception:

        return {
            "available": False,
            "risk": 0,
            "risks": [],
            "raw": {}
        }

    return parse_rugcheck(data)


def parse_rugcheck(data):

    risks = []

    # --------------------------------------------------------
    # RugCheck often exposes risks as an array.
    # --------------------------------------------------------

    raw_risks = data.get(
        "risks",
        []
    )

    if isinstance(raw_risks, list):

        for risk in raw_risks:

            if not isinstance(risk, dict):
                continue

            name = (
                risk.get("name")
                or risk.get("description")
                or risk.get("type")
                or "Unknown risk"
            )

            risks.append(
                str(name)
            )

    # --------------------------------------------------------
    # Try common score fields
    # --------------------------------------------------------

    raw_score = None

    for key in (
        "score",
        "riskScore",
        "rugScore"
    ):

        if key in data:

            raw_score = num(
                data.get(key)
            )

            break

    # RugCheck scores may be on a large scale.
    # We only convert obvious 0-10 scores.
    risk = 0

    if raw_score is not None:

        if 0 <= raw_score <= 10:

            risk = max(
                0,
                min(
                    100,
                    (10 - raw_score) * 10
                )
            )

        elif 0 <= raw_score <= 100:

            risk = raw_score

    # --------------------------------------------------------
    # Explicit risk levels
    # --------------------------------------------------------

    text = json.dumps(
        data,
        ensure_ascii=False
    ).lower()

    critical_words = [

        "mint authority",
        "freeze authority",
        "unlocked liquidity",
        "high holder concentration",
        "rugged",
        "honeypot",

    ]

    warning_count = sum(
        1
        for word in critical_words
        if word in text
    )

    if warning_count >= 1:

        risk = max(
            risk,
            min(
                90,
                warning_count * 20
            )
        )

    return {

        "available": True,

        "risk": int(
            max(
                0,
                min(
                    100,
                    risk
                )
            )
        ),

        "risks": risks[:10],

        "raw": data,

    }


# ============================================================
# MARKET RISK
# ============================================================

def calculate_market_risk(

    p5,
    p1h,
    liquidity,
    v24,
    buys,
    sells,
    turnover,
    total_tx

):

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

        risk += 22

        reasons.append(
            "низкая ликвидность"
        )

    elif liquidity < 30_000:

        risk += 10

        reasons.append(
            "тонкая ликвидность"
        )

    # --------------------------------------------------------
    # OVEREXTENSION
    # --------------------------------------------------------

    if p1h >= 250:

        risk += 35

        reasons.append(
            "экстремальный рост 1ч"
        )

    elif p1h >= 200:

        risk += 28

        reasons.append(
            "сильная перегретость"
        )

    elif p1h >= 120:

        risk += 18

        reasons.append(
            "токен сильно вырос"
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

    elif p5 >= 30:

        risk += 20

        reasons.append(
            "резкий памп 5м"
        )

    elif p5 >= 20:

        risk += 10

        reasons.append(
            "быстрый памп 5м"
        )

    # --------------------------------------------------------
    # DUMP
    # --------------------------------------------------------

    if p5 <= -20:

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

    if turnover >= 100:

        risk += 20

        reasons.append(
            "экстремальный оборот"
        )

    elif turnover >= 50:

        risk += 12

        reasons.append(
            "аномальный оборот"
        )

    # --------------------------------------------------------
    # BUY / SELL
    # --------------------------------------------------------

    if total_tx >= 10:

        ratio = (
            buys / total_tx
        )

        if ratio <= 0.30:

            risk += 20

            reasons.append(
                "сильное давление продавцов"
            )

        elif ratio <= 0.40:

            risk += 10

            reasons.append(
                "продавцы доминируют"
            )

        elif ratio >= 0.95:

            risk += 8

            reasons.append(
                "аномальный перевес покупок"
            )

    return (
        min(100, risk),
        reasons
    )


# ============================================================
# ANALYSIS
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

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    v5 = num(
        volume.get("m5")
    )

    v1h = num(
        volume.get("h1")
    )

    v24 = num(
        volume.get("h24")
    )

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    liquidity = num(
        liquidity_data.get("usd")
    )

    # --------------------------------------------------------
    # TRANSACTIONS
    # --------------------------------------------------------

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
        buys5
        + sells5
    )

    buy_ratio = 0

    if total_tx > 0:

        buy_ratio = (
            buys5 / total_tx
        )

    # ========================================================
    # MOMENTUM
    # ========================================================

    score = 0
    reasons = []

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    if 1 <= p5 < 3:

        score += 8

        reasons.append(
            "появился импульс 5м"
        )

    elif 3 <= p5 < 5:

        score += 15

        reasons.append(
            "ранний импульс 5м"
        )

    elif 5 <= p5 < 10:

        score += 22

        reasons.append(
            "хороший импульс 5м"
        )

    elif 10 <= p5 < 20:

        score += 18

        reasons.append(
            "сильный импульс 5м"
        )

    elif 20 <= p5 < 30:

        score += 8

        reasons.append(
            "сильный памп 5м"
        )

    elif p5 >= 30:

        score -= 15

        reasons.append(
            "слишком резкий памп"
        )

    elif -5 < p5 < 0:

        score -= 3

    elif -12 < p5 <= -5:

        score -= 15

        reasons.append(
            "падение 5м"
        )

    elif p5 <= -12:

        score -= 30

        reasons.append(
            "сильное падение 5м"
        )

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    if 2 <= p1h < 8:

        score += 12

        reasons.append(
            "ранний тренд 1ч"
        )

    elif 8 <= p1h < 15:

        score += 20

        reasons.append(
            "хороший тренд 1ч"
        )

    elif 15 <= p1h < 30:

        score += 25

        reasons.append(
            "здоровый тренд 1ч"
        )

    elif 30 <= p1h < 60:

        score += 15

        reasons.append(
            "сильный тренд 1ч"
        )

    elif 60 <= p1h < 100:

        score += 5

        reasons.append(
            "сильный рост 1ч"
        )

    elif 100 <= p1h < 150:

        score -= 10

        reasons.append(
            "тренд уже перегревается"
        )

    elif 150 <= p1h:

        score -= 25

        reasons.append(
            "экстремальный рост 1ч"
        )

    elif -20 < p1h < -5:

        score -= 10

        reasons.append(
            "слабый тренд 1ч"
        )

    elif p1h <= -20:

        score -= 25

        reasons.append(
            "негативный тренд 1ч"
        )

    # --------------------------------------------------------
    # 6H / 24H CONTEXT
    # --------------------------------------------------------

    if 0 < p6h < 100:

        score += 5

    elif p6h >= 150:

        score -= 5

    if p24 < -30:

        score -= 10

        reasons.append(
            "слабая динамика 24ч"
        )

    # ========================================================
    # LIQUIDITY
    # ========================================================

    if liquidity >= 150_000:

        score += 18

        reasons.append(
            "очень хорошая ликвидность"
        )

    elif liquidity >= 100_000:

        score += 15

        reasons.append(
            "высокая ликвидность"
        )

    elif liquidity >= 50_000:

        score += 13

        reasons.append(
            "хорошая ликвидность"
        )

    elif liquidity >= 30_000:

        score += 10

        reasons.append(
            "нормальная ликвидность"
        )

    elif liquidity >= 20_000:

        score += 5

        reasons.append(
            "приемлемая ликвидность"
        )

    else:

        score -= 20

        reasons.append(
            "низкая ликвидность"
        )

    # ========================================================
    # BUY / SELL PRESSURE
    # ========================================================

    if total_tx >= 5:

        if buy_ratio >= 0.75:

            score += 18

            reasons.append(
                "сильное давление покупателей"
            )

        elif buy_ratio >= 0.65:

            score += 13

            reasons.append(
                "покупатели доминируют"
            )

        elif buy_ratio >= 0.55:

            score += 6

            reasons.append(
                "покупатели немного сильнее"
            )

        elif buy_ratio <= 0.40:

            score -= 15

            reasons.append(
                "продавцы доминируют"
            )

    # ========================================================
    # VOLUME ACCELERATION
    # ========================================================

    old_v5 = num(
        old.get("v5")
    )

    volume_acceleration = 1.0

    if old_v5 > 0 and v5 > 0:

        volume_acceleration = (
            v5 / old_v5
        )

        if volume_acceleration >= 4:

            score += 20

            reasons.append(
                f"объём x{volume_acceleration:.1f}"
            )

        elif volume_acceleration >= 3:

            score += 16

            reasons.append(
                f"объём ускоряется x{volume_acceleration:.1f}"
            )

        elif volume_acceleration >= 2:

            score += 12

            reasons.append(
                f"объём растёт x{volume_acceleration:.1f}"
            )

        elif volume_acceleration >= 1.5:

            score += 8

            reasons.append(
                f"объём растёт x{volume_acceleration:.1f}"
            )

        elif volume_acceleration < 0.5:

            score -= 5

    # ========================================================
    # TRANSACTION ACCELERATION
    # ========================================================

    old_tx = integer(
        old.get("tx5")
    )

    tx_acceleration = 1.0

    if old_tx > 0 and total_tx > 0:

        tx_acceleration = (
            total_tx / old_tx
        )

        if tx_acceleration >= 3:

            score += 12

            reasons.append(
                "сделок резко больше"
            )

        elif tx_acceleration >= 2:

            score += 8

            reasons.append(
                "сделок становится больше"
            )

        elif tx_acceleration >= 1.5:

            score += 4

    # ========================================================
    # TURNOVER
    # ========================================================

    turnover = 0

    if liquidity > 0:

        turnover = (
            v24 / liquidity
        )

    if 2 <= turnover <= 20:

        score += 5

        reasons.append(
            "активный оборот"
        )

    elif turnover > 50:

        score -= 10

        reasons.append(
            "аномальный оборот"
        )

    # ========================================================
    # MOMENTUM SCORE
    # ========================================================

    score = int(
        max(
            0,
            min(
                100,
                score
            )
        )
    )

    # ========================================================
    # QUALITY SCORE
    # ========================================================

    quality = 0

    # liquidity
    if liquidity >= 150_000:
        quality += 30

    elif liquidity >= 100_000:
        quality += 27

    elif liquidity >= 50_000:
        quality += 24

    elif liquidity >= 30_000:
        quality += 20

    elif liquidity >= 20_000:
        quality += 12

    # volume
    if v24 >= 2_000_000:
        quality += 25

    elif v24 >= 1_000_000:
        quality += 22

    elif v24 >= 500_000:
        quality += 18

    elif v24 >= 100_000:
        quality += 15

    elif v24 >= 50_000:
        quality += 10

    # transactions
    if total_tx >= 1000:
        quality += 20

    elif total_tx >= 500:
        quality += 17

    elif total_tx >= 100:
        quality += 12

    elif total_tx >= 50:
        quality += 7

    # buy/sell balance
    if 0.45 <= buy_ratio <= 0.80:
        quality += 10

    # positive trend
    if 0 < p1h < 100:
        quality += 10

    quality = min(
        100,
        quality
    )

    # ========================================================
    # MARKET RISK
    # ========================================================

    market_risk, risk_reasons = calculate_market_risk(

        p5=p5,
        p1h=p1h,
        liquidity=liquidity,
        v24=v24,
        buys=buys5,
        sells=sells5,
        turnover=turnover,
        total_tx=total_tx

    )

    return {

        "score": score,

        "quality": quality,

        "market_risk": market_risk,

        "p5": p5,
        "p1h": p1h,
        "p6h": p6h,
        "p24": p24,

        "v5": v5,
        "v1h": v1h,
        "v24": v24,

        "liquidity": liquidity,

        "buys5": buys5,
        "sells5": sells5,
        "total_tx5": total_tx,

        "buy_ratio": buy_ratio,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "turnover":
            turnover,

        "reasons":
            reasons,

        "risk_reasons":
            risk_reasons,

    }


# ============================================================
# FINAL RISK
# ============================================================

def calculate_final_risk(
    market_risk,
    rugcheck
):

    if not rugcheck.get(
        "available",
        False
    ):

        # Если security слой отсутствует,
        # НЕ делаем вид, что риск нулевой.
        return min(
            100,
            market_risk + 10
        )

    rug_risk = num(
        rugcheck.get(
            "risk",
            0
        )
    )

    # Market = 55%
    # On-chain/security = 45%

    final = (
        market_risk * 0.55
        + rug_risk * 0.45
    )

    return int(
        max(
            0,
            min(
                100,
                final
            )
        )
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
    liquidity = item["liquidity"]

    # --------------------------------------------------------
    # DUMPING
    # --------------------------------------------------------

    if (
        p5 <= DUMP_5M
        or (
            p1h <= DUMP_1H
            and p5 < 0
        )
    ):

        return "🔻 DUMPING"

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
    # VERY STRONG
    # --------------------------------------------------------

    if (
        score >= VERY_STRONG_SCORE
        and quality >= 50
        and risk < 50
        and p5 > 0
        and p1h > 0
        and liquidity >= 20_000
    ):

        return "🚨 VERY STRONG"

    # --------------------------------------------------------
    # STRONG
    # --------------------------------------------------------

    if (
        score >= STRONG_SCORE
        and quality >= 45
        and risk < 50
        and p5 > 0
        and p1h > 0
        and liquidity >= 20_000
    ):

        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        score >= EARLY_SCORE
        and quality >= 35
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
        and p1h < 60
        and liquidity >= 20_000
    ):

        return "🟢 EARLY"

    # --------------------------------------------------------
    # WATCH
    # --------------------------------------------------------

    if (
        score >= 40
        and p5 > 0
        and p1h > 0
        and risk < 70
    ):

        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ============================================================
# CONFIDENCE
# ============================================================

def calculate_confidence(item):

    momentum = item["score"]
    quality = item["quality"]
    risk = item["risk"]

    confidence = (

        momentum * 0.45
        + quality * 0.35
        + (100 - risk) * 0.20

    )

    # Без security API уменьшаем confidence
    if not item.get(
        "security_available",
        False
    ):

        confidence -= 8

    # Мало сделок = слабее статистика
    if item["total_tx5"] < MIN_TXNS_5M:

        confidence -= 10

    return int(
        max(
            0,
            min(
                100,
                confidence
            )
        )
    )


# ============================================================
# SIGNAL TYPE
# ============================================================

def get_signal_type(item):

    stage = item["stage"]

    if stage in (
        "🔻 DUMPING",
        "🔴 OVEREXTENDED",
        "⚪ LOW MOMENTUM"
    ):

        return None

    score = item["score"]
    delta = item["score_delta"]

    p5 = item["p5"]
    p1h = item["p1h"]

    liquidity = item["liquidity"]
    risk = item["risk"]

    confidence = item["confidence"]

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    if (
        delta >= BREAKOUT_DELTA
        and p5 >= BREAKOUT_MIN_5M
        and p1h > BREAKOUT_MIN_1H
        and liquidity >= BREAKOUT_MIN_LIQUIDITY
        and risk < BREAKOUT_MAX_RISK
        and confidence >= 55
    ):

        return "🚀 BREAKOUT"

    # --------------------------------------------------------
    # VERY STRONG
    # --------------------------------------------------------

    if (
        score >= VERY_STRONG_SCORE
        and confidence >= 65
    ):

        return "🚨 VERY STRONG"

    # --------------------------------------------------------
    # STRONG
    # --------------------------------------------------------

    if (
        score >= STRONG_SCORE
        and confidence >= 60
    ):

        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        score >= EARLY_SCORE
        and confidence >= 55
    ):

        return "🟢 EARLY"

    return None


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

        txns = pair.get(
            "txns",
            {}
        )

        tx5 = txns.get(
            "m5",
            {}
        )

        total_tx5 = (
            integer(
                tx5.get("buys")
            )
            +
            integer(
                tx5.get("sells")
            )
        )

        # ----------------------------------------------------
        # Basic filtering
        # ----------------------------------------------------

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

        score_delta = (
            result["score"]
            - previous_score
        )

        result["name"] = name
        result["symbol"] = symbol
        result["address"] = address

        result["score_delta"] = (
            score_delta
        )

        # ----------------------------------------------------
        # Security
        # ----------------------------------------------------

        rugcheck = get_rugcheck(
            address
        )

        if rugcheck.get(
            "available",
            False
        ):

            security_checked += 1

        result["rugcheck"] = rugcheck

        result["security_available"] = (
            rugcheck.get(
                "available",
                False
            )
        )

        result["security_risk"] = num(
            rugcheck.get(
                "risk",
                0
            )
        )

        result["risk"] = calculate_final_risk(

            result["market_risk"],

            rugcheck

        )

        # ----------------------------------------------------
        # Confidence
        # ----------------------------------------------------

        result["confidence"] = (
            calculate_confidence(
                result
            )
        )

        # ----------------------------------------------------
        # Classification
        # ----------------------------------------------------

        result["stage"] = classify(
            result
        )

        # ----------------------------------------------------
        # History
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

            "risk":
                result["risk"],

            "confidence":
                result["confidence"],

            "p5":
                result["p5"],

            "p1h":
                result["p1h"],

            "liquidity":
                result["liquidity"],

            "v24":
                result["v24"],

        })

        history = history[-50:]

        # ----------------------------------------------------
        # State
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

            "risk":
                result["risk"],

            "confidence":
                result["confidence"],

            "previous_score":
                previous_score,

            "score_delta":
                score_delta,

            "v5":
                result["v5"],

            "tx5":
                result["total_tx5"],

            "liquidity":
                result["liquidity"],

            "price":
                num(
                    pair.get(
                        "priceUsd"
                    )
                ),

            "history":
                history,

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

            "timestamp":
                now_iso,

        }

        results.append(
            result
        )

        # ----------------------------------------------------
        # Small delay to reduce API pressure
        # ----------------------------------------------------

        time.sleep(
            0.05
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
        f"Pairs checked: "
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

    for item in results[:TOP_RESULTS]:

        print(

            f"{item['symbol']} | "

            f"Q {item['quality']}/100 | "

            f"M {item['score']}/100 | "

            f"C {item['confidence']}/100 | "

            f"Δ {item['score_delta']:+.0f} | "

            f"{item['stage']} | "

            f"5m {item['p5']:+.1f}% | "

            f"1h {item['p1h']:+.1f}% | "

            f"liq {money(item['liquidity'])} | "

            f"risk {item['risk']}/100"

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

            now_ts - last_alert
            >= ALERT_COOLDOWN

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

        signal_items.append(
            item
        )

        new_state[
            item["address"]
        ]["last_alert"] = (
            now_ts
        )

        new_state[
            item["address"]
        ]["last_alert_score"] = (
            item["score"]
        )

        new_state[
            item["address"]
        ]["last_alert_type"] = (
            signal_type
        )

        item["signal_type"] = (
            signal_type
        )

    # ========================================================
    # SAVE STATE
    # ========================================================

    save_json(
        STATE_FILE,
        new_state
    )

    # ========================================================
    # SAVE SIGNAL HISTORY
    # ========================================================

    for item in signal_items:

        signals.append({

            "timestamp":
                now_iso,

            "address":
                item["address"],

            "symbol":
                item["symbol"],

            "score":
                item["score"],

            "quality":
                item["quality"],

            "risk":
                item["risk"],

            "confidence":
                item["confidence"],

            "score_delta":
                item["score_delta"],

            "stage":
                item["signal_type"],

            "p5":
                item["p5"],

            "p1h":
                item["p1h"],

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
# TELEGRAM FORMAT
# ============================================================

def risk_label(risk):

    if risk >= 70:
        return "🔴 HIGH"

    if risk >= 40:
        return "🟡 MEDIUM"

    return "🟢 LOW"


def security_label(item):

    if not item.get(
        "security_available",
        False
    ):

        return (
            "⚪ unavailable"
        )

    risk = item.get(
        "security_risk",
        0
    )

    if risk >= 70:

        return "🔴 HIGH"

    if risk >= 40:

        return "🟡 MEDIUM"

    return "🟢 LOW"


def format_signal(item):

    reasons = ", ".join(

        item["reasons"][:5]

    )

    risk_reasons = ", ".join(

        item["risk_reasons"][:4]

    )

    if not risk_reasons:

        risk_reasons = (
            "критических рыночных "
            "признаков не обнаружено"
        )

    address = item["address"]

    dex_url = (
        "https://dexscreener.com/"
        "solana/"
        + address
    )

    solscan_url = (
        "https://solscan.io/token/"
        + address
    )

    security_status = (
        security_label(item)
    )

    return (

        f"{item['signal_type']}\n\n"

        f"🚀 {item['name']} "
        f"({item['symbol']})\n\n"

        f"⭐ Quality: "
        f"{item['quality']}/100\n"

        f"⚡ Momentum: "
        f"{item['score']}/100\n"

        f"🎯 Confidence: "
        f"{item['confidence']}/100\n"

        f"📊 Score Δ: "
        f"{item['score_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n"

        f"📈 6h: "
        f"{item['p6h']:+.1f}%\n\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n\n"

        f"⚡ Volume x: "
        f"{item['volume_acceleration']:.1f}\n"

        f"🔄 TX x: "
        f"{item['tx_acceleration']:.1f}\n\n"

        f"🟢 Buys 5m: "
        f"{item['buys5']}\n"

        f"🔴 Sells 5m: "
        f"{item['sells5']}\n"

        f"⚖️ Buy ratio: "
        f"{item['buy_ratio'] * 100:.0f}%\n\n"

        f"🛡 Market risk: "
        f"{item['market_risk']}/100\n"

        f"🔐 Security risk: "
        f"{item['security_risk']}/100 "
        f"({security_status})\n"

        f"⚠️ Final risk: "
        f"{item['risk']}/100 "
        f"({risk_label(item['risk'])})\n\n"

        f"🧠 Momentum:\n"
        f"{reasons}\n\n"

        f"⚠️ Risk:\n"
        f"{risk_reasons}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n"

        f"🔐 [Solscan]"
        f"({solscan_url})\n\n"

        f"📋 `{address}`\n\n"

        f"⚠️ Алгоритмический сигнал. "
        f"Он не гарантирует рост и "
        f"не является рекомендацией "
        f"покупать токен."

    )


# ============================================================
# TELEGRAM
# ============================================================

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
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "========================================\n"
        "   SOLANA MOMENTUM SCANNER v3\n"
        "========================================\n"
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

        time.sleep(
            0.5
        )


if __name__ == "__main__":

    main()
