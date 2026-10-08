import os
import threading

import discord
import requests
from discord import app_commands
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
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ─────────────────────────────
# Discord bot
# ─────────────────────────────

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


# ─────────────────────────────
# /novel
# ─────────────────────────────

@bot.tree.command(
    name="novel",
    description="Analyze a collection board for a novel."
)
@app_commands.describe(
    board="Upload the collection board you want Yeosang to analyze."
)
async def novel(
    interaction: discord.Interaction,
    board: discord.Attachment
):

    await interaction.response.defer()

    api_key = os.environ.get("OPENROUTER_API_KEY")

    if not api_key:
        await interaction.followup.send(
            "❌ OPENROUTER_API_KEY is missing from Render."
        )
        return

    # ─────────────────────────
    # Check file type
    # ─────────────────────────

    allowed_types = (
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
    )

    if board.content_type not in allowed_types:

        await interaction.followup.send(
            "❌ Please upload an image file "
            "(PNG, JPG, JPEG, WEBP, or GIF)."
        )

        return

    try:

        print(
            f"🖼️ Received image: {board.filename}"
        )

        # ─────────────────────
        # Download Discord image
        # ─────────────────────

        image_response = requests.get(
            board.url,
            timeout=30
        )

        image_response.raise_for_status()

        # ─────────────────────
        # Convert image to base64
        # ─────────────────────

        import base64

        image_base64 = base64.b64encode(
            image_response.content
        ).decode("utf-8")

        mime_type = board.content_type

        image_data_url = (
            f"data:{mime_type};base64,{image_base64}"
        )

        # ─────────────────────
        # Vision prompt
        # ─────────────────────

        prompt = """
You are Yeosang, an advanced AI novel-writing system.

Analyze the uploaded collection board carefully.

This is NOT yet a request to write the novel.

Your job is to study the visual material and identify:

1. Every visible character.
2. Their apparent visual traits.
3. Clothing and styling.
4. Distinctive accessories or objects.
5. Apparent personality clues suggested by their appearance.
6. The relationships or contrasts that could exist between the characters.
7. The overall atmosphere and aesthetic of the collection.
8. Any symbols, locations, objects, or visual details that could become
   important story elements.

Do not invent names if they are not visible or provided.

Separate what you can directly observe from what you are interpreting.

Write a detailed but organized visual analysis.

Do NOT write the novel yet.
"""

        # ─────────────────────
        # OpenRouter request
        # ─────────────────────

        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://yeosang-novel.onrender.com",
                "X-Title": "Yeosang Novel",
            },
            json={
                "model": "openrouter/free",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": prompt,
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": image_data_url
                                },
                            },
                        ],
                    }
                ],
            },
            timeout=120,
        )

        # ─────────────────────
        # OpenRouter error
        # ─────────────────────

        if not response.ok:

            print(
                f"OPENROUTER STATUS: {response.status_code}"
            )

            print(
                f"OPENROUTER RESPONSE: {response.text}"
            )

            await interaction.followup.send(
                f"❌ OpenRouter error: `{response.status_code}`"
            )

            return

        # ─────────────────────
        # Read response
        # ─────────────────────

        data = response.json()

        answer = data["choices"][0]["message"]["content"]

        print(
            "✅ Vision analysis successful."
        )

        # ─────────────────────
        # Discord message limit
        # ─────────────────────

        if len(answer) <= 1900:

            await interaction.followup.send(
                f"👁️ **Yeosang's Visual Analysis**\n\n{answer}"
            )

        else:

            # Split long AI response into Discord-safe chunks.
            chunks = [
                answer[i:i + 1900]
                for i in range(0, len(answer), 1900)
            ]

            await interaction.followup.send(
                f"👁️ **Yeosang's Visual Analysis**\n\n{chunks[0]}"
            )

            for chunk in chunks[1:]:

                await interaction.followup.send(
                    chunk
                )

    # ─────────────────────────
    # Timeout
    # ─────────────────────────

    except requests.exceptions.Timeout:

        print(
            "❌ OpenRouter request timed out."
        )

        await interaction.followup.send(
            "❌ Yeosang took too long to analyze the image."
        )

    # ─────────────────────────
    # General error
    # ─────────────────────────

    except Exception as e:

        print(
            f"❌ AI ERROR: {type(e).__name__}: {e}"
        )

        await interaction.followup.send(
            "❌ Something went wrong while analyzing the collection."
        )


# ─────────────────────────────
# Start
# ─────────────────────────────

if __name__ == "__main__":

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError(
            "DISCORD_TOKEN is not set."
        )

    bot.run(token)
