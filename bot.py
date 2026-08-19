import os
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 10

# Сигнал отправляется начиная с этого Score
ALERT_SCORE = 65

# Повторный сигнал только если Score вырос ещё на столько
RE_ALERT_SCORE_INCREASE = 15

# Минимальная пауза между сигналами одного токена
ALERT_COOLDOWN = 30 * 60

# Минимальная ликвидность
MIN_LIQUIDITY = 10_000

# Минимальный объём за 24 часа
MIN_VOLUME_24H = 10_000

BLOCKED = {
    "SOL",
    "WSOL",
    "USDC",
    "USDT",
    "USD1",
    "USDE",
}

ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
]


# ==================================================
# UTILS
# ==================================================

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


# ==================================================
# TOKEN ADDRESSES
# ==================================================

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


# ==================================================
# GET PAIR
# ==================================================

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

        # Выбираем наиболее ликвидную пару
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


# ==================================================
# CLASSIFICATION
# ==================================================

def classify(
    p5,
    p1h,
    liquidity,
    buy_ratio,
    volume_acceleration,
    score
):

    # ------------------------------------------
    # OVEREXTENDED
    # ------------------------------------------

    if p1h >= 150:
        return "🔴 OVEREXTENDED"

    if p1h >= 100 and p5 >= 5:
        return "🔴 OVEREXTENDED"

    # ------------------------------------------
    # VERY STRONG
    # ------------------------------------------

    if (
        score >= 85
        and p5 > 0
        and p1h > 0
        and p1h < 100
        and liquidity >= 20_000
    ):
        return "🚨 VERY STRONG"

    # ------------------------------------------
    # STRONG
    # ------------------------------------------

    if (
        score >= 75
        and p5 > 0
        and p1h > 0
        and p1h < 100
        and liquidity >= 20_000
    ):
        return "🔥 STRONG"

    # ------------------------------------------
    # EARLY
    # ------------------------------------------

    if (
        score >= 65
        and p5 > 0
        and p1h > 0
        and p1h < 100
        and liquidity >= 20_000
    ):
        return "🟢 EARLY"

    # ------------------------------------------
    # WATCH
    # ------------------------------------------

    if (
        score >= 45
        and p5 > 0
        and p1h > 0
    ):
        return "🟡 WATCH"

    if p5 > 0 and p1h > 0:
        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ==================================================
# ANALYZE TOKEN
# ==================================================

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

    # ------------------------------------------
    # PRICE
    # ------------------------------------------

    p5 = num(
        change.get("m5")
    )

    p1h = num(
        change.get("h1")
    )

    # ------------------------------------------
    # VOLUME
    # ------------------------------------------

    v5 = num(
        volume.get("m5")
    )

    v1h = num(
        volume.get("h1")
    )

    v24 = num(
        volume.get("h24")
    )

    # ------------------------------------------
    # LIQUIDITY
    # ------------------------------------------

    liquidity = num(
        liquidity_data.get("usd")
    )

    # ------------------------------------------
    # TRANSACTIONS
    # ------------------------------------------

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

    score = 0

    reasons = []

    # ==================================================
    # 5 MINUTE MOMENTUM
    # ==================================================

    if 3 <= p5 <= 10:

        score += 20

        reasons.append(
            "умеренный рост 5м"
        )

    elif 10 < p5 <= 20:

        score += 15

        reasons.append(
            "сильный импульс 5м"
        )

    elif 20 < p5 <= 30:

        score += 5

        reasons.append(
            "резкий рост 5м"
        )

    elif p5 > 30:

        score -= 15

        reasons.append(
            "слишком резкий памп"
        )

    elif p5 < -10:

        score -= 25

        reasons.append(
            "падение 5м"
        )

    # ==================================================
    # 1 HOUR TREND
    # ==================================================

    if 5 <= p1h <= 30:

        score += 20

        reasons.append(
            "здоровый тренд 1ч"
        )

    elif 30 < p1h <= 60:

        score += 10

        reasons.append(
            "сильный тренд 1ч"
        )

    elif 60 < p1h < 100:

        score += 3

        reasons.append(
            "сильный рост 1ч"
        )

    elif p1h >= 100:

        score -= 25

        reasons.append(
            "токен уже сильно вырос"
        )

    elif p1h < -30:

        score -= 20

        reasons.append(
            "негативный тренд 1ч"
        )

    # ==================================================
    # LIQUIDITY
    # ==================================================

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

    # ==================================================
    # BUY / SELL PRESSURE
    # ==================================================

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

    # ==================================================
    # VOLUME ACCELERATION
    # ==================================================

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
                f"объём немного растёт "
                f"{volume_acceleration:.1f}x"
            )

    # ==================================================
    # TRANSACTION ACCELERATION
    # ==================================================

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
                "число сделок резко растёт"
            )

        elif tx_acceleration >= 1.5:

            score += 5

            reasons.append(
                "число сделок растёт"
            )

    # ==================================================
    # TURNOVER
    # ==================================================

    turnover = 0

    if liquidity > 0:

        turnover = (
            v24 / liquidity
        )

    if turnover >= 2:

        score += 5

        reasons.append(
            "активный оборот"
        )

    if turnover > 50:

        score -= 10

        reasons.append(
            "аномальный оборот"
        )

    # ==================================================
    # FINAL SCORE
    # ==================================================

    score = max(
        0,
        min(
            100,
            score
        )
    )

    # ==================================================
    # CLASS
    # ==================================================

    stage = classify(
        p5,
        p1h,
        liquidity,
        buy_ratio,
        volume_acceleration,
        score
    )

    return {

        "score": score,

        "stage": stage,

        "p5": p5,

        "p1h": p1h,

        "v5": v5,

        "v1h": v1h,

        "v24": v24,

        "liquidity": liquidity,

        "buys5": buys5,

        "sells5": sells5,

        "buy_ratio": buy_ratio,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "turnover": turnover,

        "reasons": reasons,
    }


# ==================================================
# SCAN
# ==================================================

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

        # ------------------------------------------
        # SCORE HISTORY
        # ------------------------------------------

        history = old.get(
            "history",
            []
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

        history.append({

            "time":
                now_iso,

            "score":
                result["score"],

            "p5":
                result["p5"],

            "p1h":
                result["p1h"],

        })

        # Храним последние 30 измерений
        history = history[-30:]

        # ------------------------------------------
        # STATE
        # ------------------------------------------

        new_state[address] = {

            "name":
                name,

            "symbol":
                symbol,

            "score":
                result["score"],

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

            "timestamp":
                now_iso,
        }

        result["name"] = name

        result["symbol"] = symbol

        result["address"] = address

        result["score_delta"] = score_delta

        results.append(
            result
        )

    # ==================================================
    # SORT
    # ==================================================

    results.sort(
        key=lambda x: (
            x["score"],
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

    # ==================================================
    # TOP RESULTS
    # ==================================================

    print(
        "\nTOP CANDIDATES:"
    )

    for item in results[:TOP_RESULTS]:

        print(
            f"{item['symbol']} | "
            f"{item['score']}/100 | "
            f"Δ {item['score_delta']:+.0f} | "
            f"{item['stage']} | "
            f"5m {item['p5']:+.1f}% | "
            f"1h {item['p1h']:+.1f}% | "
            f"liq {money(item['liquidity'])}"
        )

    # ==================================================
    # TELEGRAM ALERTS
    # ==================================================

    now_ts = now.timestamp()

    strong = []

    for item in results:

        # Перегретые токены не отправляем
        if item["stage"] == "🔴 OVEREXTENDED":

            continue

        if item["score"] < ALERT_SCORE:

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

        # Первый сигнал
        # или cooldown закончился
        # или Score резко вырос
        if (

            last_alert == 0

            or cooldown_passed

            or score_jump

        ):

            strong.append(
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

    # ==================================================
    # SAVE STATE
    # ==================================================

    save_json(
        STATE_FILE,
        new_state
    )

    # ==================================================
    # SAVE SIGNAL HISTORY
    # ==================================================

    for item in strong:

        signals.append({

            "timestamp":
                now_iso,

            "address":
                item["address"],

            "symbol":
                item["symbol"],

            "score":
                item["score"],

            "score_delta":
                item["score_delta"],

            "stage":
                item["stage"],

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
        f"\nStrong signals: "
        f"{len(strong)}"
    )

    return strong


# ==================================================
# TELEGRAM MESSAGE
# ==================================================

def format_signal(item):

    reasons = ", ".join(
        item["reasons"][:6]
    )

    address = item["address"]

    dex_url = (
        "https://dexscreener.com/solana/"
        + address
    )

    return (

        f"{item['stage']}\n\n"

        f"🚀 {item['name']} "
        f"({item['symbol']})\n"

        f"⭐ Momentum: "
        f"{item['score']}/100\n"

        f"📊 Score change: "
        f"{item['score_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n\n"

        f"⚡ Volume 5m: "
        f"{money(item['v5'])}\n"

        f"📈 Volume acceleration: "
        f"{item['volume_acceleration']:.1f}x\n"

        f"🔄 TX acceleration: "
        f"{item['tx_acceleration']:.1f}x\n\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n\n"

        f"🟢 Buys 5m: "
        f"{item['buys5']}\n"

        f"🔴 Sells 5m: "
        f"{item['sells5']}\n\n"

        f"🧠 {reasons}\n\n"

        f"🔎 [DexScreener]({dex_url})\n\n"

        f"📋 `{address}`"
    )


# ==================================================
# SEND TELEGRAM
# ==================================================

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


# ==================================================
# MAIN
# ==================================================

def main():

    strong = scan()

    print(
        f"Strong signals to Telegram: "
        f"{len(strong)}"
    )

    for item in strong:

        send_telegram(
            format_signal(item)
        )


if __name__ == "__main__":
    main()
