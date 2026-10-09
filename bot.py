
import os
import json
import sqlite3
import threading
import logging
from datetime import datetime
from pathlib import Path

import discord
import requests
from discord import app_commands
from discord.ext import commands
from flask import Flask

from ai.manager import AIManager


# ==========================================
# CONFIGURATION
# ==========================================

logging.basicConfig(level=logging.INFO)

DATABASE_PATH = Path("data/story_sessions.db")
BOARD_FOLDER = Path("data/boards")

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
BOARD_FOLDER.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)


@app.route("/")
def home():
    return "Yeosang Novel is alive. 🖤"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ==========================================
# DATABASE
# ==========================================

def connect_db():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS story_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                board_path TEXT NOT NULL,
                board_filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                visual_analysis TEXT NOT NULL,
                story_memory TEXT NOT NULL DEFAULT '{}',
                current_question TEXT,
                current_options TEXT,
                status TEXT NOT NULL DEFAULT 'developing',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, session_date)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS story_answers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(session_id)
                    REFERENCES story_sessions(id)
            )
        """)


initialize_database()


def today_string():
    # Use UTC so the date is consistent across server restarts.
    # We can change this to a configured timezone later.
    return datetime.utcnow().date().isoformat()


def get_todays_session(user_id):
    with connect_db() as db:
        return db.execute("""
            SELECT *
            FROM story_sessions
            WHERE user_id = ? AND session_date = ?
        """, (str(user_id), today_string())).fetchone()


def get_session(session_id):
    with connect_db() as db:
        return db.execute("""
            SELECT *
            FROM story_sessions
            WHERE id = ?
        """, (session_id,)).fetchone()


def save_session(
    user_id,
    board_path,
    board_filename,
    mime_type,
    visual_analysis,
):
    now = datetime.utcnow().isoformat()

    memory = {
        "collection": {
            "filename": board_filename,
            "visual_analysis": visual_analysis,
        },
        "user_choices": [],
        "characters": [],
        "setting": None,
        "relationship": None,
        "conflict": None,
        "ending_tone": None,
        "title": None,
        "approved_chapters": [],
    }

    with connect_db() as db:
        existing = db.execute("""
            SELECT id
            FROM story_sessions
            WHERE user_id = ? AND session_date = ?
        """, (str(user_id), today_string())).fetchone()

        if existing:
            raise ValueError(
                "You already started a story session today. "
                "Your existing session has been preserved."
            )

        cursor = db.execute("""
            INSERT INTO story_sessions (
                user_id,
                session_date,
                board_path,
                board_filename,
                mime_type,
                visual_analysis,
                story_memory,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(user_id),
            today_string(),
            str(board_path),
            board_filename,
            mime_type,
            visual_analysis,
            json.dumps(memory, ensure_ascii=False),
            "developing",
            now,
            now,
        ))

        return cursor.lastrowid


def save_question(session_id, question, options):
    with connect_db() as db:
        db.execute("""
            UPDATE story_sessions
            SET current_question = ?,
                current_options = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            question,
            json.dumps(options, ensure_ascii=False),
            datetime.utcnow().isoformat(),
            session_id,
        ))


def save_answer(session_id, question, answer):
    with connect_db() as db:
        row = db.execute("""
            SELECT story_memory
            FROM story_sessions
            WHERE id = ?
        """, (session_id,)).fetchone()

        if not row:
            raise ValueError("Story session not found.")

        memory = json.loads(row["story_memory"])
        memory.setdefault("user_choices", []).append({
            "question": question,
            "answer": answer,
        })

        db.execute("""
            INSERT INTO story_answers (
                session_id, question, answer, created_at
            )
            VALUES (?, ?, ?, ?)
        """, (
            session_id,
            question,
            answer,
            datetime.utcnow().isoformat(),
        ))

        db.execute("""
            UPDATE story_sessions
            SET story_memory = ?,
                current_question = NULL,
                current_options = NULL,
                updated_at = ?
            WHERE id = ?
        """, (
            json.dumps(memory, ensure_ascii=False),
            datetime.utcnow().isoformat(),
            session_id,
        ))


# ==========================================
# DISCORD BOT
# ==========================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)

ai_manager = AIManager()


# ==========================================
# AI HELPERS
# ==========================================

VISUAL_ANALYSIS_PROMPT = """
You are the private visual-analysis system of Yeosang Novel.

Inspect the uploaded collection board carefully.

Describe visible characters, clothing, expressions, objects,
symbols, backgrounds, atmosphere, and possible relationships.

Separate direct visual observations from interpretations.
Do not invent character names or claim unsupported facts.
Do not write a story or display story ideas to the user.

Your analysis will be stored in a private story file and used
to help the user develop an original novel.
"""


def unpack_ai_result(result):
    if isinstance(result, tuple):
        text_result = result[0]
        provider = result[1] if len(result) > 1 else "AI"
        return str(text_result), str(provider)

    return str(result), "AI"


def generate_text(prompt):
    result = ai_manager.generate_text(prompt)
    return unpack_ai_result(result)


def extract_json(text):
    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        raise ValueError("The AI did not return valid JSON.")

    return json.loads(text[start:end + 1])


def get_answers(session_id):
    with connect_db() as db:
        rows = db.execute("""
            SELECT question, answer
            FROM story_answers
            WHERE session_id = ?
            ORDER BY id
        """, (session_id,)).fetchall()

    return [
        {"question": row["question"], "answer": row["answer"]}
        for row in rows
    ]


def create_next_question(session_id):
    session = get_session(session_id)

    if not session:
        raise ValueError("Story session not found.")

    answers = get_answers(session_id)

    prompt = f"""
You are Yeosang, an organized and thoughtful novel-writing
companion. Help the user build a novel from their collection.

Ask exactly ONE useful question at a time.

Do not write a story yet.
Do not dump a list of questions into chat.
Do not repeat questions the user has already answered.
Adapt your next question to their previous answers.
Prefer simple choices rather than requiring long paragraphs.

Use the visual analysis as inspiration, not as fixed story canon.
Let the user take the story in an unexpected direction.

VISUAL ANALYSIS:
{session["visual_analysis"][:12000]}

PREVIOUS ANSWERS:
{json.dumps(answers, ensure_ascii=False)}

Return only a JSON object in this exact format:
{{
  "question": "One short question for the user",
  "options": [
    {{"label": "Short option", "value": "Meaning of choice"}},
    {{"label": "Short option", "value": "Meaning of choice"}},
    {{"label": "Short option", "value": "Meaning of choice"}},
    {{"label": "Something else", "value": "Let me choose another direction"}}
  ]
}}

Rules:
- Return between 2 and 5 options.
- Keep each option label short enough for a Discord menu.
- Make the options meaningfully different.
- The final option should let the user steer the story themselves.
- Do not include Markdown outside the JSON.
"""

    result, provider = generate_text(prompt)
    data = extract_json(result)

    question = str(data.get("question", "")).strip()
    options = data.get("options", [])

    if not question or not isinstance(options, list):
        raise ValueError("The AI returned an incomplete question.")

    clean_options = []

    for option in options[:5]:
        if not isinstance(option, dict):
            continue

        label = str(option.get("label", "")).strip()
        value = str(option.get("value", "")).strip()

        if label and value:
            clean_options.append({
                "label": label[:100],
                "value": value[:500],
            })

    if not clean_options:
        raise ValueError("The AI did not provide usable choices.")

    return question, clean_options, provider


# ==========================================
# QUESTION MENU
# ==========================================

class StoryQuestionSelect(discord.ui.Select):
    def __init__(self, session_id, question, options):
        self.session_id = session_id
        self.question = question

        menu_options = [
            discord.SelectOption(
                label=option["label"],
                value=str(index),
                description=option["value"][:100],
            )
            for index, option in enumerate(options)
        ]

        self.option_values = options

        super().__init__(
            placeholder="Choose the direction of your story...",
            min_values=1,
            max_values=1,
            options=menu_options,
        )

    async def callback(self, interaction: discord.Interaction):
        session = get_session(self.session_id)

        if not session or session["user_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "This story session belongs to another writer.",
                ephemeral=True,
            )
            return

        if session["current_question"] != self.question:
            await interaction.response.send_message(
                "This question is no longer active. "
                "Please use your latest story question.",
                ephemeral=True,
            )
            return

        selected_index = int(self.values[0])
        selected = self.option_values[selected_index]

        await interaction.response.defer()

        try:
            save_answer(
                self.session_id,
                self.question,
                selected["value"],
            )

            next_question, options, provider = create_next_question(
                self.session_id
            )

            save_question(
                self.session_id,
                next_question,
                options,
            )

            embed = discord.Embed(
                title="Yeosang's Story Room",
                description=(
                    f"**Your choice:** {selected['label']}\n\n"
                    f"**{next_question}**"
                ),
                color=discord.Color.from_rgb(78, 0, 23),
            )

            embed.set_footer(
                text="One question at a time • Your story, your choices"
            )

            view = StoryQuestionView(
                self.session_id,
                next_question,
                options,
            )

            await interaction.edit_original_response(
                content=None,
                embed=embed,
                view=view,
            )

            logging.info(
                "Next question created using %s for session %s",
                provider,
                self.session_id,
            )

        except Exception:
            logging.exception("Failed to process story answer")

            await interaction.followup.send(
                "I saved your answer, but couldn't prepare the next "
                "question just now. Please try again shortly.",
                ephemeral=True,
            )


class StoryQuestionView(discord.ui.View):
    def __init__(self, session_id, question, options):
        super().__init__(timeout=86400)

        self.add_item(
            StoryQuestionSelect(
                session_id,
                question,
                options,
            )
        )


# ==========================================
# BOT READY
# ==========================================

@bot.event
async def on_ready():
    logging.info("Logged in as %s (%s)", bot.user, bot.user.id)

    try:
        synced = await bot.tree.sync()
        logging.info("Synced %s slash command(s)", len(synced))
    except Exception:
        logging.exception("Slash command sync failed")


# ==========================================
# /novel
# ==========================================

@bot.tree.command(
    name="novel",
    description="Start today's story using a collection board.",
)
@app_commands.describe(
    board="Upload the collection board for today's story.",
)
async def novel(
    interaction: discord.Interaction,
    board: discord.Attachment,
):
    await interaction.response.defer(thinking=True, ephemeral=False)

    existing = get_todays_session(interaction.user.id)

    if existing:
        await interaction.followup.send(
            "🖤 **Today's story session already exists.**\n\n"
            "Your collection board and story memory are safe. "
            "We'll add the remaining story controls next.",
            ephemeral=True,
        )
        return

    filename = board.filename.lower()

    allowed_extensions = (
        ".png", ".jpg", ".jpeg", ".webp", ".gif"
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
            "Please upload a PNG, JPG, JPEG, WEBP, or GIF image.",
            ephemeral=True,
        )
        return

    if board.size > 15 * 1024 * 1024:
        await interaction.followup.send(
            "That image is larger than Yeosang's current "
            "15 MB limit. Please upload a smaller image.",
            ephemeral=True,
        )
        return

    try:
        response = requests.get(board.url, timeout=30)
        response.raise_for_status()
        image_bytes = response.content
    except requests.RequestException:
        logging.exception("Failed to download collection board")

        await interaction.followup.send(
            "I couldn't retrieve that image. Please try again.",
            ephemeral=True,
        )
        return

    mime_type = board.content_type or {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(Path(filename).suffix, "image/jpeg")

    board_path = BOARD_FOLDER / (
        f"{interaction.user.id}_{today_string()}_{board.filename}"
    )

    try:
        # Save the original image for this session.
        board_path.write_bytes(image_bytes)

        # Analyze privately. The raw analysis is not posted to Discord.
        analysis_result = ai_manager.analyze_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=VISUAL_ANALYSIS_PROMPT,
        )

        visual_analysis, vision_provider = unpack_ai_result(
            analysis_result
        )

        session_id = save_session(
            user_id=interaction.user.id,
            board_path=board_path,
            board_filename=board.filename,
            mime_type=mime_type,
            visual_analysis=visual_analysis,
        )

        question, options, question_provider = create_next_question(
            session_id
        )

        save_question(session_id, question, options)

    except ValueError as error:
        logging.warning("Could not start story session: %s", error)

        await interaction.followup.send(
            f"🖤 {error}",
            ephemeral=True,
        )
        return

    except Exception:
        logging.exception("Failed to initialize story session")

        await interaction.followup.send(
            "I couldn't prepare today's story session. "
            "Please try again later.",
            ephemeral=True,
        )
        return

    embed = discord.Embed(
        title="Yeosang's Story Room",
        description=(
            "Your collection has been saved for today's story session.\n\n"
            f"**{question}**\n\n"
            "Choose an option below. I'll use your answer to decide "
            "what to ask next."
        ),
        color=discord.Color.from_rgb(78, 0, 23),
    )

    embed.set_thumbnail(url=board.url)
    embed.set_footer(
        text="Your story • Your choices • One question at a time"
    )

    view = StoryQuestionView(
        session_id,
        question,
        options,
    )

    await interaction.followup.send(
        embed=embed,
        view=view,
    )

    logging.info(
        "Started session %s | Vision: %s | Questions: %s",
        session_id,
        vision_provider,
        question_provider,
    )


# ==========================================
# START
# ==========================================

if __name__ == "__main__":
    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot.run(token)
