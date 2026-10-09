
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

MAX_IMAGE_SIZE = 15 * 1024 * 1024

app = Flask(__name__)


@app.route("/")
def home():
    return "Yeosang Novel is alive. 🖤"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


# ==========================================
# DISCORD BOT INITIALIZATION
# IMPORTANT: MUST BE BEFORE COMMANDS
# ==========================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


# ==========================================
# DATABASE INITIALIZATION
# ==========================================

def connect_db():
    db = sqlite3.connect(DATABASE_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


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
                    ON DELETE CASCADE
            )
        """)

        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(story_sessions)"
            ).fetchall()
        }

        migrations = {
            "story_plan": "TEXT",
            "title_options": "TEXT",
            "chosen_title": "TEXT",
            "plan_status": "TEXT NOT NULL DEFAULT 'developing'",
        }

        for column, definition in migrations.items():
            if column not in columns:
                db.execute(
                    f"ALTER TABLE story_sessions "
                    f"ADD COLUMN {column} {definition}"
                )


initialize_database()


# ==========================================
# SESSION HELPERS
# ==========================================

def today_string():
    # Uses UTC. A configurable local timezone can be added later.
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
        "story_plan": None,
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
                "You already have a story session today. "
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


def get_answers(session_id):
    with connect_db() as db:
        rows = db.execute("""
            SELECT question, answer
            FROM story_answers
            WHERE session_id = ?
            ORDER BY id
        """, (session_id,)).fetchall()

    return [
        {
            "question": row["question"],
            "answer": row["answer"],
        }
        for row in rows
    ]


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
                session_id,
                question,
                answer,
                created_at
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
# AI SYSTEM
# ==========================================

ai_manager = AIManager()

VISUAL_ANALYSIS_PROMPT = """
You are the private visual-analysis system of Yeosang Novel.

Carefully inspect the uploaded collection board.

Describe visible characters, their appearances, clothing,
expressions, objects, symbols, backgrounds, atmosphere,
and possible relationship dynamics.

Separate direct visual observations from interpretations.
Do not invent character names or unsupported facts.
Do not assume fandom canon.
Do not write a story.
Do not display story ideas to the user.

This analysis will be stored privately and used to help
the user develop an original novel.
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

    if start == -1 or end <= start:
        raise ValueError("The AI did not return valid JSON.")

    return json.loads(text[start:end + 1])


# ==========================================
# ADAPTIVE STORY READINESS SYSTEM
# ==========================================

def evaluate_story(session_id):
    session = get_session(session_id)

    if not session:
        raise ValueError("Story session not found.")

    answers = get_answers(session_id)

    prompt = f"""
You are Yeosang, an adaptive novel-writing companion.

Help the user develop an original ten-chapter novel based
on a collection board and their own creative choices.

After every answer, decide whether to ask another question
or prepare the final story plan.

Do not use a fixed question count.
Ask only when important story information is missing.
A detailed answer may resolve several decisions at once.
Do not ask the user to decide every minor detail.
Use your creative judgment for details they leave open.

Respect the user's choices. Visual interpretations are
inspiration, not confirmed story facts.

VISUAL ANALYSIS:
{session["visual_analysis"][:12000]}

USER ANSWERS:
{json.dumps(answers, ensure_ascii=False)}

Return exactly one of these JSON formats.

WHEN MORE INFORMATION IS NEEDED:
{{
  "status": "ask",
  "question": "One natural, useful question",
  "options": [
    {{"label": "Short choice", "value": "Meaning of choice"}},
    {{"label": "Short choice", "value": "Meaning of choice"}},
    {{"label": "Short choice", "value": "Meaning of choice"}},
    {{"label": "Something else", "value": "Let me choose another direction"}}
  ]
}}

WHEN THE STORY IS READY:
{{
  "status": "ready",
  "plan": {{
    "premise": "The central story premise",
    "characters": "Main characters and their roles",
    "relationship": "Important relationships",
    "setting": "Where and when the story takes place",
    "conflict": "The central problem and stakes",
    "major_secret": "A secret or mystery, if appropriate",
    "ending": "The intended ending and emotional tone",
    "chapter_arc": "How the story develops across ten chapters"
  }},
  "titles": [
    "Title option one",
    "Title option two",
    "Title option three",
    "Title option four"
  ]
}}

READINESS RULES:
- The central premise must be understandable.
- The main characters and important dynamics must be clear.
- There must be a workable central conflict.
- The intended ending direction should be clear enough.
- Do not force unnecessary details into the story.
- Ask one question only, never a questionnaire.
- If ready, create a coherent ten-chapter story plan.
- Suggest four distinct titles that suit the story.
- Return valid JSON only, without Markdown.
"""

    result, provider = generate_text(prompt)
    data = extract_json(result)

    if data.get("status") == "ask":
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
            raise ValueError("The AI returned no usable choices.")

        return {
            "status": "ask",
            "question": question,
            "options": clean_options,
            "provider": provider,
        }

    if data.get("status") == "ready":
        plan = data.get("plan")
        titles = data.get("titles", [])

        required_fields = [
            "premise",
            "characters",
            "relationship",
            "setting",
            "conflict",
            "ending",
            "chapter_arc",
        ]

        if not isinstance(plan, dict):
            raise ValueError("The AI returned an incomplete story plan.")

        for field in required_fields:
            if not str(plan.get(field, "")).strip():
                raise ValueError(
                    f"The story plan is missing: {field}"
                )

        if not isinstance(titles, list):
            titles = []

        titles = [
            str(title).strip()[:100]
            for title in titles
            if str(title).strip()
        ][:4]

        if not titles:
            titles = ["A Story Yet to Be Named"]

        return {
            "status": "ready",
            "plan": plan,
            "titles": titles,
            "provider": provider,
        }

    raise ValueError("The AI returned an unknown readiness status.")


def save_question(session_id, question, options):
    with connect_db() as db:
        db.execute("""
            UPDATE story_sessions
            SET current_question = ?,
                current_options = ?,
                status = 'developing',
                plan_status = 'developing',
                updated_at = ?
            WHERE id = ?
        """, (
            question,
            json.dumps(options, ensure_ascii=False),
            datetime.utcnow().isoformat(),
            session_id,
        ))


def save_plan(session_id, plan, titles):
    with connect_db() as db:
        row = db.execute("""
            SELECT story_memory
            FROM story_sessions
            WHERE id = ?
        """, (session_id,)).fetchone()

        if not row:
            raise ValueError("Story session not found.")

        memory = json.loads(row["story_memory"])
        memory["story_plan"] = plan

        db.execute("""
            UPDATE story_sessions
            SET story_plan = ?,
                title_options = ?,
                story_memory = ?,
                current_question = NULL,
                current_options = NULL,
                status = 'plan_ready',
                plan_status = 'awaiting_approval',
                updated_at = ?
            WHERE id = ?
        """, (
            json.dumps(plan, ensure_ascii=False),
            json.dumps(titles, ensure_ascii=False),
            json.dumps(memory, ensure_ascii=False),
            datetime.utcnow().isoformat(),
            session_id,
        ))


# ==========================================
# STORY PLAN EMBED
# ==========================================

def build_plan_embed(session_id):
    session = get_session(session_id)

    if not session or not session["story_plan"]:
        raise ValueError("No story plan is available.")

    plan = json.loads(session["story_plan"])

    embed = discord.Embed(
        title="Your Story Plan",
        description=(
            "Yeosang has enough information to prepare your story.\n"
            "Review the plan before writing begins."
        ),
        color=discord.Color.from_rgb(78, 0, 23),
    )

    fields = [
        ("Premise", "premise"),
        ("Main Characters", "characters"),
        ("Relationships", "relationship"),
        ("Setting", "setting"),
        ("Central Conflict", "conflict"),
        ("Secret or Mystery", "major_secret"),
        ("Ending Direction", "ending"),
        ("Ten-Chapter Journey", "chapter_arc"),
    ]

    for label, key in fields:
        value = str(plan.get(key, "")).strip()

        if value:
            embed.add_field(
                name=label,
                value=value[:1024],
                inline=False,
            )

    embed.set_footer(
        text="Review the plan • Approve it or request changes"
    )

    return embed


# ==========================================
# QUESTION SELECT MENU
# ==========================================

class StoryQuestionSelect(discord.ui.Select):
    def __init__(self, session_id, question, options):
        self.session_id = session_id
        self.question = question
        self.option_values = options

        menu_options = [
            discord.SelectOption(
                label=option["label"],
                value=str(index),
                description=option["value"][:100],
            )
            for index, option in enumerate(options)
        ]

        super().__init__(
            placeholder="Choose your story direction...",
            min_values=1,
            max_values=1,
            options=menu_options,
        )

    async def callback(self, interaction: discord.Interaction):
        session = get_session(self.session_id)

        if (
            not session
            or session["user_id"] != str(interaction.user.id)
        ):
            await interaction.response.send_message(
                "This story session belongs to another writer.",
                ephemeral=True,
            )
            return

        if session["current_question"] != self.question:
            await interaction.response.send_message(
                "This question is no longer active.",
                ephemeral=True,
            )
            return

        selected = self.option_values[int(self.values[0])]

        await interaction.response.defer()

        try:
            save_answer(
                self.session_id,
                self.question,
                selected["value"],
            )

            decision = evaluate_story(self.session_id)

            if decision["status"] == "ask":
                save_question(
                    self.session_id,
                    decision["question"],
                    decision["options"],
                )

                embed = discord.Embed(
                    title="Yeosang's Story Room",
                    description=(
                        f"**Your choice:** {selected['label']}\n\n"
                        f"**{decision['question']}**"
                    ),
                    color=discord.Color.from_rgb(78, 0, 23),
                )

                embed.set_footer(
                    text="One question at a time • Your story, your choices"
                )

                await interaction.edit_original_response(
                    content=None,
                    embed=embed,
                    view=StoryQuestionView(
                        self.session_id,
                        decision["question"],
                        decision["options"],
                    ),
                )

            else:
                save_plan(
                    self.session_id,
                    decision["plan"],
                    decision["titles"],
                )

                await interaction.edit_original_response(
                    content="🖤 **Story development is complete.**",
                    embed=build_plan_embed(self.session_id),
                    view=StoryPlanView(self.session_id),
                )

        except Exception:
            logging.exception("Failed to process story answer")

            await interaction.followup.send(
                "Your answer may have been saved, but I couldn't "
                "prepare the next step. Please check the Render logs.",
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
# STORY PLAN REVISION MODAL
# ==========================================

class StoryRevisionModal(discord.ui.Modal):
    def __init__(self, session_id):
        super().__init__(title="Revise Your Story Plan")

        self.session_id = session_id

        self.revision = discord.ui.TextInput(
            label="What would you like to change?",
            placeholder=(
                "For example: change the setting, make the "
                "relationship darker, or add a betrayal."
            ),
            style=discord.TextStyle.paragraph,
            max_length=1500,
            required=True,
        )

        self.add_item(self.revision)

    async def on_submit(self, interaction: discord.Interaction):
        session = get_session(self.session_id)

        if (
            not session
            or session["user_id"] != str(interaction.user.id)
        ):
            await interaction.response.send_message(
                "This story session belongs to another writer.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            save_answer(
                self.session_id,
                "Requested changes to the story plan",
                self.revision.value,
            )

            decision = evaluate_story(self.session_id)

            if decision["status"] == "ask":
                save_question(
                    self.session_id,
                    decision["question"],
                    decision["options"],
                )

                embed = discord.Embed(
                    title="Let's Refine the Story",
                    description=(
                        "I'll clarify one detail before preparing "
                        "the revised plan.\n\n"
                        f"**{decision['question']}**"
                    ),
                    color=discord.Color.from_rgb(78, 0, 23),
                )

                await interaction.followup.send(
                    embed=embed,
                    view=StoryQuestionView(
                        self.session_id,
                        decision["question"],
                        decision["options"],
                    ),
                )

            else:
                save_plan(
                    self.session_id,
                    decision["plan"],
                    decision["titles"],
                )

                await interaction.followup.send(
                    "🖤 **Here's your revised story plan.**",
                    embed=build_plan_embed(self.session_id),
                    view=StoryPlanView(self.session_id),
                )

        except Exception:
            logging.exception("Failed to revise story plan")

            await interaction.followup.send(
                "I couldn't revise the plan right now. "
                "Please check the Render logs.",
                ephemeral=True,
            )


# ==========================================
# STORY PLAN APPROVAL
# ==========================================

class StoryPlanView(discord.ui.View):
    def __init__(self, session_id):
        super().__init__(timeout=86400)
        self.session_id = session_id

    async def interaction_check(self, interaction):
        session = get_session(self.session_id)

        if (
            not session
            or session["user_id"] != str(interaction.user.id)
        ):
            await interaction.response.send_message(
                "Only the writer who started this story can approve it.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Approve Story Plan",
        style=discord.ButtonStyle.success,
        emoji="📖",
    )
    async def approve_plan(self, interaction, button):
        session = get_session(self.session_id)

        if session["plan_status"] != "awaiting_approval":
            await interaction.response.send_message(
                "This plan has already been approved or replaced.",
                ephemeral=True,
            )
            return

        titles = json.loads(session["title_options"] or "[]")

        if not titles:
            titles = ["A Story Yet to Be Named"]

        with connect_db() as db:
            db.execute("""
                UPDATE story_sessions
                SET plan_status = 'approved',
                    status = 'title_selection',
                    updated_at = ?
                WHERE id = ?
            """, (
                datetime.utcnow().isoformat(),
                self.session_id,
            ))

        embed = discord.Embed(
            title="Choose Your Novel's Title",
            description="\n".join(
                f"**{index + 1}.** {title}"
                for index, title in enumerate(titles)
            ),
            color=discord.Color.from_rgb(78, 0, 23),
        )

        await interaction.response.send_message(
            "Your story plan is approved! Choose a title.",
            embed=embed,
            view=StoryTitleView(self.session_id, titles),
        )

        self.stop()

    @discord.ui.button(
        label="Request Changes",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
    )
    async def request_changes(self, interaction, button):
        await interaction.response.send_modal(
            StoryRevisionModal(self.session_id)
        )


# ==========================================
# TITLE SELECTION
# ==========================================

class StoryTitleSelect(discord.ui.Select):
    def __init__(self, session_id, titles):
        self.session_id = session_id
        self.titles = titles

        options = [
            discord.SelectOption(
                label=title[:100],
                value=str(index),
            )
            for index, title in enumerate(titles[:25])
        ]

        super().__init__(
            placeholder="Choose the title of your novel...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        session = get_session(self.session_id)

        if (
            not session
            or session["user_id"] != str(interaction.user.id)
        ):
            await interaction.response.send_message(
                "This story session belongs to another writer.",
                ephemeral=True,
            )
            return

        if session["plan_status"] != "approved":
            await interaction.response.send_message(
                "Approve the story plan before choosing a title.",
                ephemeral=True,
            )
            return

        title = self.titles[int(self.values[0])]

        with connect_db() as db:
            row = db.execute("""
                SELECT story_memory
                FROM story_sessions
                WHERE id = ?
            """, (self.session_id,)).fetchone()

            memory = json.loads(row["story_memory"])
            memory["title"] = title

            db.execute("""
                UPDATE story_sessions
                SET chosen_title = ?,
                    story_memory = ?,
                    status = 'ready_to_write',
                    updated_at = ?
                WHERE id = ?
            """, (
                title,
                json.dumps(memory, ensure_ascii=False),
                datetime.utcnow().isoformat(),
                self.session_id,
            ))

        embed = discord.Embed(
            title="Your Novel Is Ready",
            description=(
                f"**{title}**\n\n"
                "Your story plan and title have been saved.\n\n"
                "The next step is Chapter 1 using `/write`."
            ),
            color=discord.Color.from_rgb(78, 0, 23),
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=None,
        )


class StoryTitleView(discord.ui.View):
    def __init__(self, session_id, titles):
        super().__init__(timeout=86400)

        self.add_item(
            StoryTitleSelect(session_id, titles)
        )


# ==========================================
# /novel COMMAND
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
    await interaction.response.defer(thinking=True)

    if get_todays_session(interaction.user.id):
        await interaction.followup.send(
            "🖤 You already have a story session today. "
            "Your existing session has been preserved.",
            ephemeral=True,
        )
        return

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
            "Please upload a PNG, JPG, JPEG, WEBP, or GIF image.",
            ephemeral=True,
        )
        return

    if board.size > MAX_IMAGE_SIZE:
        await interaction.followup.send(
            "Please upload an image smaller than 15 MB.",
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
            "I couldn't retrieve the uploaded image. Please try again.",
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
        board_path.write_bytes(image_bytes)

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

        decision = evaluate_story(session_id)

        if decision["status"] == "ask":
            save_question(
                session_id,
                decision["question"],
                decision["options"],
            )

            embed = discord.Embed(
                title="Yeosang's Story Room",
                description=(
                    "Your collection has been saved for today's story.\n\n"
                    f"**{decision['question']}**\n\n"
                    "Choose an option below. Your answer will shape "
                    "the next question."
                ),
                color=discord.Color.from_rgb(78, 0, 23),
            )

            embed.set_thumbnail(url=board.url)
            embed.set_footer(
                text="Your story • Your choices • One question at a time"
            )

            view = StoryQuestionView(
                session_id,
                decision["question"],
                decision["options"],
            )

            await interaction.followup.send(
                embed=embed,
                view=view,
            )

        else:
            save_plan(
                session_id,
                decision["plan"],
                decision["titles"],
            )

            await interaction.followup.send(
                "🖤 **Yeosang has enough information to begin.**",
                embed=build_plan_embed(session_id),
                view=StoryPlanView(session_id),
            )

        logging.info(
            "Session %s started. Vision provider: %s",
            session_id,
            vision_provider,
        )

    except Exception:
        logging.exception("Failed to initialize story session")

        await interaction.followup.send(
            "I couldn't prepare the story session. "
            "Please check the Render logs for the error.",
            ephemeral=True,
        )


# ==========================================
# START BOT AND WEB SERVER
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
