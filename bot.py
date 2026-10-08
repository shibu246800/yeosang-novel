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


# ─────────────────────────────
# OpenRouter helpers
# ─────────────────────────────

OPENROUTER_MODELS_URL = (
    "https://openrouter.ai/api/v1/models"
)

OPENROUTER_CHAT_URL = (
    "https://openrouter.ai/api/v1/chat/completions"
)


def get_free_vision_models(api_key):
    """
    Ask OpenRouter for its current model list.

    Return free models that advertise image input.
    """

    response = requests.get(
        OPENROUTER_MODELS_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
        },
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    models = data.get("data", [])

    vision_models = []

    for model in models:

        model_id = model.get("id")

        if not model_id:
            continue

        architecture = model.get(
            "architecture",
            {}
        )

        input_modalities = architecture.get(
            "input_modalities",
            []
        )

        pricing = model.get(
            "pricing",
            {}
        )

        prompt_price = pricing.get(
            "prompt"
        )

        completion_price = pricing.get(
            "completion"
        )

        # We want models that:
        #
        # 1. Accept images.
        # 2. Have zero input price.
        # 3. Have zero output price.

        supports_image = (
            "image" in input_modalities
        )

        is_free = (
            str(prompt_price) == "0"
            and str(completion_price) == "0"
        )

        if supports_image and is_free:

            vision_models.append(
                {
                    "id": model_id,
                    "name": model.get(
                        "name",
                        model_id
                    ),
                    "context_length": model.get(
                        "context_length"
                    ),
                }
            )

    return vision_models


def extract_text_from_chat_response(data):
    """
    Safely extract normal chat-completion text.
    """

    choices = data.get(
        "choices",
        []
    )

    if not choices:
        return None

    message = choices[0].get(
        "message",
        {}
    )

    content = message.get(
        "content"
    )

    if isinstance(content, str):
        return content

    # Some providers can return content
    # as structured pieces.

    if isinstance(content, list):

        text_parts = []

        for part in content:

            if not isinstance(part, dict):
                continue

            text = part.get(
                "text"
            )

            if text:
                text_parts.append(text)

        if text_parts:
            return "\n".join(text_parts)

    return None


def looks_like_safety_only_response(text):
    """
    Detect the exact kind of response we were
    getting from the previous free router.
    """

    if not text:
        return True

    cleaned = text.strip().lower()

    safety_responses = {
        "user safety: safe",
        "user safety:safe",
        "safe",
    }

    if cleaned in safety_responses:
        return True

    return False


# ─────────────────────────────
# Bot ready
# ─────────────────────────────

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
        or filename.endswith(
            allowed_extensions
        )
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

Do NOT write a novel yet.

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

10. Overall aesthetic and atmosphere.

11. Possible story potential.

IMPORTANT:

- Actually inspect the image.
- Do not give a safety classification.
- Do not simply answer "safe".
- Do not invent character names.
- Do not assume fandom canon.
- Do not invent facts that cannot be seen.
- Separate observation from interpretation.
- If something is unclear, say so.
- Do not write the novel yet.

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
    # Find current free vision
    # models
    # ─────────────────────────

    try:

        print(
            "🔎 Asking OpenRouter for current "
            "free vision models..."
        )

        vision_models = get_free_vision_models(
            api_key
        )

        if not vision_models:

            await interaction.followup.send(
                "❌ OpenRouter currently reports "
                "no free vision models available."
            )

            return

        print(
            f"👁️ Found {len(vision_models)} "
            "free vision model(s)."
        )

        for model in vision_models:

            print(
                f"   • {model['id']}"
            )

    except requests.exceptions.RequestException as e:

        print(
            f"❌ MODEL LIST ERROR: {e}"
        )

        await interaction.followup.send(
            "❌ I couldn't retrieve the current "
            "OpenRouter model list."
        )

        return

    # ─────────────────────────
    # Try the current models
    # ─────────────────────────

    successful_answer = None
    successful_model = None

    failed_models = []

    for model in vision_models:

        model_id = model["id"]

        print(
            f"🧪 Testing vision model: {model_id}"
        )

        payload = {

            "model": model_id,

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
        }

        try:

            response = requests.post(

                OPENROUTER_CHAT_URL,

                headers={
                    "Authorization": (
                        f"Bearer {api_key}"
                    ),
                    "Content-Type": (
                        "application/json"
                    ),
                    "HTTP-Referer": (
                        "https://yeosang-novel.onrender.com"
                    ),
                    "X-Title": (
                        "Yeosang Novel"
                    ),
                },

                json=payload,

                timeout=120,
            )

            print(
                f"MODEL: {model_id}"
            )

            print(
                f"STATUS: {response.status_code}"
            )

            # ─────────────────────
            # Model failed
            # ─────────────────────

            if not response.ok:

                print(
                    f"RESPONSE: {response.text}"
                )

                failed_models.append(
                    model_id
                )

                continue

            # ─────────────────────
            # Extract answer
            # ─────────────────────

            try:

                data = response.json()

            except ValueError:

                failed_models.append(
                    model_id
                )

                continue

            answer = (
                extract_text_from_chat_response(
                    data
                )
            )

            # ─────────────────────
            # Reject safety-only
            # ─────────────────────

            if looks_like_safety_only_response(
                answer
            ):

                print(
                    f"⚠️ {model_id} returned "
                    "a safety-only response."
                )

                failed_models.append(
                    model_id
                )

                continue

            # ─────────────────────
            # Success
            # ─────────────────────

            if answer:

                successful_answer = answer
                successful_model = model_id

                print(
                    f"✅ Vision model succeeded: "
                    f"{model_id}"
                )

                break

            failed_models.append(
                model_id
            )

        except requests.exceptions.Timeout:

            print(
                f"⏱️ {model_id} timed out."
            )

            failed_models.append(
                model_id
            )

        except requests.exceptions.RequestException as e:

            print(
                f"❌ {model_id} request error: {e}"
            )

            failed_models.append(
                model_id
            )

    # ─────────────────────────
    # No model succeeded
    # ─────────────────────────

    if not successful_answer:

        print(
            "❌ No free vision model successfully "
            "analyzed the image."
        )

        await interaction.followup.send(
            "❌ **No current free vision model "
            "successfully analyzed the board.**\n\n"
            "I checked OpenRouter's live model list "
            "instead of using a hard-coded model ID."
        )

        return

    # ─────────────────────────
    # Successful analysis
    # ─────────────────────────

    max_length = 1900

    header = (
        "👁️ **Yeosang's Visual Analysis**\n"
        f"*Vision model: `{successful_model}`*\n\n"
    )

    if len(successful_answer) <= (
        max_length - len(header)
    ):

        await interaction.followup.send(
            header + successful_answer
        )

    else:

        first_chunk_length = (
            max_length - len(header)
        )

        first_chunk = successful_answer[
            :first_chunk_length
        ]

        remaining = successful_answer[
            first_chunk_length:
        ]

        await interaction.followup.send(
            header + first_chunk
        )

        while remaining:

            chunk = remaining[
                :1900
            ]

            remaining = remaining[
                1900:
            ]

            await interaction.followup.send(
                chunk
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
