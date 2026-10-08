import os
import threading

import discord
from discord.ext import commands
from flask import Flask


# ─────────────────────────────
# Web server for Render
# ─────────────────────────────

app = Flask(__name__)


@app.route("/")
def home():
    return "Yeosang Novel is alive. 🖤"


def run_web():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))


# ─────────────────────────────
# Discord bot
# ─────────────────────────────

intents = discord.Intents.default()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


@bot.command()
async def test(ctx):
    await ctx.send("Yeosang is awake. 🖤")


# ─────────────────────────────
# Start
# ─────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot.run(token)
