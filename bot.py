import os
import json
import requests
from datetime import datetime, timezone

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ.get("CHAT_ID")

STATE_FILE = "state.json"
SIGNALS_FILE = "signals.json"

TOP_RESULTS = 10

MIN_LIQUIDITY = 10_000
MIN_VOLUME_24H = 10_000

BREAKOUT_DELTA = 30
BREAKOUT_MIN_5M = 5
BREAKOUT_MIN_1H = 0
BREAKOUT_MIN_LIQUIDITY = 20_000

RE_ALERT_SCORE_INCREASE = 15
ALERT_COOLDOWN = 30 * 60

# Через сколько минут проверяем результат сигнала
TRACK_MINUTES = [5, 15, 30]

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
# TOKEN SOURCES
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

                address = item.get("tokenAddress")

                if address:
                    addresses.add(address)

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


# ==================================================
# CLASSIFICATION
# ==================================================

def classify(p5, p1h, liquidity, score):

    if p1h >= 150:
        return "🔴 OVEREXTENDED"

    if p1h >= 100 and p5 >= 5:
        return "🔴 OVEREXTENDED"

    if (
        score >= 85
        and p5 > 0
        and p1h > 0
        and p1h < 100
        and liquidity >= 20_000
    ):
        return "🚨 VERY STRONG"

    if (
        score >= 75
        and p5 > 0
        and p1h > 0
        and p1h < 100
        and liquidity >= 20_000
    ):
        return "🔥 STRONG"

    if (
        score >= 65
        and p5 > 0
        and p1h > 0
        and p1h < 100
        and liquidity >= 20_000
    ):
        return "🟢 EARLY"

    if score >= 40 and p5 > 0 and p1h > 0:
        return "🟡 WATCH"

    return "⚪ LOW MOMENTUM"


# ==================================================
# ANALYZE
# ==================================================

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

    price = num(
        pair.get("priceUsd")
    )

    tx5 = txns.get("m5", {})

    buys5 = int(
        tx5.get("buys", 0) or 0
    )

    sells5 = int(
        tx5.get("sells", 0) or 0
    )

    total_tx = buys5 + sells5

    buy_ratio = 0

    if total_tx > 0:
        buy_ratio = buys5 / total_tx

    score = 0
    reasons = []

    # ==================================================
    # 5M
    # ==================================================

    if 3 <= p5 <= 10:

        score += 20
        reasons.append("умеренный рост 5м")

    elif 10 < p5 <= 20:

        score += 15
        reasons.append("сильный импульс 5м")

    elif 20 < p5 <= 30:

        score += 5
        reasons.append("резкий рост 5м")

    elif p5 > 30:

        score -= 15
        reasons.append("слишком резкий памп")

    elif p5 < -10:

        score -= 25
        reasons.append("падение 5м")

    # ==================================================
    # 1H
    # ==================================================

    if 5 <= p1h <= 30:

        score += 20
        reasons.append("здоровый тренд 1ч")

    elif 30 < p1h <= 60:

        score += 10
        reasons.append("сильный тренд 1ч")

    elif 60 < p1h < 100:

        score += 3
        reasons.append("сильный рост 1ч")

    elif p1h >= 100:

        score -= 25
        reasons.append("токен уже сильно вырос")

    elif p1h < -30:

        score -= 20
        reasons.append("негативный тренд 1ч")

    # ==================================================
    # LIQUIDITY
    # ==================================================

    if liquidity >= 100_000:

        score += 15
        reasons.append("высокая ликвидность")

    elif liquidity >= 50_000:

        score += 13
        reasons.append("хорошая ликвидность")

    elif liquidity >= 30_000:

        score += 10
        reasons.append("нормальная ликвидность")

    elif liquidity >= 20_000:

        score += 5
        reasons.append("приемлемая ликвидность")

    else:

        score -= 20
        reasons.append("низкая ликвидность")

    # ==================================================
    # BUY / SELL
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

    old_v5 = num(old.get("v5"))

    volume_acceleration = 1.0

    if old_v5 > 0 and v5 > 0:

        volume_acceleration = v5 / old_v5

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
    # TX ACCELERATION
    # ==================================================

    old_tx = int(
        old.get("tx5", 0) or 0
    )

    tx_acceleration = 1.0

    if old_tx > 0 and total_tx > 0:

        tx_acceleration = total_tx / old_tx

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
        turnover = v24 / liquidity

    if turnover >= 2:

        score += 5
        reasons.append("активный оборот")

    if turnover > 50:

        score -= 10
        reasons.append(
            "аномальный оборот"
        )

    score = max(
        0,
        min(100, score)
    )

    stage = classify(
        p5,
        p1h,
        liquidity,
        score
    )

    # ==================================================
    # QUALITY SCORE
    # ==================================================

    quality = 50

    # Текущий momentum
    quality += min(
        15,
        max(0, score * 0.15)
    )

    # Покупатели
    if total_tx >= 10:

        if buy_ratio >= 0.70:
            quality += 15

        elif buy_ratio >= 0.60:
            quality += 8

        elif buy_ratio < 0.45:
            quality -= 15

    # Ликвидность
    if liquidity >= 100_000:
        quality += 12

    elif liquidity >= 50_000:
        quality += 8

    elif liquidity >= 30_000:
        quality += 5

    elif liquidity < 15_000:
        quality -= 15

    # Ускорение объёма
    if volume_acceleration >= 3:
        quality += 10

    elif volume_acceleration >= 2:
        quality += 7

    elif volume_acceleration >= 1.5:
        quality += 4

    # Ускорение транзакций
    if tx_acceleration >= 2:
        quality += 8

    elif tx_acceleration >= 1.5:
        quality += 4

    # Штраф за перегрев
    if p1h >= 100:
        quality -= 25

    elif p1h >= 70:
        quality -= 10

    if p5 > 30:
        quality -= 15

    # Низкая ликвидность
    if liquidity < 20_000:
        quality -= 15

    # Аномальный оборот
    if turnover > 50:
        quality -= 10

    quality = int(
        max(
            0,
            min(100, quality)
        )
    )

    return {
        "score": score,
        "quality": quality,
        "stage": stage,
        "price": price,
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
# SIGNAL TYPE
# ==================================================

def get_signal_type(item):

    if item["stage"] == "🔴 OVEREXTENDED":
        return None

    score = item["score"]
    delta = item["score_delta"]

    p5 = item["p5"]
    p1h = item["p1h"]
    liquidity = item["liquidity"]

    breakout = (
        delta >= BREAKOUT_DELTA
        and p5 >= BREAKOUT_MIN_5M
        and p1h > BREAKOUT_MIN_1H
        and liquidity >= BREAKOUT_MIN_LIQUIDITY
        and p1h < 150
    )

    if breakout:

        if score >= 85:
            return "🚀 BREAKOUT + 🚨 VERY STRONG"

        if score >= 75:
            return "🚀 BREAKOUT + 🔥 STRONG"

        if score >= 65:
            return "🚀 BREAKOUT + 🟢 EARLY"

        return "🚀 BREAKOUT"

    if score >= 85:
        return "🚨 VERY STRONG"

    if score >= 75:
        return "🔥 STRONG"

    if score >= 65:
        return "🟢 EARLY"

    return None


# ==================================================
# TRACK OLD SIGNALS
# ==================================================

def update_tracking(state, results, now_ts):

    stats_changed = False

    result_map = {
        x["address"]: x
        for x in results
    }

    for address, data in state.items():

        tracking = data.get(
            "tracking"
        )

        if not tracking:
            continue

        item = result_map.get(address)

        if not item:
            continue

        current_price = item["price"]

        if current_price <= 0:
            continue

        start_price = num(
            tracking.get(
                "price",
                0
            )
        )

        if start_price <= 0:
            continue

        elapsed = (
            now_ts
            - num(
                tracking.get(
                    "timestamp",
                    now_ts
                )
            )
        ) / 60

        change = (
            (current_price / start_price)
            - 1
        ) * 100

        tracking["current_price"] = (
            current_price
        )

        tracking["last_change"] = (
            change
        )

        # 5 минут
        if (
            elapsed >= 5
            and not tracking.get(
                "checked_5m"
            )
        ):

            tracking["change_5m"] = (
                change
            )

            tracking["checked_5m"] = True

            stats_changed = True

        # 15 минут
        if (
            elapsed >= 15
            and not tracking.get(
                "checked_15m"
            )
        ):

            tracking["change_15m"] = (
                change
            )

            tracking["checked_15m"] = True

            stats_changed = True

        # 30 минут
        if (
            elapsed >= 30
            and not tracking.get(
                "checked_30m"
            )
        ):

            tracking["change_30m"] = (
                change
            )

            tracking["checked_30m"] = True

            stats_changed = True

        if (
            tracking.get("checked_30m")
        ):

            result = tracking.get(
                "change_30m"
            )

            data.setdefault(
                "completed_stats",
                []
            ).append({

                "signal_type":
                    tracking.get(
                        "signal_type"
                    ),

                "quality":
                    tracking.get(
                        "quality"
                    ),

                "change_5m":
                    tracking.get(
                        "change_5m"
                    ),

                "change_15m":
                    tracking.get(
                        "change_15m"
                    ),

                "change_30m":
                    result,

                "timestamp":
                    tracking.get(
                        "timestamp"
                    ),
            })

            data["completed_stats"] = (
                data["completed_stats"][-100:]
            )

            data.pop(
                "tracking",
                None
            )

            stats_changed = True

    return stats_changed


# ==================================================
# STATISTICS
# ==================================================

def calculate_stats(state):

    completed = []

    for data in state.values():

        completed.extend(
            data.get(
                "completed_stats",
                []
            )
        )

    if not completed:
        return None

    total = len(completed)

    positive_5 = sum(
        1
        for x in completed
        if num(
            x.get("change_5m")
        ) > 0
    )

    positive_15 = sum(
        1
        for x in completed
        if num(
            x.get("change_15m")
        ) > 0
    )

    positive_30 = sum(
        1
        for x in completed
        if num(
            x.get("change_30m")
        ) > 0
    )

    avg_30 = sum(
        num(
            x.get("change_30m")
        )
        for x in completed
    ) / total

    return {
        "total": total,
        "positive_5m": positive_5,
        "positive_15m": positive_15,
        "positive_30m": positive_30,
        "avg_30m": avg_30,
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

    new_state = dict(state)

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

            "price":
                result["price"],

            "p5":
                result["p5"],

            "p1h":
                result["p1h"],

        })

        history = history[-30:]

        new_state[address] = {

            "name":
                name,

            "symbol":
                symbol,

            "score":
                result["score"],

            "quality":
                result["quality"],

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

            "tracking":
                old.get(
                    "tracking"
                ),

            "completed_stats":
                old.get(
                    "completed_stats",
                    []
                ),

            "timestamp":
                now_iso,
        }

        results.append(
            result
        )

    # ==================================================
    # UPDATE TRACKING
    # ==================================================

    update_tracking(
        new_state,
        results,
        now_ts
    )

    # ==================================================
    # SORT
    # ==================================================

    results.sort(
        key=lambda x: (
            x["quality"],
            x["score"],
            x["score_delta"]
        ),
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
            f"liq {money(item['liquidity'])}"
        )

    # ==================================================
    # SIGNALS
    # ==================================================

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
            >= last_alert_score
            + RE_ALERT_SCORE_INCREASE
        )

        new_signal_type = (
            signal_type != last_alert_type
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

        # Начинаем отслеживание
        if item["price"] > 0:

            new_state[
                item["address"]
            ]["tracking"] = {

                "timestamp":
                    now_ts,

                "price":
                    item["price"],

                "signal_type":
                    signal_type,

                "quality":
                    item["quality"],

                "score":
                    item["score"],

                "change_5m":
                    None,

                "change_15m":
                    None,

                "change_30m":
                    None,

                "checked_5m":
                    False,

                "checked_15m":
                    False,

                "checked_30m":
                    False,
            }

        item["signal_type"] = (
            signal_type
        )

    # ==================================================
    # SAVE
    # ==================================================

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

            "score":
                item["score"],

            "quality":
                item["quality"],

            "score_delta":
                item["score_delta"],

            "stage":
                item["signal_type"],

            "price":
                item["price"],

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

    # ==================================================
    # STATS
    # ==================================================

    stats = calculate_stats(
        new_state
    )

    if stats:

        print(
            "\nTRACKING STATS:"
        )

        print(
            f"Completed: "
            f"{stats['total']}"
        )

        print(
            f"Positive 5m: "
            f"{stats['positive_5m']}/"
            f"{stats['total']}"
        )

        print(
            f"Positive 15m: "
            f"{stats['positive_15m']}/"
            f"{stats['total']}"
        )

        print(
            f"Positive 30m: "
            f"{stats['positive_30m']}/"
            f"{stats['total']}"
        )

        print(
            f"Average 30m: "
            f"{stats['avg_30m']:+.2f}%"
        )

    print(
        f"Strong signals: "
        f"{len(signal_items)}"
    )

    return signal_items


# ==================================================
# TELEGRAM
# ==================================================

def format_signal(item):

    reasons = "\n".join(
        "• " + x
        for x in item["reasons"][:6]
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

        f"🎯 QUALITY: "
        f"{item['quality']}/100\n"

        f"⭐ MOMENTUM: "
        f"{item['score']}/100\n"

        f"📊 SCORE Δ: "
        f"{item['score_delta']:+.0f}\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n\n"

        f"🟢 Buys: "
        f"{item['buys5']}\n"

        f"🔴 Sells: "
        f"{item['sells5']}\n\n"

        f"⚡ Volume acceleration: "
        f"{item['volume_acceleration']:.1f}x\n"

        f"🔄 TX acceleration: "
        f"{item['tx_acceleration']:.1f}x\n\n"

        f"🧠 WHY:\n"
        f"{reasons}\n\n"

        f"🔎 [DexScreener]"
        f"({dex_url})\n\n"

        f"📋 `{address}`"
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
