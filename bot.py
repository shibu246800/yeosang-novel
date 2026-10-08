import os
import threading

import discord
import requests
from discord import app_commands
from discord.ext import commands
from flask import Flask

from ai.manager import AIManager


# ═════════════════════════════════════
# WEB SERVER FOR RENDER
# ═════════════════════════════════════

app = Flask(__name__)


@app.route("/")
def home():
    return "Yeosang Novel is alive. 🖤"


def run_web():

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )


# ═════════════════════════════════════
# DISCORD BOT
# ═════════════════════════════════════

intents = discord.Intents.default()

intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


# ═════════════════════════════════════
# AI MANAGER
# ═════════════════════════════════════

ai_manager = AIManager()


# ═════════════════════════════════════
# IMAGE PROMPT
# ═════════════════════════════════════

VISUAL_ANALYSIS_PROMPT = """
You are Yeosang, the visual-analysis brain of
an advanced AI novel-writing system.

This is a collection board containing character cards.

THIS IS A VISUAL ANALYSIS TEST.

Actually inspect the uploaded image carefully.

Do NOT write a novel yet.

Do NOT give a safety classification.

Do NOT simply answer "safe".

Analyze the visible characters and the visual storytelling
potential of the board.

For EVERY visible character, identify only what can reasonably
be observed from the image.

Analyze:

1. Character appearance
   - hair
   - clothing
   - accessories
   - expression
   - posture
   - body language
   - distinctive visual details

2. Personality clues suggested by their presentation.

3. Emotional clues.

4. Contrasts between characters.

5. Possible relationship or dynamic clues.

6. Important objects.

7. Symbols and motifs.

8. Background and setting clues.

9. Overall atmosphere.

10. Story potential.

IMPORTANT RULES:

- Actually inspect the image.
- Do not invent character names.
- Do not assume fandom canon.
- Do not identify real people.
- Do not claim unseen facts.
- Separate visible observations from interpretation.
- If something cannot be determined, say so.
- Do not write the actual novel yet.

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


# ═════════════════════════════════════
# BOT READY
# ═════════════════════════════════════

@bot.event
async def on_ready():

    print(
        f"✅ Logged in as {bot.user}"
    )

    print(
        f"🆔 Bot ID: {bot.user.id}"
    )

    try:

        synced = await bot.tree.sync()

        print(
            f"✅ Synced {len(synced)} "
            f"slash command(s)"
        )

    except Exception as error:

        print(
            f"❌ Slash command sync failed: "
            f"{error}"
        )


# ═════════════════════════════════════
# /novel
# ═════════════════════════════════════

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

    # ─────────────────────────────────
    # CHECK IMAGE
    # ─────────────────────────────────

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
            and board.content_type.startswith(
                "image/"
            )
        )
        or filename.endswith(
            allowed_extensions
        )
    )

    if not is_image:

        await interaction.followup.send(
            "❌ Please upload a PNG, JPG, "
            "JPEG, WEBP, or GIF image."
        )

        return

    print(
        f"🖼️ Received board: "
        f"{board.filename}"
    )

    print(
        f"📏 Size: "
        f"{board.size} bytes"
    )

    # ─────────────────────────────────
    # DOWNLOAD IMAGE
    # ─────────────────────────────────

    try:

        image_response = requests.get(
            board.url,
            timeout=30
        )

        image_response.raise_for_status()

        image_bytes = image_response.content

    except requests.RequestException as error:

        print(
            f"❌ Image download failed: "
            f"{error}"
        )

        await interaction.followup.send(
            "❌ I couldn't retrieve the "
            "uploaded image from Discord."
        )

        return

    # ─────────────────────────────────
    # MIME TYPE
    # ─────────────────────────────────

    mime_type = board.content_type

    if not mime_type:

        extension = filename.rsplit(
            ".",
            1
        )[-1]

        mime_map = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "webp": "image/webp",
            "gif": "image/gif",
        }

        mime_type = mime_map.get(
            extension,
            "image/jpeg"
        )

    # ─────────────────────────────────
    # AI ANALYSIS
    # ─────────────────────────────────

    try:

        analysis, provider_name = (
            ai_manager.analyze_image(
                image_bytes=image_bytes,
                mime_type=mime_type,
                prompt=VISUAL_ANALYSIS_PROMPT,
            )
        )

    except Exception as error:

        print(
            f"❌ All AI providers failed: "
            f"{error}"
        )

        await interaction.followup.send(
            "❌ Yeosang couldn't analyze "
            "this collection board.\n\n"
            f"`{error}`"
        )

        return

    # ─────────────────────────────────
    # RESULT
    # ─────────────────────────────────

    header = (
        "👁️ **Yeosang's Visual Analysis**\n"
        f"*Provider: {provider_name}*\n\n"
    )

    first_limit = 1900 - len(header)

    if len(analysis) <= first_limit:

        await interaction.followup.send(
            header + analysis
        )

        return

    await interaction.followup.send(
        header + analysis[:first_limit]
    )

    remaining = analysis[first_limit:]

    while remaining:

        chunk = remaining[:1900]

        remaining = remaining[1900:]

        await interaction.followup.send(
            chunk
        )


# ═════════════════════════════════════
# START
# ═════════════════════════════════════

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
