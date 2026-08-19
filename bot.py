import os
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

PROFILES_API = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_PAIRS_API = "https://api.dexscreener.com/token-pairs/v1/solana/{}"

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

BLOCKED = {
    "SOL",
    "WSOL",
    "USDC",
    "USDT",
    "USD1",
    "USDE",
}


def num(x):
    try:
        return float(x or 0)
    except:
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
    except:
        return default


def save_json(filename, data):
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def get_profiles():

    r = requests.get(
        PROFILES_API,
        timeout=15
    )

    r.raise_for_status()

    data = r.json()

    return [
        x for x in data
        if x.get("chainId") == "solana"
        and x.get("tokenAddress")
    ]


def get_pair(address):

    try:

        r = requests.get(
            TOKEN_PAIRS_API.format(address),
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
            key=lambda p: num(
                p.get("liquidity", {}).get("usd")
            ),
            reverse=True
        )

        return data[0]

    except:
        return None


def analyse(pair, old):

    change = pair.get("priceChange", {})
    volume = pair.get("volume", {})
    liquidity_data = pair.get("liquidity", {})
    txns = pair.get("txns", {})

    p5 = num(change.get("m5"))
    p1h = num(change.get("h1"))

    v5 = num(volume.get("m5"))
    v1h = num(volume.get("h1"))
    v24 = num(volume.get("h24"))

    liquidity = num(
        liquidity_data.get("usd")
    )

    tx5 = txns.get("m5", {})
    tx1 = txns.get("h1", {})

    buys5 = int(tx5.get("buys", 0) or 0)
    sells5 = int(tx5.get("sells", 0) or 0)

    buys1 = int(tx1.get("buys", 0) or 0)
    sells1 = int(tx1.get("sells", 0) or 0)

    score = 0
    reasons = []

    # ============================================
    # PRICE
    # ============================================

    if 5 <= p5 <= 20:
        score += 20
        reasons.append("рост 5м")

    elif 20 < p5 <= 35:
        score += 10
        reasons.append("сильный рост 5м")

    elif p5 > 35:
        score -= 10
        reasons.append("слишком резкий памп")

    elif p5 < -10:
        score -= 30
        reasons.append("падение 5м")

    # ============================================
    # 1H TREND
    # ============================================

    if 5 <= p1h <= 50:
        score += 15
        reasons.append("здоровый тренд 1ч")

    elif 50 < p1h <= 100:
        score += 5
        reasons.append("уже сильный рост")

    elif p1h > 100:
        score -= 20
        reasons.append("движение уже сильно разогналось")

    elif p1h < -40:
        score -= 30
        reasons.append("сильное падение")

    # ============================================
    # LIQUIDITY
    # ============================================

    if liquidity >= 100_000:
        score += 15

    elif liquidity >= 30_000:
        score += 12
        reasons.append("нормальная ликвидность")

    elif liquidity >= 20_000:
        score += 7

    else:
        score -= 20
        reasons.append("низкая ликвидность")

    # ============================================
    # BUY PRESSURE 5 MIN
    # ============================================

    total5 = buys5 + sells5

    buy_ratio = 0

    if total5 >= 10:

        buy_ratio = buys5 / total5

        if buy_ratio >= 0.75:
            score += 20
            reasons.append("сильное давление покупателей")

        elif buy_ratio >= 0.65:
            score += 12
            reasons.append("покупателей больше")

        elif buy_ratio <= 0.40:
            score -= 15
            reasons.append("продавцы доминируют")

    # ============================================
    # VOLUME ACCELERATION
    # ============================================

    volume_acceleration = 1.0

    old_v5 = num(
        old.get("v5")
    )

    if old_v5 > 0:

        volume_acceleration = v5 / old_v5

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

    # ============================================
    # TRANSACTION ACCELERATION
    # ============================================

    old_tx = int(
        old.get("tx5", 0) or 0
    )

    current_tx = buys5 + sells5

    tx_acceleration = 1.0

    if old_tx > 0:

        tx_acceleration = current_tx / old_tx

        if tx_acceleration >= 2:
            score += 10
            reasons.append(
                "количество сделок ускоряется"
            )

    # ============================================
    # VOLUME / LIQUIDITY
    # ============================================

    turnover = 0

    if liquidity > 0:
        turnover = v24 / liquidity

    if turnover > 50:
        score -= 10
        reasons.append("аномальный оборот")

    elif turnover >= 2:
        score += 5

    # ============================================
    # STAGE
    # ============================================

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

    else:
        stage = "🟡 WATCH"

    # ============================================
    # FINAL SCORE
    # ============================================

    score = max(
        0,
        min(
            100,
            score
        )
    )

    # EARLY требует достаточно хорошего score
    if stage == "🔥 EARLY MOVE" and score < 60:
        stage = "🟡 WATCH"

    if stage == "🟢 MID MOVE" and score < 50:
        stage = "🟡 WATCH"

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

        "buys1": buys1,
        "sells1": sells1,

        "volume_acceleration":
            volume_acceleration,

        "tx_acceleration":
            tx_acceleration,

        "reasons": reasons,
    }


def scan():

    state = load_json(
        STATE_FILE,
        {}
    )

    signals = load_json(
        SIGNALS_FILE,
        []
    )

    profiles = get_profiles()

    results = []
    new_state = {}

    now = datetime.now(
        timezone.utc
    ).isoformat()

    for profile in profiles[:30]:

        address = profile["tokenAddress"]

        pair = get_pair(address)

        if not pair:
            continue

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
            continue

        if volume < 10_000:
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

            "price":
                num(pair.get("priceUsd")),

            "timestamp": now,
        }

        if result["score"] >= 50:

            result["name"] = name
            result["symbol"] = symbol
            result["address"] = address

            results.append(
                result
            )

    # ============================================
    # SAVE STATE
    # ============================================

    save_json(
        STATE_FILE,
        new_state
    )

    # ============================================
    # SORT
    # ============================================

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # ============================================
    # SIGNAL HISTORY
    # ============================================

    for item in results:

        signals.append({
            "timestamp": now,
            "address": item["address"],
            "symbol": item["symbol"],
            "stage": item["stage"],
            "score": item["score"],
            "price": item["p5"],
            "v5": item["v5"],
            "liquidity": item["liquidity"],
        })

    # оставляем последние 1000 сигналов
    signals = signals[-1000:]

    save_json(
        SIGNALS_FILE,
        signals
    )

    return results[:5]


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
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=15
    )


def main():

    try:

        results = scan()

        if not results:

            print(
                "No interesting signals."
            )

            return

        # Отправляем максимум 5 сигналов
        # за один запуск.

        for item in results:

            send_telegram(
                format_signal(item)
            )

        print(
            f"Sent {len(results)} signals."
        )

    except Exception as e:

        print(
            "FATAL ERROR:",
            repr(e)
        )

        raise


if __name__ == "__main__":
    main()
