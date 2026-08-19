import os
import json
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

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

# ------------------------------------------------------------
# SIGNAL SCORES
# ------------------------------------------------------------

EARLY_SCORE = 65
STRONG_SCORE = 75
VERY_STRONG_SCORE = 85

# ------------------------------------------------------------
# BREAKOUT
# ------------------------------------------------------------

BREAKOUT_DELTA = 25
BREAKOUT_MIN_5M = 5
BREAKOUT_MIN_1H = 0
BREAKOUT_MIN_LIQUIDITY = 20_000

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
# RUG / RISK
# ------------------------------------------------------------

MAX_SIGNAL_RISK = 50
MAX_BREAKOUT_RISK = 40
MAX_RUG_RISK = 55

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


def money(x):

    x = num(x)

    if x >= 1_000_000:
        return f"${x / 1_000_000:.2f}M"

    if x >= 1_000:
        return f"${x / 1_000:.1f}K"

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

    except Exception:

        return default


def save_json(filename, data):

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


# ============================================================
# TOKEN ADDRESSES
# ============================================================

def get_addresses():

    addresses = set()

    for endpoint in ENDPOINTS:

        try:

            response = requests.get(
                endpoint,
                timeout=15
            )

            print(
                f"{endpoint} -> "
                f"{response.status_code}"
            )

            if response.status_code != 200:
                continue

            data = response.json()

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

        except Exception as e:

            print(
                "SOURCE ERROR:",
                e
            )

    return list(addresses)


# ============================================================
# BEST PAIR
# ============================================================

def get_pair(address):

    try:

        url = (
            "https://api.dexscreener.com/"
            f"token-pairs/v1/solana/{address}"
        )

        response = requests.get(
            url,
            timeout=10
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not isinstance(data, list):
            return None

        if not data:
            return None

        data.sort(
            key=lambda p: num(
                p.get(
                    "liquidity",
                    {}
                ).get("usd")
            ),
            reverse=True
        )

        return data[0]

    except Exception as e:

        print(
            "PAIR ERROR:",
            address,
            e
        )

        return None


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
    pair_age_hours
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

        risk += 20

        reasons.append(
            "низкая ликвидность"
        )

    elif liquidity < 30_000:

        risk += 10

        reasons.append(
            "небольшая ликвидность"
        )

    # --------------------------------------------------------
    # PAIR AGE
    # --------------------------------------------------------

    if 0 < pair_age_hours < 1:

        risk += 20

        reasons.append(
            "пара создана недавно"
        )

    elif 1 <= pair_age_hours < 3:

        risk += 12

        reasons.append(
            "очень молодая пара"
        )

    elif 3 <= pair_age_hours < 12:

        risk += 5

        reasons.append(
            "молодая пара"
        )

    # --------------------------------------------------------
    # EXTREME GROWTH
    # --------------------------------------------------------

    if p1h >= 200:

        risk += 30

        reasons.append(
            "экстремальный рост 1ч"
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
    # 5M PUMP
    # --------------------------------------------------------

    if p5 >= 40:

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

    if p5 <= DUMP_5M:

        risk += 20

        reasons.append(
            "резкое падение 5м"
        )

    if p1h <= DUMP_1H:

        risk += 20

        reasons.append(
            "негативный тренд"
        )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

    if turnover >= 50:

        risk += 15

        reasons.append(
            "аномальный оборот"
        )

    elif turnover >= 30:

        risk += 8

        reasons.append(
            "очень высокий оборот"
        )

    # --------------------------------------------------------
    # BUY / SELL IMBALANCE
    # --------------------------------------------------------

    total = buys + sells

    if total >= 30:

        ratio = buys / total

        if ratio < 0.30:

            risk += 20

            reasons.append(
                "сильное давление продавцов"
            )

        elif ratio > 0.90:

            risk += 10

            reasons.append(
                "аномальный перевес покупок"
            )

    # --------------------------------------------------------
    # VOLUME VS LIQUIDITY
    # --------------------------------------------------------

    if liquidity > 0:

        volume_liq = v24 / liquidity

        if volume_liq >= 100:

            risk += 20

            reasons.append(
                "аномальное соотношение объёма"
            )

        elif volume_liq >= 50:

            risk += 10

            reasons.append(
                "очень высокий оборот относительно ликвидности"
            )

    risk = min(
        100,
        risk
    )

    return risk, reasons


# ============================================================
# RUG RISK
# ============================================================

def calculate_rug_risk(
    pair,
    risk,
    p5,
    p1h,
    liquidity,
    v24,
    buys,
    sells,
    turnover,
    pair_age_hours
):

    rug = 0
    reasons = []

    # --------------------------------------------------------
    # VERY NEW PAIR
    # --------------------------------------------------------

    if 0 < pair_age_hours < 1:

        rug += 20

        reasons.append(
            "очень новая пара"
        )

    elif 1 <= pair_age_hours < 3:

        rug += 10

        reasons.append(
            "молодая пара"
        )

    # --------------------------------------------------------
    # LOW LIQUIDITY
    # --------------------------------------------------------

    if liquidity < 15_000:

        rug += 20

        reasons.append(
            "малая ликвидность"
        )

    elif liquidity < 25_000:

        rug += 10

        reasons.append(
            "ликвидность ниже желательной"
        )

    # --------------------------------------------------------
    # EXTREME PRICE ACTION
    # --------------------------------------------------------

    if p1h >= 300:

        rug += 25

        reasons.append(
            "экстремальный рост"
        )

    elif p1h >= 200:

        rug += 15

        reasons.append(
            "очень сильный рост"
        )

    # --------------------------------------------------------
    # FAST PUMP
    # --------------------------------------------------------

    if p5 >= 50:

        rug += 20

        reasons.append(
            "экстремальный памп 5м"
        )

    elif p5 >= 30:

        rug += 10

        reasons.append(
            "сильный памп 5м"
        )

    # --------------------------------------------------------
    # DUMP
    # --------------------------------------------------------

    if p5 <= -20:

        rug += 20

        reasons.append(
            "резкий дамп"
        )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

    if turnover >= 100:

        rug += 20

        reasons.append(
            "экстремальный turnover"
        )

    elif turnover >= 50:

        rug += 10

        reasons.append(
            "высокий turnover"
        )

    # --------------------------------------------------------
    # SELL PRESSURE
    # --------------------------------------------------------

    total = buys + sells

    if total >= 30:

        ratio = buys / total

        if ratio < 0.25:

            rug += 20

            reasons.append(
                "очень много продаж"
            )

    # --------------------------------------------------------
    # COMBINATION
    # --------------------------------------------------------

    if (
        risk >= 60
        and p1h >= 100
        and liquidity < 30_000
    ):

        rug += 15

        reasons.append(
            "опасная комбинация факторов"
        )

    rug = min(
        100,
        rug
    )

    return rug, reasons


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

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    p5 = num(
        change.get("m5")
    )

    p1h = num(
        change.get("h1")
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

    buys5 = int(
        tx5.get(
            "buys",
            0
        ) or 0
    )

    sells5 = int(
        tx5.get(
            "sells",
            0
        ) or 0
    )

    total_tx = (
        buys5 + sells5
    )

    buy_ratio = 0

    if total_tx > 0:

        buy_ratio = (
            buys5 / total_tx
        )

    # ========================================================
    # PAIR AGE
    # ========================================================

    pair_created = num(
        pair.get(
            "pairCreatedAt"
        )
    )

    pair_age_hours = 0

    if pair_created > 0:

        # DexScreener timestamp is milliseconds
        created_seconds = (
            pair_created / 1000
        )

        age_seconds = (
            datetime.now(
                timezone.utc
            ).timestamp()
            - created_seconds
        )

        pair_age_hours = max(
            0,
            age_seconds / 3600
        )

    # ========================================================
    # MOMENTUM SCORE
    # ========================================================

    score = 0
    reasons = []

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    if 2 <= p5 < 5:

        score += 15

        reasons.append(
            "начинается движение"
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

    elif p5 <= -15:

        score -= 30

        reasons.append(
            "сильное падение 5м"
        )

    elif p5 < -5:

        score -= 15

        reasons.append(
            "падение 5м"
        )

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    if 3 <= p1h < 15:

        score += 20

        reasons.append(
            "ранний тренд 1ч"
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

    elif p1h >= 100:

        score -= 25

        reasons.append(
            "токен уже сильно вырос"
        )

    elif p1h <= -30:

        score -= 25

        reasons.append(
            "негативный тренд 1ч"
        )

    # --------------------------------------------------------
    # LIQUIDITY
    # --------------------------------------------------------

    if liquidity >= 100_000:

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

    # --------------------------------------------------------
    # BUY / SELL
    # --------------------------------------------------------

    if total_tx >= 10:

        if buy_ratio >= 0.75:

            score += 20

            reasons.append(
                "сильное давление покупателей"
            )

        elif buy_ratio >= 0.65:

            score += 15

            reasons.append(
                "покупатели доминируют"
            )

        elif buy_ratio >= 0.55:

            score += 7

            reasons.append(
                "покупателей немного больше"
            )

        elif buy_ratio <= 0.40:

            score -= 15

            reasons.append(
                "продавцы доминируют"
            )

    # --------------------------------------------------------
    # VOLUME ACCELERATION
    # --------------------------------------------------------

    old_v5 = num(
        old.get("v5")
    )

    volume_acceleration = 1.0

    if old_v5 > 0 and v5 > 0:

        volume_acceleration = (
            v5 / old_v5
        )

        if volume_acceleration >= 3:

            score += 20

            reasons.append(
                f"объём ускорился "
                f"{volume_acceleration:.1f}x"
            )

        elif volume_acceleration >= 2:

            score += 15

            reasons.append(
                f"объём ускоряется "
                f"{volume_acceleration:.1f}x"
            )

        elif volume_acceleration >= 1.5:

            score += 10

            reasons.append(
                f"объём растёт "
                f"{volume_acceleration:.1f}x"
            )

        elif volume_acceleration >= 1.2:

            score += 5

            reasons.append(
                f"объём растёт "
                f"{volume_acceleration:.1f}x"
            )

    # --------------------------------------------------------
    # TX ACCELERATION
    # --------------------------------------------------------

    old_tx = int(
        old.get(
            "tx5",
            0
        ) or 0
    )

    tx_acceleration = 1.0

    if old_tx > 0 and total_tx > 0:

        tx_acceleration = (
            total_tx / old_tx
        )

        if tx_acceleration >= 2:

            score += 10

            reasons.append(
                "сделок стало значительно больше"
            )

        elif tx_acceleration >= 1.5:

            score += 5

            reasons.append(
                "количество сделок растёт"
            )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

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
    # FINAL MOMENTUM
    # ========================================================

    score = max(
        0,
        min(
            100,
            score
        )
    )

    # ========================================================
    # QUALITY
    # ========================================================

    quality = 0

    if liquidity >= 100_000:

        quality += 30

    elif liquidity >= 50_000:

        quality += 25

    elif liquidity >= 30_000:

        quality += 20

    elif liquidity >= 20_000:

        quality += 12

    # --------------------------------------------------------

    if v24 >= 1_000_000:

        quality += 25

    elif v24 >= 500_000:

        quality += 20

    elif v24 >= 100_000:

        quality += 15

    elif v24 >= 50_000:

        quality += 10

    # --------------------------------------------------------

    if total_tx >= 1000:

        quality += 20

    elif total_tx >= 500:

        quality += 15

    elif total_tx >= 100:

        quality += 10

    # --------------------------------------------------------

    if 0.40 <= buy_ratio <= 0.80:

        quality += 15

    # --------------------------------------------------------

    if 0 < p1h < 100:

        quality += 10

    quality = min(
        100,
        quality
    )

    # ========================================================
    # RISK
    # ========================================================

    risk, risk_reasons = calculate_risk(
        p5,
        p1h,
        liquidity,
        v24,
        buys5,
        sells5,
        turnover,
        pair_age_hours
    )

    # ========================================================
    # RUG RISK
    # ========================================================

    rug_risk, rug_reasons = calculate_rug_risk(
        pair,
        risk,
        p5,
        p1h,
        liquidity,
        v24,
        buys5,
        sells5,
        turnover,
        pair_age_hours
    )

    return {

        "score":
            score,

        "quality":
            quality,

        "risk":
            risk,

        "rug_risk":
            rug_risk,

        "p5":
            p5,

        "p1h":
            p1h,

        "v5":
            v5,

        "v1h":
            v1h,

        "v24":
            v24,

        "liquidity":
            liquidity,

        "buys5":
            buys5,

        "sells5":
            sells5,

        "buy_ratio":
            buy_ratio,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "turnover":
            turnover,

        "pair_age_hours":
            pair_age_hours,

        "reasons":
            reasons,

        "risk_reasons":
            risk_reasons,

        "rug_reasons":
            rug_reasons,
    }


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(item):

    p5 = item["p5"]
    p1h = item["p1h"]

    score = item["score"]
    quality = item["quality"]

    risk = item["risk"]
    rug_risk = item["rug_risk"]

    liquidity = item["liquidity"]

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
    # SUSPICIOUS
    # --------------------------------------------------------

    if rug_risk >= MAX_RUG_RISK:

        return "⚠️ SUSPICIOUS"

    # --------------------------------------------------------
    # VERY STRONG
    # --------------------------------------------------------

    if (
        score >= 85
        and quality >= 50
        and risk < MAX_SIGNAL_RISK
        and rug_risk < MAX_RUG_RISK
        and p5 > 0
        and p1h > 0
        and liquidity >= 20_000
    ):

        return "🚨 VERY STRONG"

    # --------------------------------------------------------
    # STRONG
    # --------------------------------------------------------

    if (
        score >= 75
        and quality >= 45
        and risk < MAX_SIGNAL_RISK
        and rug_risk < MAX_RUG_RISK
        and p5 > 0
        and p1h > 0
        and liquidity >= 20_000
    ):

        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        score >= 65
        and quality >= 35
        and risk < MAX_SIGNAL_RISK
        and rug_risk < MAX_RUG_RISK
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
        and rug_risk < 70
    ):

        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ============================================================
# SIGNAL TYPE
# ============================================================

def get_signal_type(item):

    stage = item["stage"]

    if stage in (
        "🔻 DUMPING",
        "🔴 OVEREXTENDED",
        "⚠️ SUSPICIOUS",
        "⚪ LOW MOMENTUM"
    ):

        return None

    score = item["score"]
    delta = item["score_delta"]

    p5 = item["p5"]
    p1h = item["p1h"]

    liquidity = item["liquidity"]

    risk = item["risk"]
    rug_risk = item["rug_risk"]

    # ========================================================
    # BREAKOUT
    # ========================================================

    if (
        delta >= BREAKOUT_DELTA
        and p5 >= BREAKOUT_MIN_5M
        and p1h > BREAKOUT_MIN_1H
        and liquidity >= BREAKOUT_MIN_LIQUIDITY
        and risk < MAX_BREAKOUT_RISK
        and rug_risk < MAX_RUG_RISK
        and score >= 65
    ):

        return "🚀 BREAKOUT"

    # ========================================================
    # VERY STRONG
    # ========================================================

    if (
        score >= VERY_STRONG_SCORE
        and risk < MAX_SIGNAL_RISK
        and rug_risk < MAX_RUG_RISK
    ):

        return "🚨 VERY STRONG"

    # ========================================================
    # STRONG
    # ========================================================

    if (
        score >= STRONG_SCORE
        and risk < MAX_SIGNAL_RISK
        and rug_risk < MAX_RUG_RISK
    ):

        return "🔥 STRONG"

    # ========================================================
    # EARLY
    # ========================================================

    if (
        score >= EARLY_SCORE
        and risk < MAX_SIGNAL_RISK
        and rug_risk < MAX_RUG_RISK
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
        result["score_delta"] = score_delta

        result["stage"] = classify(
            result
        )

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

            "risk":
                result["risk"],

            "rug_risk":
                result["rug_risk"],

            "p5":
                result["p5"],

            "p1h":
                result["p1h"],

        })

        history = history[-40:]

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

            "risk":
                result["risk"],

            "rug_risk":
                result["rug_risk"],

            "previous_score":
                previous_score,

            "score_delta":
                score_delta,

            "v5":
                result["v5"],

            "tx5":
                result["buys5"]
                + result["sells5"],

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

    # ========================================================
    # SORT
    # ========================================================

    results.sort(
        key=lambda x: (
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
            f"Δ {item['score_delta']:+.0f} | "
            f"{item['stage']} | "
            f"5m {item['p5']:+.1f}% | "
            f"1h {item['p1h']:+.1f}% | "
            f"liq {money(item['liquidity'])} | "
            f"risk {item['risk']}/100 | "
            f"rug {item['rug_risk']}/100"
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
        ]["last_alert"] = now_ts

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

            "rug_risk":
                item["rug_risk"],

            "score_delta":
                item["score_delta"],

            "stage":
                item["signal_type"],

            "p5":
                item["p5"],

            "p1h":
                item["p1h"],

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
# TELEGRAM FORMAT
# ============================================================

def format_signal(item):

    reasons = ", ".join(
        item["reasons"][:6]
    )

    risk_text = "🟢 LOW"

    if item["risk"] >= 70:

        risk_text = "🔴 HIGH"

    elif item["risk"] >= 40:

        risk_text = "🟡 MEDIUM"

    rug_text = "🟢 LOW"

    if item["rug_risk"] >= 70:

        rug_text = "🔴 HIGH"

    elif item["rug_risk"] >= 40:

        rug_text = "🟡 MEDIUM"

    address = item["address"]

    dex_url = (
        "https://dexscreener.com/solana/"
        + address
    )

    return (

        f"{item['signal_type']}\n\n"

        f"🚀 {item['name']} "
        f"({item['symbol']})\n\n"

        f"⭐ Quality: "
        f"{item['quality']}/100\n"

        f"⚡ Momentum: "
        f"{item['score']}/100\n"

        f"📊 Score change: "
        f"{item['score_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n"

        f"⚡ Volume acceleration: "
        f"{item['volume_acceleration']:.1f}x\n"

        f"🔄 TX acceleration: "
        f"{item['tx_acceleration']:.1f}x\n\n"

        f"🟢 Buys 5m: "
        f"{item['buys5']}\n"

        f"🔴 Sells 5m: "
        f"{item['sells5']}\n\n"

        f"⚠️ Risk: "
        f"{item['risk']}/100 "
        f"({risk_text})\n"

        f"🛡️ Rug Risk: "
        f"{item['rug_risk']}/100 "
        f"({rug_text})\n\n"

        f"🕐 Pair age: "
        f"{item['pair_age_hours']:.1f}h\n\n"

        f"🧠 {reasons}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n\n"

        f"📋 `{address}`\n\n"

        f"⚠️ Это алгоритмический сигнал, "
        f"а не гарантия роста."
    )


# ============================================================
# TELEGRAM
# ============================================================

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

    except Exception as e:

        print(
            "TELEGRAM ERROR:",
            e
        )


# ============================================================
# MAIN
# ============================================================

def main():

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
