import os
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 10
ALERT_SCORE = 65

# Минимум через сколько секунд можно повторно
# прислать сигнал по одному токену
ALERT_COOLDOWN = 30 * 60

# Если Score вырос настолько — разрешаем
# повторное уведомление раньше cooldown
RE_ALERT_SCORE_INCREASE = 15

BLOCKED = {
    "SOL",
    "WSOL",
    "USDC",
    "USDT",
    "USD1",
    "USDE",
}

# Несколько источников DexScreener
ENDPOINTS = [
    "https://api.dexscreener.com/token-profiles/latest/v1",
    "https://api.dexscreener.com/token-boosts/latest/v1",
]


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
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


# ==================================================
# PROFILES / BOOSTS
# ==================================================

def get_addresses():

    addresses = set()

    for endpoint in ENDPOINTS:

        try:

            r = requests.get(
                endpoint,
                timeout=15
            )

            print(
                f"{endpoint} -> {r.status_code}"
            )

            if r.status_code != 200:
                continue

            data = r.json()

            if not isinstance(data, list):
                continue

            for item in data:

                if item.get("chainId") != "solana":
                    continue

                address = item.get(
                    "tokenAddress"
                )

                if address:
                    addresses.add(
                        address
                    )

        except Exception as e:

            print(
                "SOURCE ERROR:",
                e
            )

    return list(addresses)


# ==================================================
# PAIR
# ==================================================

def get_pair(address):

    try:

        url = (
            "https://api.dexscreener.com/"
            f"token-pairs/v1/solana/{address}"
        )

        r = requests.get(
            url,
            timeout=10
        )

        if r.status_code != 200:
            return None

        data = r.json()

        if not isinstance(data, list):
            return None

        if not data:
            return None

        data.sort(
            key=lambda x: num(
                x.get(
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
# ANALYSIS
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

    p5 = num(change.get("m5"))
    p1h = num(change.get("h1"))

    v5 = num(volume.get("m5"))
    v1h = num(volume.get("h1"))
    v24 = num(volume.get("h24"))

    liquidity = num(
        liquidity_data.get("usd")
    )

    tx5 = txns.get("m5", {})

    buys5 = int(
        tx5.get("buys", 0) or 0
    )

    sells5 = int(
        tx5.get("sells", 0) or 0
    )

    score = 0
    reasons = []

    # -----------------------------
    # 5M PRICE
    # -----------------------------

    if 5 <= p5 <= 15:

        score += 20
        reasons.append("рост 5м")

    elif 15 < p5 <= 30:

        score += 12
        reasons.append("сильный рост 5м")

    elif p5 > 30:

        score -= 10
        reasons.append("слишком резкий памп")

    elif p5 < -10:

        score -= 25
        reasons.append("падение 5м")

    # -----------------------------
    # 1H
    # -----------------------------

    if 5 <= p1h <= 50:

        score += 15
        reasons.append("здоровый тренд 1ч")

    elif 50 < p1h <= 100:

        score += 5
        reasons.append("сильный рост 1ч")

    elif p1h > 100:

        score -= 20
        reasons.append("токен уже сильно вырос")

    elif p1h < -40:

        score -= 30
        reasons.append("сильное падение 1ч")

    # -----------------------------
    # LIQUIDITY
    # -----------------------------

    if liquidity >= 100_000:

        score += 15
        reasons.append("высокая ликвидность")

    elif liquidity >= 30_000:

        score += 12
        reasons.append("нормальная ликвидность")

    elif liquidity >= 20_000:

        score += 7
        reasons.append("приемлемая ликвидность")

    else:

        score -= 15
        reasons.append("низкая ликвидность")

    # -----------------------------
    # BUYS / SELLS
    # -----------------------------

    total = buys5 + sells5

    buy_ratio = 0

    if total >= 10:

        buy_ratio = buys5 / total

        if buy_ratio >= 0.75:

            score += 20
            reasons.append(
                "сильное давление покупателей"
            )

        elif buy_ratio >= 0.65:

            score += 12
            reasons.append(
                "покупателей больше"
            )

        elif buy_ratio >= 0.55:

            score += 5
            reasons.append(
                "покупок немного больше"
            )

        elif buy_ratio <= 0.40:

            score -= 15
            reasons.append(
                "продавцы доминируют"
            )

    # -----------------------------
    # VOLUME ACCELERATION
    # -----------------------------

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
                f"объём ускорился {volume_acceleration:.1f}x"
            )

        elif volume_acceleration >= 2:

            score += 15
            reasons.append(
                f"объём ускоряется {volume_acceleration:.1f}x"
            )

        elif volume_acceleration >= 1.5:

            score += 8
            reasons.append(
                f"объём растёт {volume_acceleration:.1f}x"
            )

    # -----------------------------
    # TRANSACTION ACCELERATION
    # -----------------------------

    old_tx = int(
        old.get("tx5", 0) or 0
    )

    current_tx = total

    tx_acceleration = 1.0

    if old_tx > 0 and current_tx > 0:

        tx_acceleration = (
            current_tx / old_tx
        )

        if tx_acceleration >= 2:

            score += 10
            reasons.append(
                "сделок стало значительно больше"
            )

        elif tx_acceleration >= 1.5:

            score += 5
            reasons.append(
                "число сделок растёт"
            )

    # -----------------------------
    # TURNOVER
    # -----------------------------

    turnover = 0

    if liquidity > 0:

        turnover = (
            v24 / liquidity
        )

    if turnover > 50:

        score -= 10
        reasons.append(
            "аномальный оборот"
        )

    elif turnover >= 2:

        score += 5
        reasons.append(
            "активный оборот"
        )

    # -----------------------------
    # STAGE
    # -----------------------------

    if (
        5 <= p5 <= 20
        and 5 <= p1h <= 50
        and volume_acceleration >= 1.5
        and buy_ratio >= 0.60
    ):

        stage = "🔥 EARLY MOVE"

    elif (
        p5 > 0
        and p1h > 0
        and p1h <= 100
    ):

        stage = "🟢 MID MOVE"

    elif p5 > 0:

        stage = "🟡 WATCH"

    else:

        stage = "⚪ LOW MOMENTUM"

    score = max(
        0,
        min(
            100,
            score
        )
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
        "volume_acceleration":
            volume_acceleration,
        "tx_acceleration":
            tx_acceleration,
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

    now = datetime.now(
        timezone.utc
    ).isoformat()

    checked = 0
    filtered = 0

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

        if liquidity < 10_000:
            filtered += 1
            continue

        if volume < 10_000:
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

        new_state[address] = {
            "name": name,
            "symbol": symbol,
            "v5": result["v5"],
            "tx5":
                result["buys5"]
                + result["sells5"],
            "score": result["score"],
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
            "timestamp": now,
        }

        result["name"] = name
        result["symbol"] = symbol
        result["address"] = address

        results.append(result)

    save_json(
        STATE_FILE,
        new_state
    )

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    print(
        f"Pairs checked: {checked}"
    )

    print(
        f"Filtered: {filtered}"
    )

    print(
        f"Candidates: {len(results)}"
    )

    print("\nTOP CANDIDATES:")

    for item in results[:TOP_RESULTS]:

        print(
            f"{item['symbol']} | "
            f"{item['score']}/100 | "
            f"{item['stage']} | "
            f"5m {item['p5']:+.1f}% | "
            f"1h {item['p1h']:+.1f}% | "
            f"liq {money(item['liquidity'])}"
        )

    # ==========================================
    # ANTI-SPAM
    # ==========================================

    now_ts = datetime.now(
        timezone.utc
    ).timestamp()

    strong = []

    for item in results:

        if item["score"] < ALERT_SCORE:
            continue

        old = state.get(
            item["address"],
            {}
        )

        last_alert = float(
            old.get(
                "last_alert",
                0
            ) or 0
        )

        last_score = float(
            old.get(
                "last_alert_score",
                0
            ) or 0
        )

        cooldown_passed = (
            now_ts - last_alert
            >= ALERT_COOLDOWN
        )

        score_improved = (
            item["score"]
            >= last_score
            + RE_ALERT_SCORE_INCREASE
        )

        if (
            last_alert == 0
            or cooldown_passed
            or score_improved
        ):

            strong.append(item)

            # Обновляем состояние
            new_state[
                item["address"]
            ]["last_alert"] = now_ts

            new_state[
                item["address"]
            ]["last_alert_score"] = (
                item["score"]
            )

    save_json(
        STATE_FILE,
        new_state
    )

    # ==========================================
    # SIGNAL HISTORY
    # ==========================================

    for item in strong:

        signals.append({
            "timestamp": now,
            "address":
                item["address"],
            "symbol":
                item["symbol"],
            "stage":
                item["stage"],
            "score":
                item["score"],
            "p5":
                item["p5"],
            "p1h":
                item["p1h"],
            "v5":
                item["v5"],
            "liquidity":
                item["liquidity"],
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
# TELEGRAM
# ==================================================

def format_signal(item):

    reasons = ", ".join(
        item["reasons"][:5]
    )

    return (
        f"{item['stage']}\n\n"

        f"🚀 {item['name']} "
        f"({item['symbol']})\n"

        f"⭐ Momentum: "
        f"{item['score']}/100\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n\n"

        f"⚡ Volume 5m: "
        f"{money(item['v5'])}\n"

        f"⚡ Volume acceleration: "
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

        f"🔗 `{item['address']}`"
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

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=15
    )

    print(
        "Telegram:",
        response.status_code,
        response.text[:300]
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
