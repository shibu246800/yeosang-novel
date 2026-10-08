import os
import threading
import base64

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
    # Check image type
    # ─────────────────────────

    allowed_types = {
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/webp",
        "image/gif",
    }

    if board.content_type not in allowed_types:
        await interaction.followup.send(
            "❌ Please upload a PNG, JPG, JPEG, WEBP, or GIF image."
        )
        return

    try:

        print(f"🖼️ Received image: {board.filename}")

        # ─────────────────────
        # Download Discord image
        # ─────────────────────

        image_response = requests.get(
            board.url,
            timeout=30
        )

        image_response.raise_for_status()

        # ─────────────────────
        # Convert image to Base64
        # ─────────────────────

        image_base64 = base64.b64encode(
            image_response.content
        ).decode("utf-8")

        image_data_url = (
            f"data:{board.content_type};base64,{image_base64}"
        )

        # ─────────────────────
        # Vision instructions
        # ─────────────────────

        prompt = """
You are Yeosang, an advanced visual analysis and novel-writing AI.

You are looking at a collection board containing character cards.

THIS IS ONLY A VISUAL ANALYSIS TEST.

Do NOT write a novel yet.

Carefully inspect the entire uploaded image.

Identify and analyze:

1. Every visible character.
2. Each character's visible appearance.
3. Hair, face, clothing, accessories, posture, expression,
   body language, and distinctive visual details.
4. The apparent mood or personality suggested by the visual design.
5. Differences and contrasts between the characters.
6. Possible relationships or character dynamics suggested by
   the visual material.
7. Important objects, symbols, locations, backgrounds, and motifs.
8. The overall atmosphere and aesthetic of the collection.

IMPORTANT RULES:

- Only claim something is visually supported when the image actually
  provides evidence.
- Clearly distinguish observation from interpretation.
- Do not invent character names.
- Do not assume canon or fandom information.
- Do not write the novel.
- Do not summarize the request.
- Actually analyze the image.

Organize the response as:

CHARACTERS
- Character 1
- Character 2
- etc.

RELATIONSHIP / DYNAMIC CLUES

IMPORTANT VISUAL ELEMENTS

OVERALL ATMOSPHERE

POSSIBLE STORY POTENTIAL
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
                "model": "nvidia/nemotron-3-nano-omni:free",
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
        # Read AI response
        # ─────────────────────

        data = response.json()

        answer = data["choices"][0]["message"]["content"]

        print("✅ Vision analysis successful.")

        # ─────────────────────
        # Send result to Discord
        # ─────────────────────

        max_length = 1900

        if len(answer) <= max_length:

            await interaction.followup.send(
                f"👁️ **Yeosang's Visual Analysis**\n\n{answer}"
            )

        else:

            chunks = [
                answer[i:i + max_length]
                for i in range(
                    0,
                    len(answer),
                    max_length
                )
            ]

            await interaction.followup.send(
                f"👁️ **Yeosang's Visual Analysis**\n\n{chunks[0]}"
            )

            for chunk in chunks[1:]:
                await interaction.followup.send(chunk)

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
