import os
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = os.environ["BOT_TOKEN"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 Token Scanner запущен!\n\n"
        "/scan — найти интересные токены\n"
        "/help — список команд"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Команды:\n\n"
        "/scan — сканирование рынка\n"
        "/help — помощь"
    )

async def scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔎 Сканирую рынок...")

    try:
        response = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=10
        )

        if response.status_code != 200:
            await msg.edit_text("❌ API временно недоступен.")
            return

        data = response.json()

        solana = [
            x for x in data
            if x.get("chainId") == "solana"
        ][:10]

        if not solana:
            await msg.edit_text("😕 Интересных токенов пока не найдено.")
            return

        text = "🔥 Последние токены Solana:\n\n"

        for i, token in enumerate(solana, 1):
            address = token.get("tokenAddress", "unknown")
            text += f"{i}. `{address}`\n"

        await msg.edit_text(text, parse_mode="Markdown")

    except Exception as e:
        await msg.edit_text("❌ Ошибка при сканировании.")

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("scan", scan))

    print("Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
