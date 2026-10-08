import os
import threading

import discord
import requests
from discord import app_commands
from discord.ext import commands
from flask import Flask

from ai.manager import AIManager
from ai.storyteller import Storyteller


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
# AI SYSTEM
# ═════════════════════════════════════

ai_manager = AIManager()

storyteller = Storyteller(
    ai_manager
)


# ═════════════════════════════════════
# VISUAL ANALYSIS PROMPT
# ═════════════════════════════════════

VISUAL_ANALYSIS_PROMPT = """
You are the visual-analysis brain of Yeosang Novel.

Actually inspect the uploaded collection board.

Do NOT write a novel.

Do NOT give a safety classification.

Do NOT simply answer "safe".

Your job is to create useful visual evidence for another
AI that will later invent stories.

For EVERY visible character, identify what can reasonably
be observed.

Analyze:

1. Appearance
2. Clothing
3. Accessories
4. Expression
5. Posture
6. Body language
7. Distinctive visual details
8. Personality clues suggested by presentation
9. Emotional clues
10. Possible relationship clues
11. Important objects
12. Symbols and motifs
13. Background and setting clues
14. Overall atmosphere
15. Storytelling possibilities

IMPORTANT:

- Do not invent names.
- Do not assume fandom canon.
- Do not identify real people.
- Do not claim unseen facts.
- Clearly distinguish observation from interpretation.
- If something cannot be determined, say so.

Use this structure:

CHARACTER 1

Appearance:
Personality clues:
Emotional clues:
Distinctive details:
Possible narrative significance:

CHARACTER 2

Appearance:
Personality clues:
Emotional clues:
Distinctive details:
Possible narrative significance:

Continue for every visible character.

RELATIONSHIP / DYNAMIC CLUES

IMPORTANT VISUAL ELEMENTS

SETTING CLUES

OVERALL ATMOSPHERE

POSSIBLE STORY INGREDIENTS
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
    description="Turn a collection board into story ideas."
)
@app_commands.describe(
    board="Upload the collection board you want Yeosang to use."
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

    # ═════════════════════════════════
    # STAGE 1
    # VISUAL ANALYSIS
    # ═════════════════════════════════

    print(
        "👁️ Stage 1: visual analysis"
    )

    try:

        visual_analysis, vision_provider = (
            ai_manager.analyze_image(
                image_bytes=image_bytes,
                mime_type=mime_type,
                prompt=VISUAL_ANALYSIS_PROMPT,
            )
        )

    except Exception as error:

        print(
            f"❌ Visual analysis failed: "
            f"{error}"
        )

        await interaction.followup.send(
            "❌ Yeosang couldn't understand "
            "the collection board.\n\n"
            f"`{error}`"
        )

        return

    # ═════════════════════════════════
    # STAGE 2
    # STORY BRAIN
    # ═════════════════════════════════

    print(
        "🧠 Stage 2: story brainstorming"
    )

    try:

        story_ideas, story_provider = (
            storyteller.brainstorm(
                visual_analysis
            )
        )

    except Exception as error:

        print(
            f"❌ Story Brain failed: "
            f"{error}"
        )

        await interaction.followup.send(
            "❌ Yeosang understood the "
            "collection, but couldn't turn "
            "it into story possibilities.\n\n"
            f"`{error}`"
        )

        return

    # ═════════════════════════════════
    # RESULT
    # ═════════════════════════════════

    header = (
        "🧠 **Yeosang's Story Brain**\n"
        f"*Vision: {vision_provider}*\n"
        f"*Story Brain: {story_provider}*\n\n"
    )

    first_limit = 1900 - len(header)

    if len(story_ideas) <= first_limit:

        await interaction.followup.send(
            header + story_ideas
        )

        return

    await interaction.followup.send(
        header + story_ideas[:first_limit]
    )

    remaining = story_ideas[first_limit:]

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
