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
    # Check image
    # ─────────────────────────

    filename = board.filename.lower()

    allowed_extensions = (
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
    )

    is_image = (
        (
            board.content_type
            and board.content_type.startswith("image/")
        )
        or filename.endswith(allowed_extensions)
    )

    if not is_image:

        await interaction.followup.send(
            "❌ Please upload a PNG, JPG, JPEG, WEBP, or GIF image."
        )

        return

    print(
        f"🖼️ Received image: {board.filename}"
    )

    print(
        f"📦 Content type: {board.content_type}"
    )

    print(
        f"📏 Size: {board.size} bytes"
    )

    # ─────────────────────────
    # Vision prompt
    # ─────────────────────────

    prompt = """
You are Yeosang, the visual-analysis brain of an
advanced AI novel-writing system.

You have been given a collection board containing
character cards.

THIS IS A VISUAL ANALYSIS TEST.

Do NOT write a novel.

Do NOT give a safety classification.

Actually inspect the uploaded image.

Study every visible character and every useful visual
detail.

Analyze:

1. EVERY visible character.

2. Appearance:
   - face
   - hair
   - clothing
   - accessories
   - posture
   - expression
   - body language
   - distinctive features

3. Personality clues suggested by appearance.

4. Emotional clues.

5. Differences and contrasts between characters.

6. Possible relationship or dynamic clues.

7. Important objects.

8. Symbols and motifs.

9. Backgrounds and possible locations.

10. The overall aesthetic and atmosphere.

11. Possible story potential.

IMPORTANT:

- Do not invent names.
- Do not assume fandom canon.
- Do not invent facts that cannot be seen.
- Separate observation from interpretation.
- If something is unclear, say so.
- Do not simply say "safe".
- Actually describe what you see.

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
    # OpenRouter Responses API
    # ─────────────────────────

    payload = {
        "model": "openrouter/free",

        "input": [
            {
                "role": "user",

                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    },

                    {
                        "type": "input_image",

                        "image_url": board.url,
                    },
                ],
            }
        ],
    }

    try:

        print(
            "🧠 Sending image to OpenRouter..."
        )

        response = requests.post(

            "https://openrouter.ai/api/v1/responses",

            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": (
                    "https://yeosang-novel.onrender.com"
                ),
                "X-Title": "Yeosang Novel",
            },

            json=payload,

            timeout=120,
        )

        # ─────────────────────
        # Always print response
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
        # Error
        # ─────────────────────

        if not response.ok:

            error_text = response.text

            if len(error_text) > 1500:
                error_text = error_text[:1500]

            await interaction.followup.send(
                "❌ **OpenRouter rejected the request.**\n\n"
                f"Status: `{response.status_code}`\n"
                f"```json\n{error_text}\n```"
            )

            return

        # ─────────────────────
        # Parse JSON
        # ─────────────────────

        try:

            data = response.json()

        except ValueError:

            await interaction.followup.send(
                "❌ OpenRouter returned invalid JSON."
            )

            return

        # ─────────────────────
        # Extract Responses API
        # output
        # ─────────────────────

        answer = None

        # Standard Responses API output
        output = data.get("output", [])

        for item in output:

            if item.get("type") != "message":
                continue

            content = item.get(
                "content",
                []
            )

            for content_item in content:

                if content_item.get("type") in (
                    "output_text",
                    "text",
                ):

                    answer = content_item.get(
                        "text"
                    )

                    if answer:
                        break

            if answer:
                break

        # ─────────────────────
        # Fallback
        # ─────────────────────

        if not answer:

            print(
                "❌ Could not find text in response."
            )

            print(
                f"FULL DATA: {data}"
            )

            await interaction.followup.send(
                "❌ The AI responded, but I couldn't "
                "extract its analysis."
            )

            return

        print(
            "✅ Vision analysis successful."
        )

        # ─────────────────────
        # Discord message limit
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
    # General error
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
