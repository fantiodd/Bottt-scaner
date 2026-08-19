import os
import json
import requests
from datetime import datetime, timezone

from telegram import Bot, Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]

PROFILES_API = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_PAIRS_API = "https://api.dexscreener.com/token-pairs/v1/solana/{}"

STATE_FILE = "state.json"

BLOCKED_SYMBOLS = {
    "SOL",
    "WSOL",
    "USDC",
    "USDT",
    "USD1",
    "USDE",
}


def num(value):
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def money(value):
    value = num(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def analyse(pair, previous=None):
    change = pair.get("priceChange", {})
    volume = pair.get("volume", {})
    liquidity_data = pair.get("liquidity", {})
    txns = pair.get("txns", {})

    p5 = num(change.get("m5"))
    p1h = num(change.get("h1"))

    v5 = num(volume.get("m5"))
    v1h = num(volume.get("h1"))
    v24 = num(volume.get("h24"))

    liquidity = num(liquidity_data.get("usd"))

    tx1h = txns.get("h1", {})
    buys = int(tx1h.get("buys", 0) or 0)
    sells = int(tx1h.get("sells", 0) or 0)

    total = buys + sells

    score = 0
    reasons = []

    # -----------------------------
    # PRICE MOMENTUM
    # -----------------------------

    if 5 <= p5 <= 30:
        score += 20
        reasons.append("цена ускоряется")

    elif p5 > 30:
        score += 8
        reasons.append("сильный памп, но уже поздняя стадия")

    elif p5 < -10:
        score -= 25

    # -----------------------------
    # 1H TREND
    # -----------------------------

    if 5 <= p1h <= 60:
        score += 15
        reasons.append("положительный тренд 1ч")

    elif p1h > 60:
        score += 5
        reasons.append("токен уже сильно вырос")

    elif p1h <= -40:
        score -= 30
        reasons.append("сильное падение 1ч")

    # -----------------------------
    # LIQUIDITY
    # -----------------------------

    if liquidity >= 100_000:
        score += 15
        reasons.append("хорошая ликвидность")

    elif liquidity >= 25_000:
        score += 10
        reasons.append("нормальная ликвидность")

    elif liquidity >= 10_000:
        score += 4

    else:
        score -= 20
        reasons.append("низкая ликвидность")

    # -----------------------------
    # BUY / SELL PRESSURE
    # -----------------------------

    if total >= 20:

        buy_ratio = buys / total

        if buy_ratio >= 0.70:
            score += 20
            reasons.append("покупатели доминируют")

        elif buy_ratio >= 0.60:
            score += 12
            reasons.append("покупок больше продаж")

        elif buy_ratio <= 0.40:
            score -= 15
            reasons.append("продавцы доминируют")

    else:
        score -= 5

    # -----------------------------
    # VOLUME / LIQUIDITY
    # -----------------------------

    turnover = 0

    if liquidity > 0:
        turnover = v24 / liquidity

    if 1 <= turnover <= 20:
        score += 10
        reasons.append("активный оборот")

    elif turnover > 50:
        score -= 10
        reasons.append("аномально высокий оборот")

    # -----------------------------
    # VOLUME ACCELERATION
    # -----------------------------

    volume_acceleration = 1.0

    if previous:

        old_v1h = num(previous.get("v1h"))

        if old_v1h > 0:
            volume_acceleration = v1h / old_v1h

            if volume_acceleration >= 3:
                score += 20
                reasons.append(
                    f"объём ускорился {volume_acceleration:.1f}x"
                )

            elif volume_acceleration >= 1.7:
                score += 12
                reasons.append(
                    f"объём растёт {volume_acceleration:.1f}x"
                )

    score = max(0, min(score, 100))

    # -----------------------------
    # CLASSIFICATION
    # -----------------------------

    if score >= 70:
        category = "🔥 EARLY MOVE"

    elif score >= 50:
        category = "🟢 OPPORTUNITY"

    elif score >= 35:
        category = "🟡 WATCH"

    else:
        category = None

    return {
        "score": score,
        "category": category,
        "p5": p5,
        "p1h": p1h,
        "v5": v5,
        "v1h": v1h,
        "v24": v24,
        "liquidity": liquidity,
        "buys": buys,
        "sells": sells,
        "volume_acceleration": volume_acceleration,
        "reasons": reasons,
    }


def get_profiles():
    r = requests.get(
        PROFILES_API,
        timeout=15
    )

    r.raise_for_status()

    data = r.json()

    return [
        x
        for x in data
        if x.get("chainId") == "solana"
        and x.get("tokenAddress")
    ]


def get_pair(address):
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
        key=lambda x: num(
            x.get("liquidity", {}).get("usd")
        ),
        reverse=True
    )

    return data[0]


def scan_market():
    state = load_state()
    new_state = {}

    profiles = get_profiles()

    results = []

    for profile in profiles[:30]:

        address = profile["tokenAddress"]

        try:
            pair = get_pair(address)

            if not pair:
                continue

            base = pair.get("baseToken", {})

            symbol = (
                base.get("symbol", "")
                .upper()
            )

            name = base.get(
                "name",
                "Unknown"
            )

            if symbol in BLOCKED_SYMBOLS:
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

            previous = state.get(address)

            result = analyse(
                pair,
                previous
            )

            # Сохраняем текущее состояние
            new_state[address] = {
                "name": name,
                "symbol": symbol,
                "v1h": result["v1h"],
                "score": result["score"],
                "timestamp": datetime.now(
                    timezone.utc
                ).isoformat()
            }

            if result["category"]:

                result["name"] = name
                result["symbol"] = symbol
                result["address"] = address
                result["url"] = pair.get(
                    "url",
                    ""
                )

                results.append(result)

        except Exception as e:
            print(
                "ERROR:",
                address,
                e
            )

    save_state(new_state)

    results.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return results[:8]


def format_result(item):

    reasons = ", ".join(
        item["reasons"][:4]
    )

    return (
        f"{item['category']}\n\n"

        f"🚀 {item['name']} "
        f"({item['symbol']})\n"

        f"⭐ Momentum: "
        f"{item['score']}/100\n\n"

        f"📈 5m: "
        f"{item['p5']:+.1f}%\n"

        f"📈 1h: "
        f"{item['p1h']:+.1f}%\n\n"

        f"📊 Volume 1h: "
        f"{money(item['v1h'])}\n"

        f"💰 Volume 24h: "
        f"{money(item['v24'])}\n"

        f"💧 Liquidity: "
        f"{money(item['liquidity'])}\n\n"

        f"🟢 Buys: "
        f"{item['buys']}\n"

        f"🔴 Sells: "
        f"{item['sells']}\n\n"

        f"⚡ Volume acceleration: "
        f"{item['volume_acceleration']:.1f}x\n\n"

        f"🧠 {reasons}\n\n"

        f"🔗 `{item['address']}`"
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "🤖 Token Scanner v4\n\n"

        "🔥 Сканер ранних движений Solana\n\n"

        "/scan — ручной скан\n"
        "/help — помощь"
    )


async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "/scan — найти текущие сигналы"
    )


async def scan_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    msg = await update.message.reply_text(
        "🔎 Анализирую рынок..."
    )

    try:

        results = scan_market()

        if not results:

            await msg.edit_text(
                "😴 Сейчас подходящих "
                "движений не обнаружено."
            )

            return

        text = (
            "🔥 SOLANA EARLY MOVES\n\n"
        )

        for item in results:

            text += (
                format_result(item)
                + "\n\n"
                + "────────────\n\n"
            )

        text += (
            "⚠️ Это алгоритмический "
            "сигнал, а не гарантия роста."
        )

        await msg.edit_text(
            text,
            parse_mode="Markdown"
        )

    except Exception as e:

        print("SCAN ERROR:", e)

        await msg.edit_text(
            "❌ Ошибка сканирования:\n"
            f"{str(e)[:300]}"
        )


async def main_async():

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    app.add_handler(
        CommandHandler(
            "help",
            help_command
        )
    )

    app.add_handler(
        CommandHandler(
            "scan",
            scan_command
        )
    )

    await app.initialize()
    await app.start()

    bot = Bot(BOT_TOKEN)

    try:
        results = scan_market()

        # CHAT_ID нужен только для автоматических уведомлений.
        chat_id = os.environ.get(
            "CHAT_ID"
        )

        if chat_id and results:

            for item in results:

                await bot.send_message(
                    chat_id=chat_id,
                    text=format_result(item),
                    parse_mode="Markdown"
                )

    finally:

        await app.stop()
        await app.shutdown()


def main():

    import asyncio

    asyncio.run(
        main_async()
    )


if __name__ == "__main__":
    main()
