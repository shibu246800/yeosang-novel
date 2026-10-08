import os
import threading

import discord
import requests
from discord.ext import commands
from flask import Flask


app = Flask(__name__)


@app.route("/")
def home():
    return "Yeosang Novel is alive. 🖤"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")

    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Slash sync failed: {e}")


@bot.tree.command(
    name="novel",
    description="Test Yeosang's novel intelligence."
)
async def novel(interaction: discord.Interaction):

    await interaction.response.defer()

    api_key = os.environ.get("OPENROUTER_API_KEY")

    if not api_key:
        await interaction.followup.send(
            "❌ OPENROUTER_API_KEY is missing."
        )
        return

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "google/gemini-3-flash-preview",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "You are Yeosang, a creative novel-writing AI. "
                            "Reply with exactly one short, imaginative sentence "
                            "to confirm that your story brain is working."
                        ),
                    }
                ],
            },
            timeout=60,
        )

        if not response.ok:
            print(response.text)
            await interaction.followup.send(
                "❌ AI request failed. Check the Render logs."
            )
            return

        data = response.json()
        answer = data["choices"][0]["message"]["content"]

        await interaction.followup.send(
            f"📖 **Yeosang's story brain:**\n\n{answer}"
        )

    except Exception as e:
        print(f"❌ AI error: {e}")
        await interaction.followup.send(
            "❌ Something went wrong while contacting the AI."
        )


if __name__ == "__main__":
    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot.run(token)
