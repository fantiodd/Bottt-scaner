import os
import json
import time
import math
import requests
from datetime import datetime, timezone


# ============================================================
# SOLANA MOMENTUM SCANNER v4
# ============================================================

VERSION = "4.0"

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Бесплатный публичный Solana RPC.
# Для частого запуска лучше указать свой RPC через секрет SOLANA_RPC.
SOLANA_RPC = os.environ.get(
    "SOLANA_RPC",
    "https://api.mainnet-beta.solana.com"
)

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 12

# ------------------------------------------------------------
# FILTERS
# ------------------------------------------------------------

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

MIN_SIGNAL_LIQUIDITY = 20_000

# ------------------------------------------------------------
# SIGNAL THRESHOLDS
# ------------------------------------------------------------

EARLY_SCORE = 65
STRONG_SCORE = 75
VERY_STRONG_SCORE = 85

BREAKOUT_DELTA = 22

BREAKOUT_MIN_5M = 3
BREAKOUT_MIN_1H = 0

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MAX_SIGNAL_RISK = 55

DUMP_5M = -12
DUMP_1H = -20

OVEREXTENDED_1H = 180
OVEREXTENDED_5M = 35

# ------------------------------------------------------------
# ALERT CONTROL
# ------------------------------------------------------------

ALERT_COOLDOWN = 30 * 60
RE_ALERT_SCORE_INCREASE = 15

# ------------------------------------------------------------
# SECURITY
# ------------------------------------------------------------

SECURITY_CHECK_LIMIT = 12

RUGCHECK_ENABLED = True

# ------------------------------------------------------------
# API
# ------------------------------------------------------------

DEX_PROFILE_URL = (
    "https://api.dexscreener.com/"
    "token-profiles/latest/v1"
)

DEX_BOOST_URL = (
    "https://api.dexscreener.com/"
    "token-boosts/latest/v1"
)

DEX_TOKEN_URL = (
    "https://api.dexscreener.com/"
    "tokens/v1/solana/"
)

RUGCHECK_URL = (
    "https://api.rugcheck.xyz/v1/tokens/"
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
    "USDT0",
}


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "SolanaMomentumScanner/4.0"
})


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


def clamp(value, low=0, high=100):
    return max(
        low,
        min(high, value)
    )


def money(value):

    value = num(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def pct(value):
    return f"{num(value):+.1f}%"


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

    tmp = filename + ".tmp"

    with open(
        tmp,
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
        tmp,
        filename
    )


def now_iso():

    return datetime.now(
        timezone.utc
    ).isoformat()


def now_ts():

    return time.time()


# ============================================================
# HTTP
# ============================================================

def get_json(
    url,
    timeout=15,
    retries=2
):

    for attempt in range(
        retries + 1
    ):

        try:

            response = SESSION.get(
                url,
                timeout=timeout
            )

            if response.status_code == 200:

                return response.json()

            if response.status_code == 429:

                wait = 2 + attempt * 2

                print(
                    f"429 rate limit -> "
                    f"sleep {wait}s"
                )

                time.sleep(wait)

                continue

            print(
                f"HTTP {response.status_code}: "
                f"{url}"
            )

        except Exception as e:

            print(
                "HTTP ERROR:",
                e
            )

        if attempt < retries:

            time.sleep(
                1 + attempt
            )

    return None


# ============================================================
# TOKEN DISCOVERY
# ============================================================

def get_addresses():

    addresses = set()

    endpoints = [
        DEX_PROFILE_URL,
        DEX_BOOST_URL
    ]

    for endpoint in endpoints:

        data = get_json(
            endpoint
        )

        print(
            f"{endpoint} -> "
            f"{'OK' if data is not None else 'FAILED'}"
        )

        if not isinstance(
            data,
            list
        ):
            continue

        for item in data:

            if item.get(
                "chainId"
            ) != "solana":

                continue

            address = item.get(
                "tokenAddress"
            )

            if address:

                addresses.add(
                    address
                )

    return list(
        addresses
    )


# ============================================================
# DEXSCREENER BATCH
# ============================================================

def get_pairs_batch(
    addresses
):

    pairs = {}

    # DexScreener accepts max 30 token addresses
    # in this endpoint.
    for i in range(
        0,
        len(addresses),
        30
    ):

        chunk = addresses[
            i:i + 30
        ]

        url = (
            DEX_TOKEN_URL
            + ",".join(chunk)
        )

        data = get_json(
            url,
            timeout=20
        )

        if not isinstance(
            data,
            list
        ):
            continue

        for pair in data:

            if pair.get(
                "chainId"
            ) != "solana":

                continue

            token = pair.get(
                "baseToken",
                {}
            )

            address = token.get(
                "address"
            )

            if not address:
                continue

            old = pairs.get(
                address
            )

            if old is None:

                pairs[address] = pair

                continue

            # Keep the most liquid pair.
            old_liq = num(
                old.get(
                    "liquidity",
                    {}
                ).get("usd")
            )

            new_liq = num(
                pair.get(
                    "liquidity",
                    {}
                ).get("usd")
            )

            if new_liq > old_liq:

                pairs[address] = pair

    return pairs


# ============================================================
# BASIC PAIR DATA
# ============================================================

def extract_pair(
    pair
):

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

    p1 = num(
        change.get("m1")
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
        liquidity_data.get(
            "usd"
        )
    )

    tx5 = txns.get(
        "m5",
        {}
    )

    tx1h = txns.get(
        "h1",
        {}
    )

    buys5 = integer(
        tx5.get("buys")
    )

    sells5 = integer(
        tx5.get("sells")
    )

    buys1h = integer(
        tx1h.get("buys")
    )

    sells1h = integer(
        tx1h.get("sells")
    )

    total5 = (
        buys5 +
        sells5
    )

    total1h = (
        buys1h +
        sells1h
    )

    buy_ratio = (
        buys5 / total5
        if total5
        else 0
    )

    turnover = (
        v24 / liquidity
        if liquidity > 0
        else 0
    )

    pair_created = integer(
        pair.get(
            "pairCreatedAt"
        )
    )

    age_hours = 0

    if pair_created:

        age_seconds = (
            time.time()
            -
            pair_created / 1000
        )

        age_hours = max(
            0,
            age_seconds / 3600
        )

    return {

        "p1": p1,
        "p5": p5,
        "p1h": p1h,
        "p6h": p6h,
        "p24": p24,

        "v5": v5,
        "v1h": v1h,
        "v6h": v6h,
        "v24": v24,

        "liquidity":
            liquidity,

        "buys5":
            buys5,

        "sells5":
            sells5,

        "buys1h":
            buys1h,

        "sells1h":
            sells1h,

        "total5":
            total5,

        "total1h":
            total1h,

        "buy_ratio":
            buy_ratio,

        "turnover":
            turnover,

        "age_hours":
            age_hours,
    }


# ============================================================
# SECURITY - SOLANA RPC
# ============================================================

def rpc_call(
    method,
    params
):

    payload = {

        "jsonrpc":
            "2.0",

        "id":
            1,

        "method":
            method,

        "params":
            params
    }

    try:

        response = SESSION.post(

            SOLANA_RPC,

            json=payload,

            timeout=15
        )

        if response.status_code != 200:

            return None

        data = response.json()

        return data.get(
            "result"
        )

    except Exception as e:

        print(
            "RPC ERROR:",
            e
        )

        return None


def get_token_supply(
    address
):

    result = rpc_call(
        "getTokenSupply",
        [
            address,
            {
                "commitment":
                    "confirmed"
            }
        ]
    )

    if not result:
        return 0

    value = result.get(
        "value",
        {}
    )

    return num(
        value.get(
            "uiAmount"
        )
    )


def get_largest_accounts(
    address
):

    result = rpc_call(

        "getTokenLargestAccounts",

        [
            address,
            {
                "commitment":
                    "confirmed"
            }
        ]
    )

    if not result:

        return []

    return result.get(
        "value",
        []
    )


# ============================================================
# TOKEN AUTHORITY PARSER
# ============================================================

def parse_mint_account(
    address
):

    result = rpc_call(

        "getAccountInfo",

        [
            address,

            {
                "encoding":
                    "jsonParsed",

                "commitment":
                    "confirmed"
            }
        ]
    )

    if not result:

        return {}

    value = result.get(
        "value"
    )

    if not value:

        return {}

    data = value.get(
        "data"
    )

    if (
        not isinstance(
            data,
            dict
        )
    ):

        return {}

    parsed = data.get(
        "parsed",
        {}
    )

    info = parsed.get(
        "info",
        {}
    )

    return {

        "mint_authority":
            info.get(
                "mintAuthority"
            ),

        "freeze_authority":
            info.get(
                "freezeAuthority"
            ),

        "decimals":
            info.get(
                "decimals"
            ),
    }


# ============================================================
# HOLDER CONCENTRATION
# ============================================================

def holder_concentration(
    address
):

    accounts = get_largest_accounts(
        address
    )

    if not accounts:

        return {

            "top1": 0,
            "top5": 0,
            "top10": 0
        }

    total = get_token_supply(
        address
    )

    if total <= 0:

        return {

            "top1": 0,
            "top5": 0,
            "top10": 0
        }

    amounts = []

    for account in accounts:

        amount = num(
            account.get(
                "uiAmount"
            )
        )

        amounts.append(
            amount
        )

    top1 = (
        sum(amounts[:1])
        / total
        * 100
    )

    top5 = (
        sum(amounts[:5])
        / total
        * 100
    )

    top10 = (
        sum(amounts[:10])
        / total
        * 100
    )

    return {

        "top1":
            clamp(top1),

        "top5":
            clamp(top5),

        "top10":
            clamp(top10),
    }


# ============================================================
# RUGCHECK
# ============================================================

def rugcheck(
    address
):

    if not RUGCHECK_ENABLED:

        return {}

    url = (
        RUGCHECK_URL
        + address
        + "/report"
    )

    data = get_json(
        url,
        timeout=15,
        retries=1
    )

    if not isinstance(
        data,
        dict
    ):

        return {}

    risks = data.get(
        "risks",
        []
    )

    score = num(
        data.get(
            "score"
        )
    )

    return {

        "score":
            score,

        "risks":
            risks,

        "raw":
            data,
    }


# ============================================================
# SECURITY SCORE
# ============================================================

def security_analysis(
    address
):

    result = {

        "risk":
            0,

        "reasons":
            [],

        "mint_authority":
            None,

        "freeze_authority":
            None,

        "top1":
            0,

        "top5":
            0,

        "top10":
            0,

        "rugcheck_score":
            None,
    }

    # --------------------------------------------
    # Authorities
    # --------------------------------------------

    authority = parse_mint_account(
        address
    )

    mint_authority = authority.get(
        "mint_authority"
    )

    freeze_authority = authority.get(
        "freeze_authority"
    )

    result[
        "mint_authority"
    ] = mint_authority

    result[
        "freeze_authority"
    ] = freeze_authority

    if mint_authority:

        result["risk"] += 25

        result[
            "reasons"
        ].append(
            "mint authority active"
        )

    if freeze_authority:

        result["risk"] += 15

        result[
            "reasons"
        ].append(
            "freeze authority active"
        )

    # --------------------------------------------
    # Holder concentration
    # --------------------------------------------

    holders = holder_concentration(
        address
    )

    result.update(
        holders
    )

    top1 = holders["top1"]
    top5 = holders["top5"]
    top10 = holders["top10"]

    if top1 >= 20:

        result["risk"] += 25

        result[
            "reasons"
        ].append(
            f"top holder {top1:.1f}%"
        )

    elif top1 >= 10:

        result["risk"] += 12

        result[
            "reasons"
        ].append(
            f"top holder {top1:.1f}%"
        )

    if top5 >= 50:

        result["risk"] += 20

        result[
            "reasons"
        ].append(
            f"top 5 hold {top5:.1f}%"
        )

    elif top5 >= 35:

        result["risk"] += 10

        result[
            "reasons"
        ].append(
            f"top 5 hold {top5:.1f}%"
        )

    # --------------------------------------------
    # RugCheck
    # --------------------------------------------

    rc = rugcheck(
        address
    )

    if rc:

        result[
            "rugcheck_score"
        ] = rc.get(
            "score"
        )

        # RugCheck score interpretation
        # is intentionally only an additional
        # layer; it does not replace on-chain checks.

        rc_score = num(
            rc.get("score")
        )

        if rc_score >= 5000:

            result["risk"] += 25

            result[
                "reasons"
            ].append(
                "high RugCheck risk"
            )

        elif rc_score >= 3000:

            result["risk"] += 12

            result[
                "reasons"
            ].append(
                "elevated RugCheck risk"
            )

        risks = rc.get(
            "risks",
            []
        )

        if isinstance(
            risks,
            list
        ):

            serious = 0

            for risk in risks:

                if not isinstance(
                    risk,
                    dict
                ):
                    continue

                level = str(
                    risk.get(
                        "level",
                        ""
                    )
                ).lower()

                if level in (
                    "danger",
                    "critical",
                    "high"
                ):

                    serious += 1

            if serious >= 2:

                result["risk"] += 20

                result[
                    "reasons"
                ].append(
                    "multiple serious security flags"
                )

    result["risk"] = clamp(
        result["risk"]
    )

    return result


# ============================================================
# MOMENTUM SCORE
# ============================================================

def momentum_analysis(
    d,
    old
):

    score = 0
    reasons = []

    p5 = d["p5"]
    p1h = d["p1h"]

    v5 = d["v5"]
    v1h = d["v1h"]

    liquidity = d["liquidity"]

    total5 = d["total5"]

    buy_ratio = d["buy_ratio"]

    # ========================================================
    # PRICE STRUCTURE
    # ========================================================

    if 2 <= p5 < 5:

        score += 12

        reasons.append(
            "раннее ускорение 5м"
        )

    elif 5 <= p5 < 10:

        score += 20

        reasons.append(
            "хороший импульс 5м"
        )

    elif 10 <= p5 < 18:

        score += 18

        reasons.append(
            "сильный импульс 5м"
        )

    elif 18 <= p5 < 30:

        score += 8

        reasons.append(
            "агрессивный импульс 5м"
        )

    elif p5 >= 30:

        score -= 18

        reasons.append(
            "перегретый импульс 5м"
        )

    elif -5 < p5 < 0:

        score -= 3

    elif -12 < p5 <= -5:

        score -= 12

        reasons.append(
            "ослабление 5м"
        )

    elif p5 <= -12:

        score -= 30

        reasons.append(
            "сильное падение 5м"
        )

    # ========================================================
    # 1H STRUCTURE
    # ========================================================

    if 3 <= p1h < 10:

        score += 14

        reasons.append(
            "ранний тренд 1ч"
        )

    elif 10 <= p1h < 25:

        score += 23

        reasons.append(
            "здоровый тренд 1ч"
        )

    elif 25 <= p1h < 45:

        score += 17

        reasons.append(
            "сильный тренд 1ч"
        )

    elif 45 <= p1h < 70:

        score += 9

        reasons.append(
            "агрессивный тренд 1ч"
        )

    elif 70 <= p1h < 100:

        score += 3

        reasons.append(
            "токен уже сильно вырос"
        )

    elif 100 <= p1h < 180:

        score -= 12

        reasons.append(
            "высокая перегретость 1ч"
        )

    elif p1h >= 180:

        score -= 30

        reasons.append(
            "экстремальный рост 1ч"
        )

    elif p1h <= -20:

        score -= 25

        reasons.append(
            "негативный тренд 1ч"
        )

    # ========================================================
    # MOMENTUM CONSISTENCY
    # ========================================================

    if p5 > 0 and p1h > 0:

        score += 8

        reasons.append(
            "5м и 1ч направлены вверх"
        )

    elif p5 > 0 and p1h < 0:

        score -= 10

        reasons.append(
            "рост 5м против тренда 1ч"
        )

    # ========================================================
    # LIQUIDITY
    # ========================================================

    if liquidity >= 150_000:

        score += 15

    elif liquidity >= 100_000:

        score += 13

    elif liquidity >= 50_000:

        score += 11

    elif liquidity >= 30_000:

        score += 8

    elif liquidity >= 20_000:

        score += 4

    else:

        score -= 20

        reasons.append(
            "низкая ликвидность"
        )

    # ========================================================
    # BUY PRESSURE
    # ========================================================

    if total5 >= 10:

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

    volume_acc = 1.0

    if old_v5 > 0 and v5 > 0:

        volume_acc = (
            v5 / old_v5
        )

        if volume_acc >= 3:

            score += 18

            reasons.append(
                f"объём x{volume_acc:.1f}"
            )

        elif volume_acc >= 2:

            score += 13

            reasons.append(
                f"объём ускоряется x{volume_acc:.1f}"
            )

        elif volume_acc >= 1.5:

            score += 8

        elif volume_acc >= 1.2:

            score += 4

        elif volume_acc < 0.7:

            score -= 7

            reasons.append(
                "объём ослабевает"
            )

    # ========================================================
    # TRANSACTION ACCELERATION
    # ========================================================

    old_tx = integer(
        old.get("tx5")
    )

    tx_acc = 1.0

    if old_tx > 0 and total5 > 0:

        tx_acc = (
            total5 /
            old_tx
        )

        if tx_acc >= 2.5:

            score += 12

            reasons.append(
                "резкое ускорение сделок"
            )

        elif tx_acc >= 1.7:

            score += 8

            reasons.append(
                "сделок становится больше"
            )

        elif tx_acc >= 1.3:

            score += 4

    # ========================================================
    # TURNOVER
    # ========================================================

    turnover = d["turnover"]

    if 2 <= turnover <= 20:

        score += 5

        reasons.append(
            "здоровый оборот"
        )

    elif turnover > 50:

        score -= 10

        reasons.append(
            "аномальный оборот"
        )

    # ========================================================
    # FINAL
    # ========================================================

    score = clamp(
        score
    )

    return {

        "score":
            score,

        "volume_acc":
            volume_acc,

        "tx_acc":
            tx_acc,

        "reasons":
            reasons,
    }


# ============================================================
# QUALITY SCORE
# ============================================================

def quality_analysis(
    d,
    security
):

    score = 0

    liquidity = d[
        "liquidity"
    ]

    v24 = d[
        "v24"
    ]

    total1h = d[
        "total1h"
    ]

    age = d[
        "age_hours"
    ]

    top5 = security.get(
        "top5",
        0
    )

    # Liquidity
    if liquidity >= 150_000:
        score += 25
    elif liquidity >= 100_000:
        score += 22
    elif liquidity >= 50_000:
        score += 18
    elif liquidity >= 30_000:
        score += 14
    elif liquidity >= 20_000:
        score += 9

    # Volume
    if v24 >= 2_000_000:
        score += 25
    elif v24 >= 1_000_000:
        score += 22
    elif v24 >= 500_000:
        score += 18
    elif v24 >= 100_000:
        score += 13
    elif v24 >= 50_000:
        score += 8

    # Transaction activity
    if total1h >= 5000:
        score += 20
    elif total1h >= 2000:
        score += 17
    elif total1h >= 1000:
        score += 14
    elif total1h >= 500:
        score += 10
    elif total1h >= 100:
        score += 6

    # Age
    if 2 <= age <= 72:

        score += 10

    elif age > 72:

        score += 7

    elif age < 1:

        score += 3

    # Holder distribution
    if top5 < 25:

        score += 20

    elif top5 < 40:

        score += 13

    elif top5 < 55:

        score += 7

    return clamp(
        score
    )


# ============================================================
# CONFIDENCE
# ============================================================

def confidence_analysis(
    momentum,
    quality,
    risk,
    d
):

    score = 0

    score += (
        momentum["score"]
        * 0.45
    )

    score += (
        quality
        * 0.35
    )

    score += (
        (100 - risk)
        * 0.20
    )

    # Confirmation bonus
    if (
        d["p5"] > 0
        and d["p1h"] > 0
        and d["buy_ratio"] > 0.55
    ):

        score += 5

    # Contradiction penalty
    if (
        d["p5"] > 5
        and d["p1h"] < 0
    ):

        score -= 10

    return clamp(
        score
    )


# ============================================================
# CLASSIFICATION
# ============================================================

def classify(
    d,
    momentum,
    quality,
    confidence,
    security
):

    p5 = d["p5"]
    p1h = d["p1h"]

    risk = security[
        "risk"
    ]

    m = momentum[
        "score"
    ]

    # --------------------------------------------
    # DUMP
    # --------------------------------------------

    if (
        p5 <= DUMP_5M
        or (
            p1h <= DUMP_1H
            and p5 < 0
        )
    ):

        return "🔻 DUMPING"

    # --------------------------------------------
    # OVEREXTENDED
    # --------------------------------------------

    if (
        p1h >= OVEREXTENDED_1H
        or (
            p5 >= OVEREXTENDED_5M
            and p1h >= 60
        )
    ):

        return "🔴 OVEREXTENDED"

    # --------------------------------------------
    # VERY STRONG
    # --------------------------------------------

    if (
        m >= VERY_STRONG_SCORE
        and quality >= 50
        and confidence >= 70
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
    ):

        return "🚨 VERY STRONG"

    # --------------------------------------------
    # STRONG
    # --------------------------------------------

    if (
        m >= STRONG_SCORE
        and quality >= 40
        and confidence >= 62
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
    ):

        return "🔥 STRONG"

    # --------------------------------------------
    # EARLY
    # --------------------------------------------

    if (
        m >= EARLY_SCORE
        and quality >= 35
        and confidence >= 55
        and risk < MAX_SIGNAL_RISK
        and p5 > 0
        and p1h > 0
        and p1h < 60
    ):

        return "🟢 EARLY"

    # --------------------------------------------
    # WATCH
    # --------------------------------------------

    if (
        m >= 40
        and p5 > 0
        and p1h > 0
        and risk < 70
    ):

        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ============================================================
# SIGNAL TYPE
# ============================================================

def signal_type(
    item
):

    stage = item[
        "stage"
    ]

    if stage in (
        "🔻 DUMPING",
        "🔴 OVEREXTENDED",
        "⚪ LOW MOMENTUM"
    ):

        return None

    d = item

    # --------------------------------------------
    # BREAKOUT
    # --------------------------------------------

    if (
        d["score_delta"]
        >= BREAKOUT_DELTA

        and d["p5"]
        >= BREAKOUT_MIN_5M

        and d["p1h"]
        > BREAKOUT_MIN_1H

        and d["liquidity"]
        >= MIN_SIGNAL_LIQUIDITY

        and d["risk"]
        < MAX_SIGNAL_RISK

        and d["volume_acc"]
        >= 1.2
    ):

        return "🚀 BREAKOUT"

    # --------------------------------------------
    # VERY STRONG
    # --------------------------------------------

    if (
        d["score"]
        >= VERY_STRONG_SCORE
    ):

        return "🚨 VERY STRONG"

    # --------------------------------------------
    # STRONG
    # --------------------------------------------

    if (
        d["score"]
        >= STRONG_SCORE
    ):

        return "🔥 STRONG"

    # --------------------------------------------
    # EARLY
    # --------------------------------------------

    if (
        d["score"]
        >= EARLY_SCORE
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

    pairs = get_pairs_batch(
        addresses
    )

    print(
        f"Pairs received: "
        f"{len(pairs)}"
    )

    results = []

    new_state = {}

    filtered = 0

    security_checked = 0

    scan_time = now_iso()

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

        d = extract_pair(
            pair
        )

        if (
            d["liquidity"]
            < MIN_LIQUIDITY
        ):

            filtered += 1
            continue

        if (
            d["v24"]
            < MIN_VOLUME_24H
        ):

            filtered += 1
            continue

        old = state.get(
            address,
            {}
        )

        momentum = momentum_analysis(
            d,
            old
        )

        # ----------------------------------------------------
        # Security only for potentially interesting tokens.
        # This prevents RPC overload.
        # ----------------------------------------------------

        preliminary = (
            momentum["score"]
            >= 35
            or d["p5"] > 3
            or d["p1h"] > 10
        )

        if preliminary:

            security = security_analysis(
                address
            )

            security_checked += 1

        else:

            security = {

                "risk":
                    0,

                "reasons":
                    [],

                "mint_authority":
                    None,

                "freeze_authority":
                    None,

                "top1":
                    0,

                "top5":
                    0,

                "top10":
                    0,

                "rugcheck_score":
                    None,
            }

        quality = quality_analysis(
            d,
            security
        )

        confidence = confidence_analysis(
            momentum,
            quality,
            security["risk"],
            d
        )

        previous_score = num(
            old.get(
                "score"
            )
        )

        score_delta = (
            momentum["score"]
            - previous_score
        )

        item = {

            "address":
                address,

            "name":
                name,

            "symbol":
                symbol,

            "score":
                momentum["score"],

            "quality":
                quality,

            "confidence":
                confidence,

            "risk":
                security["risk"],

            "score_delta":
                score_delta,

            "p1":
                d["p1"],

            "p5":
                d["p5"],

            "p1h":
                d["p1h"],

            "p6h":
                d["p6h"],

            "p24":
                d["p24"],

            "v5":
                d["v5"],

            "v1h":
                d["v1h"],

            "v6h":
                d["v6h"],

            "v24":
                d["v24"],

            "liquidity":
                d["liquidity"],

            "buys5":
                d["buys5"],

            "sells5":
                d["sells5"],

            "buy_ratio":
                d["buy_ratio"],

            "total5":
                d["total5"],

            "total1h":
                d["total1h"],

            "turnover":
                d["turnover"],

            "age_hours":
                d["age_hours"],

            "volume_acc":
                momentum["volume_acc"],

            "tx_acc":
                momentum["tx_acc"],

            "reasons":
                momentum["reasons"],

            "risk_reasons":
                security["reasons"],

            "mint_authority":
                security["mint_authority"],

            "freeze_authority":
                security["freeze_authority"],

            "top1":
                security["top1"],

            "top5":
                security["top5"],

            "top10":
                security["top10"],

            "rugcheck_score":
                security[
                    "rugcheck_score"
                ],
        }

        item["stage"] = classify(
            d,
            momentum,
            quality,
            confidence,
            security
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
                scan_time,

            "score":
                item["score"],

            "quality":
                item["quality"],

            "confidence":
                item["confidence"],

            "risk":
                item["risk"],

            "p5":
                item["p5"],

            "p1h":
                item["p1h"],

            "liquidity":
                item["liquidity"],
        })

        history = history[-60:]

        new_state[address] = {

            "name":
                name,

            "symbol":
                symbol,

            "score":
                item["score"],

            "quality":
                item["quality"],

            "confidence":
                item["confidence"],

            "risk":
                item["risk"],

            "v5":
                item["v5"],

            "tx5":
                item["total5"],

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
                scan_time,
        }

        results.append(
            item
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

    print(
        "\nTOP CANDIDATES:"
    )

    for item in results[
        :TOP_RESULTS
    ]:

        print(

            f"{item['symbol']} | "

            f"Q {item['quality']}/100 | "

            f"M {item['score']}/100 | "

            f"C {item['confidence']}/100 | "

            f"Δ {item['score_delta']:+.0f} | "

            f"{item['stage']} | "

            f"5m {pct(item['p5'])} | "

            f"1h {pct(item['p1h'])} | "

            f"liq "
            f"{money(item['liquidity'])} | "

            f"risk "
            f"{item['risk']}/100"
        )

    # ========================================================
    # SIGNALS
    # ========================================================

    signal_items = []

    current_time = now_ts()

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
                "last_alert",
                0
            )
        )

        last_score = num(
            old.get(
                "last_alert_score",
                0
            )
        )

        last_type = old.get(
            "last_alert_type",
            ""
        )

        cooldown_passed = (

            last_alert == 0

            or

            current_time
            -
            last_alert
            >=
            ALERT_COOLDOWN
        )

        score_jump = (

            item["score"]
            >=
            last_score
            +
            RE_ALERT_SCORE_INCREASE
        )

        type_changed = (
            stype != last_type
        )

        # Breakout is special:
        # if a token becomes breakout after
        # an EARLY alert, send it again.
        should_alert = (

            last_alert == 0

            or cooldown_passed

            or score_jump

            or type_changed
        )

        if not should_alert:

            continue

        item[
            "signal_type"
        ] = stype

        signal_items.append(
            item
        )

        new_state[
            item["address"]
        ][
            "last_alert"
        ] = current_time

        new_state[
            item["address"]
        ][
            "last_alert_score"
        ] = item["score"]

        new_state[
            item["address"]
        ][
            "last_alert_type"
        ] = stype

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
                scan_time,

            "address":
                item["address"],

            "symbol":
                item["symbol"],

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

            "score_delta":
                item["score_delta"],

            "p5":
                item["p5"],

            "p1h":
                item["p1h"],

            "liquidity":
                item["liquidity"],
        })

    signals = signals[
        -2000:
    ]

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

def format_signal(
    item
):

    address = item[
        "address"
    ]

    dex_url = (
        "https://dexscreener.com/"
        "solana/"
        + address
    )

    risk = item[
        "risk"
    ]

    if risk >= 70:

        risk_text = "🔴 HIGH"

    elif risk >= 40:

        risk_text = "🟡 MEDIUM"

    else:

        risk_text = "🟢 LOW"

    reasons = (
        item["reasons"][:5]
        +
        item["risk_reasons"][:3]
    )

    reason_text = ", ".join(
        reasons
    )

    security_lines = []

    if item[
        "mint_authority"
    ]:

        security_lines.append(
            "⚠️ Mint authority: ACTIVE"
        )

    else:

        security_lines.append(
            "✅ Mint authority: revoked/unknown"
        )

    if item[
        "freeze_authority"
    ]:

        security_lines.append(
            "⚠️ Freeze authority: ACTIVE"
        )

    else:

        security_lines.append(
            "✅ Freeze authority: revoked/unknown"
        )

    if item["top5"]:

        security_lines.append(
            f"👥 Top 5: "
            f"{item['top5']:.1f}%"
        )

    return (

        f"{item['signal_type']}\n\n"

        f"🚀 *{item['name']}* "
        f"({item['symbol']})\n\n"

        f"🎯 Confidence: "
        f"{item['confidence']:.0f}/100\n"

        f"⭐ Momentum: "
        f"{item['score']:.0f}/100\n"

        f"🏆 Quality: "
        f"{item['quality']:.0f}/100\n"

        f"📊 Δ Score: "
        f"{item['score_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{pct(item['p5'])}\n"

        f"📈 1h: "
        f"{pct(item['p1h'])}\n"

        f"📈 6h: "
        f"{pct(item['p6h'])}\n\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n"

        f"⚡ Volume: "
        f"x{item['volume_acc']:.1f}\n"

        f"🔄 Transactions: "
        f"x{item['tx_acc']:.1f}\n\n"

        f"🟢 Buys 5m: "
        f"{item['buys5']}\n"

        f"🔴 Sells 5m: "
        f"{item['sells5']}\n\n"

        f"⚠️ Risk: "
        f"{item['risk']}/100 "
        f"{risk_text}\n\n"

        f"🔐 Security:\n"
        + "\n".join(
            security_lines
        )
        + "\n\n"

        f"🧠 *Why:*\n"
        f"{reason_text}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n\n"

        f"📋 `{address}`\n\n"

        f"⚠️ Алгоритмический сигнал. "
        f"Это не гарантия роста."
    )


def send_telegram(
    text
):

    if not BOT_TOKEN:

        print(
            "BOT_TOKEN is missing"
        )

        return False

    if not CHAT_ID:

        print(
            "CHAT_ID is missing"
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
            "Markdown",

        "disable_web_page_preview":
            False,
    }

    try:

        response = SESSION.post(

            url,

            json=payload,

            timeout=15
        )

        print(
            "Telegram:",
            response.status_code,
            response.text[:250]
        )

        return (
            response.status_code
            == 200
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

    print()
    print(
        "========================================"
    )

    print(
        f"   SOLANA MOMENTUM SCANNER v{VERSION}"
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


if __name__ == "__main__":

    main()
