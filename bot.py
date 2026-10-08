import os
import threading

import discord
from discord import app_commands
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
    print(f"🆔 Bot ID: {bot.user.id}")

    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"❌ Slash command sync failed: {e}")


@bot.tree.command(name="novel", description="Create a short novel from character images.")
async def novel(interaction: discord.Interaction):
    await interaction.response.send_message(
        "📖 Yeosang's Novel Engine is ready. 🖤"
    )


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot.run(token)
