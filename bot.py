import os
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ["BOT_TOKEN"]

DEX_API = "https://api.dexscreener.com/latest/dex/search"


def safe_float(value):
    try:
        return float(value or 0)
    except:
        return 0.0


def calculate_score(pair):
    score = 0
    reasons = []

    price5m = safe_float(pair.get("priceChange", {}).get("m5"))
    price1h = safe_float(pair.get("priceChange", {}).get("h1"))

    volume = safe_float(pair.get("volume", {}).get("h24"))
    liquidity = safe_float(pair.get("liquidity", {}).get("usd"))

    txns = pair.get("txns", {}).get("h1", {})
    buys = int(txns.get("buys", 0) or 0)
    sells = int(txns.get("sells", 0) or 0)

    # Цена
    if price5m >= 10:
        score += 20
        reasons.append("сильный рост за 5м")
    elif price5m >= 5:
        score += 12
        reasons.append("рост за 5м")

    if price1h >= 20:
        score += 20
        reasons.append("сильный рост за 1ч")
    elif price1h >= 10:
        score += 12
        reasons.append("рост за 1ч")

    # Ликвидность
    if liquidity >= 100_000:
        score += 15
        reasons.append("хорошая ликвидность")
    elif liquidity >= 25_000:
        score += 8
        reasons.append("приемлемая ликвидность")

    # Объём
    if volume >= 1_000_000:
        score += 20
        reasons.append("очень большой объём")
    elif volume >= 250_000:
        score += 12
        reasons.append("высокий объём")
    elif volume >= 50_000:
        score += 6
        reasons.append("есть заметный объём")

    # Покупки / продажи
    total = buys + sells

    if total > 0:
        buy_ratio = buys / total

        if buy_ratio >= 0.70:
            score += 20
            reasons.append("покупок значительно больше продаж")
        elif buy_ratio >= 0.60:
            score += 10
            reasons.append("покупок больше продаж")

    # Штраф за очень маленькую ликвидность
    if liquidity < 10_000:
        score -= 20
        reasons.append("⚠️ очень низкая ликвидность")

    # Ограничиваем score
    score = max(0, min(score, 100))

    return score, reasons


def format_money(value):
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.0f}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Token Scanner v2\n\n"
        "/scan — поиск интересных токенов\n"
        "/help — помощь"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команды:\n\n"
        "/scan — сканирование Solana\n"
        "/help — помощь"
    )


async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = await update.message.reply_text(
        "🔎 Сканирую Solana...\n"
        "Анализирую цену, объём, ликвидность и сделки."
    )

    try:
        response = requests.get(
            DEX_API,
            params={"q": "SOL"},
            timeout=15
        )

        if response.status_code != 200:
            await message.edit_text(
                f"❌ DexScreener API вернул {response.status_code}"
            )
            return

        data = response.json()
        pairs = data.get("pairs", [])

        # Только Solana
        pairs = [
            p for p in pairs
            if p.get("chainId") == "solana"
        ]

        results = []

        for pair in pairs:
            liquidity = safe_float(
                pair.get("liquidity", {}).get("usd")
            )

            volume = safe_float(
                pair.get("volume", {}).get("h24")
            )

            # Отбрасываем совсем мусорные пары
            if liquidity < 5_000 or volume < 5_000:
                continue

            score, reasons = calculate_score(pair)

            results.append({
                "pair": pair,
                "score": score,
                "reasons": reasons
            })

        # Сначала лучшие
        results.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        results = results[:7]

        if not results:
            await message.edit_text(
                "😕 Подходящих токенов сейчас не найдено."
            )
            return

        text = "🔥 TOP SOLANA MOVES\n\n"

        for i, item in enumerate(results, 1):
            pair = item["pair"]
            score = item["score"]
            reasons = item["reasons"]

            base = pair.get("baseToken", {})
            name = base.get("name", "Unknown")
            symbol = base.get("symbol", "?")
            address = base.get("address", "")

            price = safe_float(pair.get("priceUsd"))

            price5m = safe_float(
                pair.get("priceChange", {}).get("m5")
            )

            price1h = safe_float(
                pair.get("priceChange", {}).get("h1")
            )

            volume = safe_float(
                pair.get("volume", {}).get("h24")
            )

            liquidity = safe_float(
                pair.get("liquidity", {}).get("usd")
            )

            txns = pair.get("txns", {}).get("h1", {})
            buys = int(txns.get("buys", 0) or 0)
            sells = int(txns.get("sells", 0) or 0)

            text += (
                f"{i}. 🚀 {name} ({symbol})\n"
                f"⭐ Score: {score}/100\n"
                f"💵 Price: ${price:.8f}\n"
                f"📈 5m: {price5m:+.1f}% | "
                f"1h: {price1h:+.1f}%\n"
                f"💰 Volume 24h: {format_money(volume)}\n"
                f"💧 Liquidity: {format_money(liquidity)}\n"
                f"🟢 Buys: {buys} | 🔴 Sells: {sells}\n"
                f"🧠 {', '.join(reasons[:3])}\n"
                f"🔗 `{address}`\n\n"
            )

        await message.edit_text(
            text,
            parse_mode="Markdown"
        )

    except Exception as e:
        print("ERROR:", e)

        await message.edit_text(
            "❌ Ошибка при анализе рынка.\n"
            "Попробуй ещё раз через несколько секунд."
        )


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CommandHandler("help", help_command)
    )

    app.add_handler(
        CommandHandler("scan", scan)
    )

    print("Token Scanner v2 started")

    app.run_polling()


if __name__ == "__main__":
    main()
