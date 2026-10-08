import discord
from discord.ext import commands

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


bot.run("YOUR_BOT_TOKEN")
