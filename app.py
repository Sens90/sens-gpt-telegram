import os
from flask import Flask
from openai import OpenAI
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)

app = Flask(__name__)

@app.route("/")
def home():
    return "Sens GPT - TITANI ABUSIVI ONLINE"

    async def answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message

            if not message or not message.text:
                    return

                        bot_username = context.bot.username

                            # Risponde solo se viene menzionato
                                if f"@{bot_username.lower()}" not in message.text.lower():
                                        return

                                            question = message.text

                                                # Rimuove la menzione del bot
                                                    question = question.replace(f"@{bot_username}", "").strip()

                                                        if not question:
                                                                await message.reply_text(
                                                                            "Sono Sens GPT, l'AI ufficiale dei TITANI ABUSIVI. "
                                                                                        "Fammi una domanda su Brawl Stars."
                                                                                                )
                                                                                                        return

                                                                                                            system_prompt = """
                                                                                                            Sei Sens GPT, l'intelligenza artificiale ufficiale della community TITANI ABUSIVI.

                                                                                                            Sei specializzato soprattutto in Brawl Stars.

                                                                                                            Rispondi sempre in italiano.

                                                                                                            Il tuo stile deve essere:
                                                                                                            - competente
                                                                                                            - diretto
                                                                                                            - chiaro
                                                                                                            - utile
                                                                                                            - amichevole
                                                                                                            - con una personalità coerente con la community TITANI ABUSIVI

                                                                                                            Quando la domanda riguarda Brawl Stars, fornisci consigli
                                                                                                            concreti e spiegazioni semplici.

                                                                                                            Non inventare informazioni quando non sei sicuro.
                                                                                                            """

                                                                                                                try:
                                                                                                                        response = client.responses.create(
                                                                                                                                    model="gpt-5-mini",
                                                                                                                                                instructions=system_prompt,
                                                                                                                                                            input=question
                                                                                                                                                                    )

                                                                                                                                                                            await message.reply_text(response.output_text)

                                                                                                                                                                                except Exception as e:
                                                                                                                                                                                        print("Errore:", e)
                                                                                                                                                                                                await message.reply_text(
                                                                                                                                                                                                            "Ho avuto un problema con il sistema AI. Riprova tra poco."
                                                                                                                                                                                                                    )

                                                                                                                                                                                                                    def main():
                                                                                                                                                                                                                        application = Application.builder().token(TELEGRAM_TOKEN).build()

                                                                                                                                                                                                                            application.add_handler(
                                                                                                                                                                                                                                    MessageHandler(filters.TEXT & ~filters.COMMAND, answer)
                                                                                                                                                                                                                                        )

                                                                                                                                                                                                                                            application.run_polling()

                                                                                                                                                                                                                                            if __name__ == "__main__":
                                                                                                                                                                                                                                                main()