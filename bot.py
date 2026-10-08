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

    app.run(
        host="0.0.0.0",
        port=port
    )


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

        print(
            f"✅ Synced {len(synced)} slash command(s)"
        )

    except Exception as e:

        print(
            f"❌ Slash command sync failed: {e}"
        )


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

    # ─────────────────────────
    # API key
    # ─────────────────────────

    api_key = os.environ.get(
        "OPENROUTER_API_KEY"
    )

    if not api_key:

        await interaction.followup.send(
            "❌ OPENROUTER_API_KEY is missing from Render."
        )

        return

    # ─────────────────────────
    # Check attachment
    # ─────────────────────────

    print(
        f"🖼️ Received attachment: {board.filename}"
    )

    print(
        f"📦 Content type: {board.content_type}"
    )

    print(
        f"📏 File size: {board.size} bytes"
    )

    # Discord normally supplies image/jpeg,
    # image/png, image/webp, etc.
    #
    # We also check the filename because Discord
    # can occasionally return a missing content type.

    filename = board.filename.lower()

    allowed_extensions = (
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
    )

    is_image = (
        board.content_type
        and board.content_type.startswith("image/")
    ) or filename.endswith(allowed_extensions)

    if not is_image:

        await interaction.followup.send(
            "❌ Please upload an image "
            "(PNG, JPG, JPEG, WEBP, or GIF)."
        )

        return

    # ─────────────────────────
    # Vision prompt
    # ─────────────────────────

    prompt = """
You are Yeosang, an advanced visual-analysis system
for an AI novel-writing application.

You are looking at a collection board containing
character cards.

THIS IS A VISUAL ANALYSIS TEST.

Do NOT write a novel yet.

Study the uploaded image carefully.

Your task is to extract useful information that a
future novel-writing system can use.

Analyze:

1. EVERY visible character.

2. Each character's visible appearance:
   - face
   - hair
   - clothing
   - accessories
   - posture
   - expression
   - body language
   - distinctive visual details

3. Personality clues suggested by the visual design.

4. Emotional clues.

5. Differences and contrasts between characters.

6. Possible relationship or character-dynamic clues
   suggested by the visual material.

7. Important objects.

8. Symbols.

9. Backgrounds and possible locations.

10. Recurring visual motifs.

11. The overall atmosphere and aesthetic.

IMPORTANT RULES:

- Actually inspect the image.
- Do not simply say that the image is safe.
- Do not respond with a safety classification.
- Do not write the novel.
- Do not invent character names.
- Do not assume fandom canon.
- Do not claim uncertain details as facts.
- Clearly distinguish observation from interpretation.
- If something cannot be determined from the image,
  say that it cannot be determined.

Use this structure:

CHARACTER 1
Appearance:
Personality clues:
Emotional clues:
Distinctive details:

CHARACTER 2
Appearance:
Personality clues:
Emotional clues:
Distinctive details:

Continue for every visible character.

RELATIONSHIP / DYNAMIC CLUES

IMPORTANT VISUAL ELEMENTS

OVERALL ATMOSPHERE

POSSIBLE STORY POTENTIAL
"""

    # ─────────────────────────
    # OpenRouter request
    # ─────────────────────────

    try:

        print(
            "🧠 Sending image to OpenRouter..."
        )

        response = requests.post(

            "https://openrouter.ai/api/v1/chat/completions",

            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": (
                    "https://yeosang-novel.onrender.com"
                ),
                "X-Title": "Yeosang Novel",
            },

            json={

                "model": (
                    "nvidia/"
                    "nemotron-3-nano-omni:free"
                ),

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
                                    "url": board.url
                                },
                            },

                        ],
                    }

                ],

            },

            timeout=120,
        )

        # ─────────────────────
        # Print complete response
        # ─────────────────────

        print(
            f"OPENROUTER STATUS: "
            f"{response.status_code}"
        )

        print(
            f"OPENROUTER RESPONSE: "
            f"{response.text}"
        )

        # ─────────────────────
        # OpenRouter error
        # ─────────────────────

        if not response.ok:

            error_text = response.text

            # Keep Discord message under its limit.
            if len(error_text) > 1500:
                error_text = error_text[:1500]

            await interaction.followup.send(
                "❌ **OpenRouter rejected the vision request.**\n\n"
                f"Status: `{response.status_code}`\n"
                f"```json\n{error_text}\n```"
            )

            return

        # ─────────────────────
        # Parse response
        # ─────────────────────

        try:

            data = response.json()

        except ValueError:

            await interaction.followup.send(
                "❌ OpenRouter returned an invalid response."
            )

            return

        # ─────────────────────
        # Extract answer safely
        # ─────────────────────

        choices = data.get("choices")

        if not choices:

            print(
                "❌ No choices returned by OpenRouter."
            )

            await interaction.followup.send(
                "❌ OpenRouter returned no AI response."
            )

            return

        message = choices[0].get(
            "message",
            {}
        )

        answer = message.get(
            "content"
        )

        if not answer:

            print(
                f"❌ Unexpected AI response: {data}"
            )

            await interaction.followup.send(
                "❌ The AI returned an empty response."
            )

            return

        print(
            "✅ Vision analysis successful."
        )

        # ─────────────────────
        # Send analysis
        # ─────────────────────

        max_length = 1900

        if len(answer) <= max_length:

            await interaction.followup.send(
                "👁️ **Yeosang's Visual Analysis**\n\n"
                f"{answer}"
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
                "👁️ **Yeosang's Visual Analysis**\n\n"
                f"{chunks[0]}"
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
    # Connection error
    # ─────────────────────────

    except requests.exceptions.RequestException as e:

        print(
            f"❌ REQUEST ERROR: {type(e).__name__}: {e}"
        )

        await interaction.followup.send(
            "❌ Could not connect to OpenRouter."
        )

    # ─────────────────────────
    # Unexpected error
    # ─────────────────────────

    except Exception as e:

        print(
            f"❌ AI ERROR: {type(e).__name__}: {e}"
        )

        await interaction.followup.send(
            "❌ Something went wrong while analyzing "
            "the collection."
        )


# ─────────────────────────────
# Start
# ─────────────────────────────

if __name__ == "__main__":

    threading.Thread(
        target=run_web,
        daemon=True
    ).start()

    token = os.environ.get(
        "DISCORD_TOKEN"
    )

    if not token:

        raise RuntimeError(
            "DISCORD_TOKEN is not set."
        )

    bot.run(token)
