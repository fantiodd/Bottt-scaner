import os
import requests
import time

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes


TOKEN = os.environ["BOT_TOKEN"]

PROFILES_API = "https://api.dexscreener.com/token-profiles/latest/v1"
TOKEN_PAIRS_API = "https://api.dexscreener.com/token-pairs/v1/solana/{}"


def num(value):
    try:
        return float(value or 0)
    except:
        return 0.0


def money(value):
    value = num(value)

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.1f}K"

    return f"${value:.0f}"


def percent(value):
    value = num(value)
    return f"{value:+.1f}%"


def score_pair(pair):
    score = 0
    reasons = []

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

    total_tx = buys + sells

    # ------------------------------------------------
    # 1. PRICE MOMENTUM
    # ------------------------------------------------

    if p5 >= 20:
        score += 25
        reasons.append("🔥 сильный импульс 5м")

    elif p5 >= 10:
        score += 18
        reasons.append("рост 5м")

    elif p5 >= 5:
        score += 10
        reasons.append("положительная динамика")

    # ------------------------------------------------
    # 2. 1 HOUR MOMENTUM
    # ------------------------------------------------

    if p1h >= 50:
        score += 15
        reasons.append("сильный рост за 1ч")

    elif p1h >= 20:
        score += 10
        reasons.append("рост за 1ч")

    # ------------------------------------------------
    # 3. VOLUME
    # ------------------------------------------------

    if v1h >= 250_000:
        score += 15
        reasons.append("высокий объём")

    elif v1h >= 50_000:
        score += 8
        reasons.append("заметный объём")

    # ------------------------------------------------
    # 4. VOLUME / LIQUIDITY
    # ------------------------------------------------

    if liquidity > 0:

        ratio = v24 / liquidity

        if ratio >= 5:
            score += 15
            reasons.append("очень высокий оборот")

        elif ratio >= 2:
            score += 10
            reasons.append("высокий оборот")

        elif ratio >= 1:
            score += 5
            reasons.append("активная торговля")

    # ------------------------------------------------
    # 5. BUY / SELL PRESSURE
    # ------------------------------------------------

    if total_tx > 0:

        buy_ratio = buys / total_tx

        if buy_ratio >= 0.70:
            score += 15
            reasons.append("покупатели доминируют")

        elif buy_ratio >= 0.60:
            score += 8
            reasons.append("покупок больше продаж")

        elif buy_ratio <= 0.35:
            score -= 10
            reasons.append("много продаж")

    # ------------------------------------------------
    # 6. LIQUIDITY
    # ------------------------------------------------

    if liquidity >= 100_000:
        score += 10
        reasons.append("хорошая ликвидность")

    elif liquidity >= 25_000:
        score += 6
        reasons.append("нормальная ликвидность")

    elif liquidity < 10_000:
        score -= 20
        reasons.append("⚠️ низкая ликвидность")

    # ------------------------------------------------
    # 7. TOO FEW TRANSACTIONS
    # ------------------------------------------------

    if total_tx < 10:
        score -= 15
        reasons.append("мало сделок")

    # ------------------------------------------------
    # LIMIT
    # ------------------------------------------------

    score = max(0, min(100, score))

    return score, reasons


def analyse_pair(pair):

    base = pair.get("baseToken", {})

    symbol = base.get("symbol", "?")
    name = base.get("name", "Unknown")
    address = base.get("address", "")

    price = num(pair.get("priceUsd"))

    change = pair.get("priceChange", {})
    p5 = num(change.get("m5"))
    p1h = num(change.get("h1"))

    volume = pair.get("volume", {})

    v5 = num(volume.get("m5"))
    v1h = num(volume.get("h1"))
    v24 = num(volume.get("h24"))

    liquidity = num(
        pair.get("liquidity", {}).get("usd")
    )

    txns = pair.get("txns", {}).get("h1", {})

    buys = int(txns.get("buys", 0) or 0)
    sells = int(txns.get("sells", 0) or 0)

    score, reasons = score_pair(pair)

    return {
        "name": name,
        "symbol": symbol,
        "address": address,
        "price": price,
        "p5": p5,
        "p1h": p1h,
        "v5": v5,
        "v1h": v1h,
        "v24": v24,
        "liquidity": liquidity,
        "buys": buys,
        "sells": sells,
        "score": score,
        "reasons": reasons,
        "url": pair.get("url", "")
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "🤖 Token Scanner v3\n\n"
        "🔥 Новый сканер Solana-токенов\n\n"
        "/scan — найти самые интересные движения\n"
        "/help — помощь"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "📋 Команды:\n\n"
        "/scan — сканирование новых токенов\n"
        "/help — помощь"
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = await update.message.reply_text(
        "🔎 Сканирую Solana...\n\n"
        "1️⃣ Получаю новые токены\n"
        "2️⃣ Проверяю торговые пары\n"
        "3️⃣ Анализирую объём\n"
        "4️⃣ Считаю давление покупателей\n"
        "5️⃣ Формирую рейтинг"
    )

    try:

        # ============================================
        # GET LATEST TOKEN PROFILES
        # ============================================

        response = requests.get(
            PROFILES_API,
            timeout=15
        )

        if response.status_code != 200:

            await message.edit_text(
                f"❌ Profiles API: {response.status_code}"
            )

            return

        profiles = response.json()

        solana_profiles = [
            x for x in profiles
            if x.get("chainId") == "solana"
            and x.get("tokenAddress")
        ]

        # ограничиваем количество API запросов
        solana_profiles = solana_profiles[:30]

        analysed = []

        # ============================================
        # GET PAIRS
        # ============================================

        for profile in solana_profiles:

            address = profile["tokenAddress"]

            try:

                r = requests.get(
                    TOKEN_PAIRS_API.format(address),
                    timeout=10
                )

                if r.status_code != 200:
                    continue

                data = r.json()

                pairs = data if isinstance(data, list) else []

                if not pairs:
                    continue

                # выбираем наиболее ликвидную пару
                pairs.sort(
                    key=lambda p: num(
                        p.get("liquidity", {}).get("usd")
                    ),
                    reverse=True
                )

                pair = pairs[0]

                # ====================================
                # BASIC FILTERS
                # ====================================

                base = pair.get("baseToken", {})

                symbol = (
                    base.get("symbol", "")
                    .upper()
                )

                # исключаем крупные/стабильные активы
                blocked = {
                    "SOL",
                    "WSOL",
                    "USDC",
                    "USDT",
                    "USD1",
                    "USDE"
                }

                if symbol in blocked:
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

                # совсем маленький мусор
                if liquidity < 5_000:
                    continue

                if volume < 5_000:
                    continue

                result = analyse_pair(pair)

                analysed.append(result)

            except Exception as e:

                print(
                    "PAIR ERROR:",
                    address,
                    e
                )

            # не создаём слишком много запросов мгновенно
            time.sleep(0.15)

        # ============================================
        # SORT
        # ============================================

        analysed.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        analysed = analysed[:8]

        if not analysed:

            await message.edit_text(
                "😕 Сейчас подходящих токенов не найдено."
            )

            return

        # ============================================
        # FORMAT
        # ============================================

        text = "🔥 SOLANA TOKEN SCANNER\n\n"

        for i, token in enumerate(
            analysed,
            1
        ):

            reasons = ", ".join(
                token["reasons"][:3]
            )

            text += (
                f"{i}. 🚀 "
                f"{token['name']} "
                f"({token['symbol']})\n"

                f"⭐ Score: "
                f"{token['score']}/100\n"

                f"💵 Price: "
                f"${token['price']:.8f}\n"

                f"📈 5m: "
                f"{percent(token['p5'])} | "
                f"1h: "
                f"{percent(token['p1h'])}\n"

                f"📊 Volume 1h: "
                f"{money(token['v1h'])}\n"

                f"💰 Volume 24h: "
                f"{money(token['v24'])}\n"

                f"💧 Liquidity: "
                f"{money(token['liquidity'])}\n"

                f"🟢 Buys: "
                f"{token['buys']} | "
                f"🔴 Sells: "
                f"{token['sells']}\n"

                f"🧠 {reasons}\n"

                f"🔗 `{token['address']}`\n\n"
            )

        text += (
            "⚠️ Score — это только алгоритмическая "
            "оценка активности, а не гарантия роста."
        )

        await message.edit_text(
            text,
            parse_mode="Markdown"
        )

    except Exception as e:

        print(
            "SCAN ERROR:",
            e
        )

        await message.edit_text(
            "❌ Ошибка сканирования.\n\n"
            f"`{str(e)[:300]}`",
            parse_mode="Markdown"
        )


def main():

    app = (
        Application
        .builder()
        .token(TOKEN)
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
            scan
        )
    )

    print(
        "Token Scanner v3 started"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
