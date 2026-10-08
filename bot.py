import os
import threading

import discord
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


@bot.command()
async def test(ctx):
    print(f"TEST command received from {ctx.author}")
    await ctx.send("Yeosang is awake. 🖤")


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot.run(token)
