import os
import json
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# SOLANA MOMENTUM SCANNER v5
# ============================================================

print("""
========================================
   SOLANA MOMENTUM SCANNER v5.0
========================================
""")


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"
SECURITY_FILE = "security_cache.json"

TOP_RESULTS = 15

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

# Не анализируем слишком маленькие токены
MIN_SECURITY_LIQUIDITY = 15_000

# Сколько одновременно запрашивать DexScreener
MAX_WORKERS = 8

# ============================================================
# SIGNAL THRESHOLDS
# ============================================================

EARLY_SCORE = 65
STRONG_SCORE = 75
VERY_STRONG_SCORE = 85

BREAKOUT_DELTA = 22
BREAKOUT_MIN_5M = 4
BREAKOUT_MIN_1H = 0

# Минимальное качество
EARLY_MIN_QUALITY = 45
STRONG_MIN_QUALITY = 50
VERY_STRONG_MIN_QUALITY = 60

# Максимальный риск
MAX_SIGNAL_RISK = 45

# ============================================================
# RE-ALERT
# ============================================================

ALERT_COOLDOWN = 30 * 60
RE_ALERT_SCORE_INCREASE = 12

# ============================================================
# DUMP
# ============================================================

DUMP_5M = -12
DUMP_1H = -20

# ============================================================
# OVEREXTENDED
# ============================================================

OVEREXTENDED_1H = 150
OVEREXTENDED_1H_WITH_MOMENTUM = 100

# ============================================================
# SECURITY
# ============================================================

SECURITY_CACHE_TTL = 15 * 60

# Если security API недоступен:
# confidence ограничивается, но токен не обязан
# полностью исчезать из TOP.
SECURITY_UNKNOWN_CONFIDENCE_CAP = 55

# Для сильного сигнала security должна быть известна
REQUIRE_SECURITY_FOR_SIGNAL = True

# ============================================================
# BLOCKED
# ============================================================

BLOCKED = {
    "SOL",
    "WSOL",
    "USDC",
    "USDT",
    "USD1",
    "USDE",
}

# ============================================================
# ENDPOINTS
# ============================================================

DEX_ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
]

RUGCHECK_URL = (
    "https://api.rugcheck.xyz/v1/tokens/"
    "{}/report/summary"
)


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "SolanaMomentumScanner/5.0"
})


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

    temp = filename + ".tmp"

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

    os.replace(temp, filename)


def clamp(value, low=0, high=100):
    return max(low, min(high, value))


# ============================================================
# GET TOKEN ADDRESSES
# ============================================================

def get_addresses():

    addresses = set()

    for endpoint in DEX_ENDPOINTS:

        try:

            response = SESSION.get(
                endpoint,
                timeout=15
            )

            status = (
                "OK"
                if response.status_code == 200
                else str(response.status_code)
            )

            print(
                f"{endpoint} -> {status}"
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
                endpoint,
                e
            )

    return list(addresses)


# ============================================================
# GET BEST PAIR
# ============================================================

def get_pair(address):

    try:

        url = (
            "https://api.dexscreener.com/"
            f"token-pairs/v1/solana/{address}"
        )

        response = SESSION.get(
            url,
            timeout=12
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if not isinstance(data, list):
            return None

        if not data:
            return None

        # Лучший pair = не просто максимальная liquidity.
        # Предпочитаем достаточно ликвидный pair.
        data.sort(
            key=lambda p: (
                num(
                    p.get(
                        "liquidity",
                        {}
                    ).get("usd")
                ),
                num(
                    p.get(
                        "volume",
                        {}
                    ).get("h24")
                )
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
# SECURITY
# ============================================================

def extract_security(data):

    """
    RugCheck API может менять структуру ответа.
    Поэтому здесь не предполагается одна жёсткая схема.
    """

    if not isinstance(data, dict):

        return {
            "known": False,
            "score": None,
            "risk": 50,
            "reasons": [
                "security data unavailable"
            ]
        }

    risks = data.get("risks", [])

    if not isinstance(risks, list):
        risks = []

    risk_names = []

    for r in risks:

        if isinstance(r, dict):

            name = (
                r.get("name")
                or r.get("description")
                or r.get("level")
            )

            if name:
                risk_names.append(
                    str(name)
                )

        elif isinstance(r, str):

            risk_names.append(r)

    # Попытка получить score
    raw_score = data.get("score")

    if raw_score is None:
        raw_score = data.get("riskScore")

    score = None

    if raw_score is not None:
        try:
            score = float(raw_score)
        except Exception:
            score = None

    # RugCheck score может быть интерпретирован
    # по-разному между версиями API.
    # Поэтому используем risks как основной источник.
    risk = 0

    for r in risks:

        if not isinstance(r, dict):
            continue

        level = str(
            r.get("level", "")
        ).lower()

        if level in ("danger", "critical"):
            risk += 30

        elif level in ("high",):
            risk += 20

        elif level in ("warn", "warning", "medium"):
            risk += 10

    # Authorities
    token_meta = data.get(
        "tokenMeta",
        {}
    )

    if not isinstance(token_meta, dict):
        token_meta = {}

    mint_authority = (
        data.get("mintAuthority")
        or token_meta.get("mintAuthority")
    )

    freeze_authority = (
        data.get("freezeAuthority")
        or token_meta.get("freezeAuthority")
    )

    if mint_authority:
        risk += 20
        risk_names.append(
            "mint authority active"
        )

    if freeze_authority:
        risk += 20
        risk_names.append(
            "freeze authority active"
        )

    risk = clamp(risk)

    return {
        "known": True,
        "score": score,
        "risk": risk,
        "reasons": risk_names[:8],
        "mint_authority": bool(
            mint_authority
        ),
        "freeze_authority": bool(
            freeze_authority
        )
    }


def get_security(address, cache):

    now = time.time()

    old = cache.get(address)

    if old:

        timestamp = num(
            old.get("timestamp")
        )

        if (
            now - timestamp
            < SECURITY_CACHE_TTL
        ):

            return old

    try:

        url = RUGCHECK_URL.format(
            address
        )

        response = SESSION.get(
            url,
            timeout=12
        )

        if response.status_code != 200:

            return {
                "known": False,
                "score": None,
                "risk": 50,
                "reasons": [
                    "security API unavailable"
                ],
                "timestamp": now
            }

        data = response.json()

        result = extract_security(
            data
        )

        result["timestamp"] = now

        return result

    except Exception as e:

        return {
            "known": False,
            "score": None,
            "risk": 50,
            "reasons": [
                "security check failed"
            ],
            "timestamp": now
        }


# ============================================================
# SECURITY BATCH
# ============================================================

def security_scan(addresses, cache):

    results = {}

    if not addresses:
        return results

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        jobs = {
            executor.submit(
                get_security,
                address,
                cache
            ): address

            for address in addresses
        }

        for future in as_completed(jobs):

            address = jobs[future]

            try:

                results[address] = (
                    future.result()
                )

            except Exception:

                results[address] = {
                    "known": False,
                    "score": None,
                    "risk": 50,
                    "reasons": [
                        "security error"
                    ],
                    "timestamp": time.time()
                }

    return results


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
    security
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
    # OVEREXTENSION
    # --------------------------------------------------------

    if p1h >= 200:

        risk += 35

        reasons.append(
            "экстремальный рост 1ч"
        )

    elif p1h >= 150:

        risk += 25

        reasons.append(
            "сильная перегретость"
        )

    elif p1h >= 100:

        risk += 18

        reasons.append(
            "токен сильно разогнан"
        )

    elif p1h >= 80:

        risk += 10

        reasons.append(
            "сильный рост 1ч"
        )

    # --------------------------------------------------------
    # SHORT-TERM PUMP
    # --------------------------------------------------------

    if p5 >= 50:

        risk += 30

        reasons.append(
            "экстремальный памп 5м"
        )

    elif p5 >= 30:

        risk += 20

        reasons.append(
            "сильный памп 5м"
        )

    elif p5 >= 20:

        risk += 10

        reasons.append(
            "быстрый рост 5м"
        )

    # --------------------------------------------------------
    # DUMP
    # --------------------------------------------------------

    if p5 <= -20:

        risk += 30

        reasons.append(
            "экстремальный дамп 5м"
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
            "негативный тренд 1ч"
        )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

    if turnover > 50:

        risk += 20

        reasons.append(
            "аномальный оборот"
        )

    elif turnover > 30:

        risk += 10

        reasons.append(
            "очень высокий оборот"
        )

    # --------------------------------------------------------
    # BUY/SELL
    # --------------------------------------------------------

    total = buys + sells

    if total >= 20:

        ratio = buys / total

        if ratio < 0.30:

            risk += 20

            reasons.append(
                "сильное давление продавцов"
            )

        elif ratio < 0.40:

            risk += 10

            reasons.append(
                "продавцы доминируют"
            )

        elif ratio > 0.92:

            risk += 8

            reasons.append(
                "аномальный перевес покупателей"
            )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    if not security.get("known", False):

        risk += 20

        reasons.append(
            "security не подтверждена"
        )

    else:

        security_risk = num(
            security.get("risk")
        )

        if security_risk >= 50:

            risk += 35

            reasons.append(
                "высокий security risk"
            )

        elif security_risk >= 30:

            risk += 20

            reasons.append(
                "повышенный security risk"
            )

        elif security_risk >= 15:

            risk += 10

            reasons.append(
                "есть security warnings"
            )

    return clamp(risk), reasons


# ============================================================
# MOMENTUM
# ============================================================

def calculate_momentum(
    p5,
    p1h,
    buy_ratio,
    total_tx,
    volume_acceleration,
    tx_acceleration,
    turnover
):

    score = 0
    reasons = []

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    if 2 <= p5 < 5:

        score += 12

        reasons.append(
            "начинается движение"
        )

    elif 5 <= p5 < 10:

        score += 22

        reasons.append(
            "хороший импульс 5м"
        )

    elif 10 <= p5 < 18:

        score += 20

        reasons.append(
            "сильный импульс 5м"
        )

    elif 18 <= p5 < 25:

        score += 10

        reasons.append(
            "быстрый рост 5м"
        )

    elif 25 <= p5 < 40:

        score += 2

        reasons.append(
            "токен уже разгоняется"
        )

    elif p5 >= 40:

        score -= 15

        reasons.append(
            "слишком резкий памп"
        )

    elif -5 < p5 < 2:

        score += 2

    elif p5 <= -5:

        score -= 20

        reasons.append(
            "негативный импульс 5м"
        )

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    if 3 <= p1h < 15:

        score += 18

        reasons.append(
            "ранний тренд 1ч"
        )

    elif 15 <= p1h < 30:

        score += 25

        reasons.append(
            "здоровый тренд 1ч"
        )

    elif 30 <= p1h < 50:

        score += 18

        reasons.append(
            "сильный тренд 1ч"
        )

    elif 50 <= p1h < 80:

        score += 8

        reasons.append(
            "токен уже сильно вырос"
        )

    elif 80 <= p1h < 120:

        score -= 5

        reasons.append(
            "высокий рост 1ч"
        )

    elif 120 <= p1h < 200:

        score -= 20

        reasons.append(
            "перегретый тренд"
        )

    elif p1h >= 200:

        score -= 35

        reasons.append(
            "экстремальная перегретость"
        )

    elif p1h <= -20:

        score -= 25

        reasons.append(
            "сильный нисходящий тренд"
        )

    # --------------------------------------------------------
    # BUY PRESSURE
    # --------------------------------------------------------

    if total_tx >= 10:

        if buy_ratio >= 0.75:

            score += 18

            reasons.append(
                "покупатели сильно доминируют"
            )

        elif buy_ratio >= 0.65:

            score += 13

            reasons.append(
                "покупатели доминируют"
            )

        elif buy_ratio >= 0.55:

            score += 6

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

    if volume_acceleration >= 3:

        score += 18

        reasons.append(
            f"объём ускорился {volume_acceleration:.1f}x"
        )

    elif volume_acceleration >= 2:

        score += 13

        reasons.append(
            f"объём ускоряется {volume_acceleration:.1f}x"
        )

    elif volume_acceleration >= 1.5:

        score += 8

        reasons.append(
            f"объём растёт {volume_acceleration:.1f}x"
        )

    elif volume_acceleration >= 1.2:

        score += 4

    # --------------------------------------------------------
    # TX
    # --------------------------------------------------------

    if tx_acceleration >= 2:

        score += 10

        reasons.append(
            "число сделок резко растёт"
        )

    elif tx_acceleration >= 1.5:

        score += 5

        reasons.append(
            "число сделок растёт"
        )

    # --------------------------------------------------------
    # TURNOVER
    # --------------------------------------------------------

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

    return clamp(score), reasons


# ============================================================
# QUALITY
# ============================================================

def calculate_quality(
    liquidity,
    v24,
    total_tx,
    buy_ratio,
    p1h
):

    score = 0
    reasons = []

    # Liquidity
    if liquidity >= 250_000:

        score += 30

    elif liquidity >= 100_000:

        score += 27

    elif liquidity >= 50_000:

        score += 23

    elif liquidity >= 30_000:

        score += 19

    elif liquidity >= 20_000:

        score += 12

    else:

        score += 5

    # Volume
    if v24 >= 2_000_000:

        score += 25

    elif v24 >= 1_000_000:

        score += 22

    elif v24 >= 500_000:

        score += 19

    elif v24 >= 100_000:

        score += 15

    elif v24 >= 50_000:

        score += 10

    # Transactions
    if total_tx >= 1000:

        score += 20

    elif total_tx >= 500:

        score += 16

    elif total_tx >= 200:

        score += 13

    elif total_tx >= 100:

        score += 10

    elif total_tx >= 50:

        score += 5

    # Balanced enough order flow
    if 0.40 <= buy_ratio <= 0.80:

        score += 15

    elif buy_ratio > 0.90:

        score -= 5

    # Healthy trend
    if 0 < p1h < 100:

        score += 10

    elif p1h >= 150:

        score -= 10

    return clamp(score)


# ============================================================
# ANALYSE
# ============================================================

def analyse(pair, old, security):

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

    buy_ratio = (
        buys5 / total_tx
        if total_tx > 0
        else 0
    )

    old_v5 = num(
        old.get("v5")
    )

    old_tx = integer(
        old.get("tx5")
    )

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

    turnover = (
        v24 / liquidity
        if liquidity > 0
        else 0
    )

    momentum, momentum_reasons = (
        calculate_momentum(
            p5,
            p1h,
            buy_ratio,
            total_tx,
            volume_acceleration,
            tx_acceleration,
            turnover
        )
    )

    quality = calculate_quality(
        liquidity,
        v24,
        total_tx,
        buy_ratio,
        p1h
    )

    risk, risk_reasons = calculate_risk(
        p5,
        p1h,
        liquidity,
        v24,
        buys5,
        sells5,
        turnover,
        security
    )

    # --------------------------------------------------------
    # SECURITY SCORE
    # --------------------------------------------------------

    if security.get("known"):

        security_risk = num(
            security.get("risk")
        )

        security_score = (
            100 - security_risk
        )

    else:

        security_score = 40

    # --------------------------------------------------------
    # CONFIDENCE
    # --------------------------------------------------------

    confidence = (
        momentum * 0.40
        + quality * 0.25
        + security_score * 0.20
        + (100 - risk) * 0.15
    )

    # Unknown security => hard cap
    if not security.get("known"):

        confidence = min(
            confidence,
            SECURITY_UNKNOWN_CONFIDENCE_CAP
        )

    confidence = round(
        clamp(confidence),
        2
    )

    previous_score = num(
        old.get("momentum", 0)
    )

    momentum_delta = (
        momentum - previous_score
    )

    return {

        "momentum": momentum,

        "quality": quality,

        "security_score":
            round(security_score, 1),

        "confidence":
            confidence,

        "risk":
            risk,

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

        "total_tx":
            total_tx,

        "buy_ratio":
            buy_ratio,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "turnover":
            turnover,

        "momentum_delta":
            momentum_delta,

        "momentum_reasons":
            momentum_reasons,

        "risk_reasons":
            risk_reasons,

        "security_reasons":
            security.get(
                "reasons",
                []
            ),

        "security_known":
            security.get(
                "known",
                False
            ),
    }


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(item):

    p5 = item["p5"]
    p1h = item["p1h"]

    momentum = item["momentum"]
    quality = item["quality"]
    confidence = item["confidence"]
    risk = item["risk"]

    # --------------------------------------------------------
    # DUMP
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
        p1h >= OVEREXTENDED_1H
        or (
            p1h >= OVEREXTENDED_1H_WITH_MOMENTUM
            and p5 >= 5
        )
    ):

        return "🔴 OVEREXTENDED"

    # --------------------------------------------------------
    # VERY STRONG
    # --------------------------------------------------------

    if (
        momentum >= VERY_STRONG_SCORE
        and quality >= VERY_STRONG_MIN_QUALITY
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
        momentum >= STRONG_SCORE
        and quality >= STRONG_MIN_QUALITY
        and confidence >= 65
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
    ):

        return "🔥 STRONG"

    # --------------------------------------------------------
    # EARLY
    # --------------------------------------------------------

    if (
        momentum >= EARLY_SCORE
        and quality >= EARLY_MIN_QUALITY
        and confidence >= 58
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
        and p1h < 60
    ):

        return "🟢 EARLY"

    # --------------------------------------------------------
    # WATCH
    # --------------------------------------------------------

    if (
        momentum >= 40
        and confidence >= 45
        and p5 > 0
        and p1h > 0
        and risk < 65
    ):

        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ============================================================
# SIGNAL
# ============================================================

def get_signal_type(item):

    stage = item["stage"]

    if stage in (
        "🔻 DUMPING",
        "🔴 OVEREXTENDED",
        "⚪ LOW MOMENTUM"
    ):

        return None

    # Не даём неизвестной security
    # создать сильный сигнал.
    if (
        REQUIRE_SECURITY_FOR_SIGNAL
        and not item["security_known"]
    ):

        return None

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    if (
        item["momentum_delta"]
        >= BREAKOUT_DELTA

        and item["p5"]
        >= BREAKOUT_MIN_5M

        and item["p1h"]
        > BREAKOUT_MIN_1H

        and item["liquidity"]
        >= 20_000

        and item["risk"]
        < 40

        and item["quality"]
        >= 45

        and item["confidence"]
        >= 60
    ):

        return "🚀 BREAKOUT"

    # --------------------------------------------------------
    # NORMAL
    # --------------------------------------------------------

    if stage == "🚨 VERY STRONG":

        return "🚨 VERY STRONG"

    if stage == "🔥 STRONG":

        return "🔥 STRONG"

    if stage == "🟢 EARLY":

        return "🟢 EARLY"

    return None


# ============================================================
# FETCH PAIRS IN PARALLEL
# ============================================================

def fetch_pairs(addresses):

    pairs = {}

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        jobs = {
            executor.submit(
                get_pair,
                address
            ): address

            for address in addresses
        }

        for future in as_completed(jobs):

            address = jobs[future]

            try:

                pair = future.result()

                if pair:

                    pairs[address] = pair

            except Exception as e:

                print(
                    "FETCH ERROR:",
                    address,
                    e
                )

    return pairs


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

    security_cache = load_json(
        SECURITY_FILE,
        {}
    )

    addresses = get_addresses()

    print(
        f"Unique Solana addresses: "
        f"{len(addresses)}"
    )

    pairs = fetch_pairs(
        addresses
    )

    print(
        f"Pairs received: "
        f"{len(pairs)}"
    )

    candidates = []

    filtered = 0

    # --------------------------------------------------------
    # FIRST PASS
    # --------------------------------------------------------

    for address, pair in pairs.items():

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

        candidates.append({
            "address": address,
            "pair": pair,
            "name": name,
            "symbol": symbol
        })

    print(
        f"Filtered: {filtered}"
    )

    print(
        f"Candidates: {len(candidates)}"
    )

    # --------------------------------------------------------
    # SECURITY
    # --------------------------------------------------------

    security_addresses = [
        x["address"]
        for x in candidates
    ]

    security_results = security_scan(
        security_addresses,
        security_cache
    )

    # Update cache
    for address, result in security_results.items():

        security_cache[address] = result

    save_json(
        SECURITY_FILE,
        security_cache
    )

    print(
        f"Security checked: "
        f"{len(security_results)}"
    )

    # --------------------------------------------------------
    # ANALYSIS
    # --------------------------------------------------------

    results = []

    new_state = {}

    now = datetime.now(
        timezone.utc
    )

    now_iso = now.isoformat()
    now_ts = now.timestamp()

    for candidate in candidates:

        address = candidate["address"]
        pair = candidate["pair"]

        old = state.get(
            address,
            {}
        )

        security = security_results.get(
            address,
            security_cache.get(
                address,
                {
                    "known": False,
                    "risk": 50,
                    "reasons": [
                        "security unavailable"
                    ]
                }
            )
        )

        result = analyse(
            pair,
            old,
            security
        )

        result["name"] = (
            candidate["name"]
        )

        result["symbol"] = (
            candidate["symbol"]
        )

        result["address"] = address

        result["stage"] = classify(
            result
        )

        history = old.get(
            "history",
            []
        )

        history.append({

            "time":
                now_iso,

            "momentum":
                result["momentum"],

            "quality":
                result["quality"],

            "confidence":
                result["confidence"],

            "risk":
                result["risk"],

            "p5":
                result["p5"],

            "p1h":
                result["p1h"]
        })

        history = history[-50:]

        new_state[address] = {

            "name":
                candidate["name"],

            "symbol":
                candidate["symbol"],

            "momentum":
                result["momentum"],

            "quality":
                result["quality"],

            "confidence":
                result["confidence"],

            "risk":
                result["risk"],

            "previous_momentum":
                num(
                    old.get(
                        "momentum",
                        0
                    )
                ),

            "v5":
                result["v5"],

            "tx5":
                result["total_tx"],

            "history":
                history,

            "last_alert":
                old.get(
                    "last_alert",
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

            "timestamp":
                now_iso
        }

        results.append(
            result
        )

    # --------------------------------------------------------
    # SORT BY CONFIDENCE
    # --------------------------------------------------------

    results.sort(
        key=lambda x: (
            x["confidence"],
            x["momentum"],
            x["quality"]
        ),
        reverse=True
    )

    print("\nTOP CANDIDATES:")

    for item in results[:TOP_RESULTS]:

        print(
            f"{item['symbol']} | "
            f"Q {item['quality']}/100 | "
            f"M {item['momentum']}/100 | "
            f"C {item['confidence']:.1f}/100 | "
            f"Δ {item['momentum_delta']:+.0f} | "
            f"{item['stage']} | "
            f"5m {item['p5']:+.1f}% | "
            f"1h {item['p1h']:+.1f}% | "
            f"liq {money(item['liquidity'])} | "
            f"risk {item['risk']}/100"
        )

    # --------------------------------------------------------
    # SIGNALS
    # --------------------------------------------------------

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
                "last_alert"
            )
        )

        last_alert_confidence = num(
            old.get(
                "last_alert_confidence"
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

        confidence_jump = (
            item["confidence"]
            >=
            last_alert_confidence
            + RE_ALERT_SCORE_INCREASE
        )

        new_type = (
            signal_type
            != last_alert_type
        )

        should_alert = (
            last_alert == 0
            or cooldown_passed
            or confidence_jump
            or new_type
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
        ]["last_alert"] = now_ts

        new_state[
            item["address"]
        ]["last_alert_confidence"] = (
            item["confidence"]
        )

        new_state[
            item["address"]
        ]["last_alert_type"] = (
            signal_type
        )

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

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

            "signal":
                item["signal_type"],

            "momentum":
                item["momentum"],

            "quality":
                item["quality"],

            "confidence":
                item["confidence"],

            "risk":
                item["risk"],

            "p5":
                item["p5"],

            "p1h":
                item["p1h"]
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

    security_text = (
        "✅ verified"
        if item["security_known"]
        else "⚠️ unknown"
    )

    reasons = []

    reasons.extend(
        item["momentum_reasons"][:4]
    )

    reasons.extend(
        item["risk_reasons"][:3]
    )

    reasons = ", ".join(
        reasons
    )

    address = item["address"]

    dex_url = (
        "https://dexscreener.com/solana/"
        + address
    )

    return (

        f"{item['signal_type']}\n\n"

        f"🚀 {item['name']} "
        f"({item['symbol']})\n\n"

        f"🎯 Confidence: "
        f"{item['confidence']:.1f}/100\n"

        f"⚡ Momentum: "
        f"{item['momentum']}/100\n"

        f"🏆 Quality: "
        f"{item['quality']}/100\n"

        f"⚠️ Risk: "
        f"{item['risk']}/100 "
        f"({risk_text})\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n"

        f"📊 Volume accel: "
        f"{item['volume_acceleration']:.1f}x\n"

        f"🔄 TX accel: "
        f"{item['tx_acceleration']:.1f}x\n\n"

        f"🟢 Buys 5m: "
        f"{item['buys5']}\n"

        f"🔴 Sells 5m: "
        f"{item['sells5']}\n"

        f"📊 Buy ratio: "
        f"{item['buy_ratio'] * 100:.0f}%\n\n"

        f"🛡 Security: "
        f"{security_text}\n"

        f"🧠 {reasons}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n\n"

        f"📋 `{address}`\n\n"

        f"⚠️ Алгоритмический сигнал. "
        f"Это не гарантия роста."
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
                    False
            },

            timeout=15
        )

        print(
            "Telegram:",
            response.status_code,
            response.text[:250]
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

    try:

        main()

    except Exception as e:

        print(
            "FATAL ERROR:",
            repr(e)
        )

        raise
