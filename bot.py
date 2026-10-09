
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


# ==================================================
# CONFIGURATION
# ==================================================

logging.basicConfig(level=logging.INFO)

DATABASE_PATH = Path("data/story_sessions.db")
BOARD_FOLDER = Path("data/boards")

DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
BOARD_FOLDER.mkdir(parents=True, exist_ok=True)

MAX_IMAGE_SIZE = 15 * 1024 * 1024
EMBED_COLOR = discord.Color.from_rgb(78, 0, 23)

app = Flask(__name__)


@app.route("/")
def home():
    return "Yeosang Novel is alive. 🖤"


def run_web():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )


# ==================================================
# DISCORD BOT INITIALIZATION
# ==================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)

ai_manager = AIManager()


# ==================================================
# DATABASE
# ==================================================

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
            "current_draft": "TEXT",
            "current_draft_chapter": "INTEGER",
        }

        for column, definition in migrations.items():
            if column not in columns:
                db.execute(
                    f"ALTER TABLE story_sessions "
                    f"ADD COLUMN {column} {definition}"
                )


initialize_database()


# ==================================================
# GENERAL HELPERS
# ==================================================

def now_string():
    return datetime.utcnow().isoformat()


def today_string():
    return datetime.utcnow().date().isoformat()


def get_session(session_id):
    with connect_db() as db:
        return db.execute(
            "SELECT * FROM story_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()


def get_todays_session(user_id):
    with connect_db() as db:
        return db.execute("""
            SELECT *
            FROM story_sessions
            WHERE user_id = ? AND session_date = ?
        """, (str(user_id), today_string())).fetchone()


def unpack_ai_result(result):
    if isinstance(result, tuple):
        return (
            str(result[0]),
            str(result[1]) if len(result) > 1 else "AI",
        )

    return str(result), "AI"


def generate_text(prompt):
    return unpack_ai_result(ai_manager.generate_text(prompt))


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

    if start < 0 or end <= start:
        raise ValueError("The AI did not return valid JSON.")

    return json.loads(text[start:end + 1])


def get_memory(session):
    try:
        return json.loads(session["story_memory"] or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}


def get_approved_chapters(session):
    memory = get_memory(session)
    chapters = memory.get("approved_chapters", [])
    return chapters if isinstance(chapters, list) else []


def save_session(
    user_id,
    board_path,
    board_filename,
    mime_type,
    visual_analysis,
):
    now = now_string()

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
        cursor = db.execute("""
            INSERT INTO story_sessions (
                user_id, session_date, board_path,
                board_filename, mime_type, visual_analysis,
                story_memory, status, created_at, updated_at
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
        {"question": row["question"], "answer": row["answer"]}
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

        memory = json.loads(row["story_memory"] or "{}")
        memory.setdefault("user_choices", []).append({
            "question": question,
            "answer": answer,
        })

        db.execute("""
            INSERT INTO story_answers (
                session_id, question, answer, created_at
            )
            VALUES (?, ?, ?, ?)
        """, (session_id, question, answer, now_string()))

        db.execute("""
            UPDATE story_sessions
            SET story_memory = ?,
                current_question = NULL,
                current_options = NULL,
                updated_at = ?
            WHERE id = ?
        """, (
            json.dumps(memory, ensure_ascii=False),
            now_string(),
            session_id,
        ))


# ==================================================
# PRIVATE BOARD ANALYSIS
# ==================================================

VISUAL_ANALYSIS_PROMPT = """
You are the private visual-analysis system of Yeosang Novel.

Inspect the collection board carefully.

Describe visible characters, appearance, clothing, expressions,
objects, symbols, backgrounds, atmosphere, and possible dynamics.

Separate direct visual observations from interpretations.
Do not invent names or unsupported facts.
Do not assume that a visual interpretation is confirmed canon.
Do not write a story or expose this analysis directly to the user.

This analysis is private and will help develop the novel.
"""


# ==================================================
# ADAPTIVE STORY DEVELOPMENT
# ==================================================

def evaluate_story(session_id):
    session = get_session(session_id)

    if not session:
        raise ValueError("Story session not found.")

    prompt = f"""
You are Yeosang, a thoughtful novel-writing companion.

Help the user develop an original novel based on their collection
board and their answers.

Ask only one question at a time. After each answer, decide whether
important story information is still missing or the story is ready.

Do not use a fixed question count. Use creative judgment for details
the user leaves open. Respect the user's choices.

PRIVATE VISUAL ANALYSIS:
{session["visual_analysis"][:12000]}

USER ANSWERS:
{json.dumps(get_answers(session_id), ensure_ascii=False)}

Return valid JSON only.

If more information is needed:
{{
  "status": "ask",
  "question": "One useful question",
  "options": [
    {{"label": "Choice A", "value": "Meaning of choice A"}},
    {{"label": "Choice B", "value": "Meaning of choice B"}},
    {{"label": "Choice C", "value": "Meaning of choice C"}},
    {{"label": "Something else", "value": "Let me choose another direction"}}
  ]
}}

If ready:
{{
  "status": "ready",
  "plan": {{
    "premise": "Central premise",
    "characters": "Main characters and roles",
    "relationship": "Important relationships",
    "setting": "Setting",
    "conflict": "Central conflict",
    "major_secret": "Secret or mystery, if appropriate",
    "ending": "Ending direction",
    "chapter_arc": "A coherent ten-chapter outline"
  }},
  "titles": ["Title one", "Title two", "Title three", "Title four"]
}}

The story plan must support exactly ten chapters.
Do not force unnecessary details.
Return four distinct titles when possible.
Do not include Markdown outside the JSON.
"""

    result, provider = generate_text(prompt)
    data = extract_json(result)

    if data.get("status") == "ask":
        question = str(data.get("question", "")).strip()
        options = data.get("options", [])

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

        if not question or not clean_options:
            raise ValueError("The AI returned an incomplete question.")

        return {
            "status": "ask",
            "question": question,
            "options": clean_options,
            "provider": provider,
        }

    if data.get("status") == "ready":
        plan = data.get("plan")
        titles = data.get("titles", [])

        required = [
            "premise", "characters", "relationship",
            "setting", "conflict", "ending", "chapter_arc",
        ]

        if not isinstance(plan, dict):
            raise ValueError("The AI returned an incomplete plan.")

        for key in required:
            if not str(plan.get(key, "")).strip():
                raise ValueError(f"Missing story-plan field: {key}")

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

    raise ValueError("Unknown AI readiness status.")


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
            now_string(),
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

        memory = json.loads(row["story_memory"] or "{}")
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
            now_string(),
            session_id,
        ))


# ==================================================
# STORY PLAN EMBED
# ==================================================

def build_plan_embed(session_id):
    session = get_session(session_id)

    if not session or not session["story_plan"]:
        raise ValueError("No story plan is available.")

    plan = json.loads(session["story_plan"])

    embed = discord.Embed(
        title="Your Story Plan",
        description="Review your story before approving it.",
        color=EMBED_COLOR,
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

    embed.set_footer(text="Approve the plan or request changes.")
    return embed


# ==================================================
# STORY QUESTION UI
# ==================================================

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

    async def callback(self, interaction):
        session = get_session(self.session_id)

        if not session or session["user_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "This story belongs to another writer.",
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
                    color=EMBED_COLOR,
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
                "I couldn't prepare the next step. "
                "Please check the Render logs.",
                ephemeral=True,
            )


class StoryQuestionView(discord.ui.View):
    def __init__(self, session_id, question, options):
        super().__init__(timeout=86400)
        self.add_item(
            StoryQuestionSelect(session_id, question, options)
        )


# ==================================================
# STORY PLAN REVISION
# ==================================================

class StoryRevisionModal(discord.ui.Modal):
    def __init__(self, session_id):
        super().__init__(title="Revise Your Story Plan")
        self.session_id = session_id

        self.revision = discord.ui.TextInput(
            label="What would you like to change?",
            placeholder="Describe the changes you want...",
            style=discord.TextStyle.paragraph,
            max_length=1500,
            required=True,
        )
        self.add_item(self.revision)

    async def on_submit(self, interaction):
        session = get_session(self.session_id)

        if not session or session["user_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "This story belongs to another writer.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        try:
            save_answer(
                self.session_id,
                "Requested story-plan changes",
                self.revision.value,
            )

            decision = evaluate_story(self.session_id)

            if decision["status"] == "ask":
                save_question(
                    self.session_id,
                    decision["question"],
                    decision["options"],
                )

                await interaction.followup.send(
                    f"**One quick clarification:**\n"
                    f"{decision['question']}",
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
                "I couldn't revise the plan. Please check the Render logs.",
                ephemeral=True,
            )


class StoryPlanView(discord.ui.View):
    def __init__(self, session_id):
        super().__init__(timeout=86400)
        self.session_id = session_id

    async def interaction_check(self, interaction):
        session = get_session(self.session_id)

        if not session or session["user_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "Only the writer can approve this plan.",
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
            """, (now_string(), self.session_id))

        embed = discord.Embed(
            title="Choose Your Novel's Title",
            description="\n".join(
                f"**{i + 1}.** {title}"
                for i, title in enumerate(titles)
            ),
            color=EMBED_COLOR,
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


class StoryTitleSelect(discord.ui.Select):
    def __init__(self, session_id, titles):
        self.session_id = session_id
        self.titles = titles

        options = [
            discord.SelectOption(
                label=title[:100],
                value=str(i),
            )
            for i, title in enumerate(titles[:25])
        ]

        super().__init__(
            placeholder="Choose your novel's title...",
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction):
        session = get_session(self.session_id)

        if not session or session["user_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "This story belongs to another writer.",
                ephemeral=True,
            )
            return

        if session["plan_status"] != "approved":
            await interaction.response.send_message(
                "Approve the story plan first.",
                ephemeral=True,
            )
            return

        title = self.titles[int(self.values[0])]

        with connect_db() as db:
            row = db.execute("""
                SELECT story_memory
                FROM story_sessions WHERE id = ?
            """, (self.session_id,)).fetchone()

            memory = json.loads(row["story_memory"] or "{}")
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
                now_string(),
                self.session_id,
            ))

        embed = discord.Embed(
            title="Your Novel Is Ready",
            description=(
                f"**{title}**\n\n"
                "Your plan and title are saved.\n"
                "Use `/write` to generate Chapter 1."
            ),
            color=EMBED_COLOR,
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=None,
        )


class StoryTitleView(discord.ui.View):
    def __init__(self, session_id, titles):
        super().__init__(timeout=86400)
        self.add_item(StoryTitleSelect(session_id, titles))


# ==================================================
# CHAPTER DISPLAY HELPERS
# ==================================================

def split_chapter_text(text, limit=3800):
    text = text.strip()
    chunks = []

    while len(text) > limit:
        split_at = text.rfind("\n", 0, limit)

        if split_at < limit // 2:
            split_at = text.rfind(" ", 0, limit)

        if split_at < limit // 2:
            split_at = limit

        chunks.append(text[:split_at].strip())
        text = text[split_at:].strip()

    if text:
        chunks.append(text)

    return chunks or ["The chapter draft is empty."]


def chapter_embeds(session, chapter_number, chapter_text, draft=True):
    title = session["chosen_title"] or "Untitled Novel"
    heading = f"Chapter {chapter_number}"

    chunks = split_chapter_text(chapter_text)

    # Discord permits up to 10 embeds per message.
    # Keep a chapter within that limit.
    if len(chunks) > 10:
        chunks = chunks[:10]
        chunks[-1] = (
            chunks[-1][:3500]
            + "\n\n[Draft shortened to fit Discord's display limit.]"
        )

    embeds = []

    for index, chunk in enumerate(chunks):
        embed = discord.Embed(
            title=f"{title} | {heading}" if index == 0 else heading,
            description=chunk,
            color=EMBED_COLOR,
        )

        if index == 0:
            embed.set_author(name="Yeosang Novel")
            embed.set_footer(
                text=(
                    "Draft • Review and edit before /post"
                    if draft
                    else "Official chapter"
                )
            )
        else:
            embed.set_footer(
                text=f"{heading} • Part {index + 1}"
            )

        embeds.append(embed)

    return embeds


# ==================================================
# CHAPTER EDITING
# ==================================================

class ChapterEditModal(discord.ui.Modal):
    def __init__(self, session_id):
        super().__init__(title="Edit Chapter Draft")
        self.session_id = session_id

        session = get_session(session_id)
        draft = session["current_draft"] if session else ""

        self.edited_text = discord.ui.TextInput(
            label="Revised chapter text",
            style=discord.TextStyle.paragraph,
            default=(draft or "")[:4000],
            max_length=4000,
            required=True,
        )

        self.add_item(self.edited_text)

    async def on_submit(self, interaction):
        session = get_session(self.session_id)

        if not session or session["user_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "Only the writer can edit this chapter.",
                ephemeral=True,
            )
            return

        if not session["current_draft"]:
            await interaction.response.send_message(
                "There is no draft waiting for edits.",
                ephemeral=True,
            )
            return

        if len(session["current_draft"]) > 4000:
            await interaction.response.send_message(
                "This chapter is too long to edit in one Discord modal. "
                "You can still approve it with `/post`.",
                ephemeral=True,
            )
            return

        with connect_db() as db:
            db.execute("""
                UPDATE story_sessions
                SET current_draft = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                self.edited_text.value,
                now_string(),
                self.session_id,
            ))

        await interaction.response.send_message(
            "🖤 Your revised draft has been saved. "
            "Use `/post` when you're ready to approve it.",
            ephemeral=True,
        )


class ChapterDraftView(discord.ui.View):
    def __init__(self, session_id):
        super().__init__(timeout=86400)
        self.session_id = session_id

    async def interaction_check(self, interaction):
        session = get_session(self.session_id)

        if not session or session["user_id"] != str(interaction.user.id):
            await interaction.response.send_message(
                "Only the writer can manage this draft.",
                ephemeral=True,
            )
            return False

        return True

    @discord.ui.button(
        label="Edit Draft",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
    )
    async def edit_draft(self, interaction, button):
        session = get_session(self.session_id)

        if not session or not session["current_draft"]:
            await interaction.response.send_message(
                "There is no active chapter draft.",
                ephemeral=True,
            )
            return

        if len(session["current_draft"]) > 4000:
            await interaction.response.send_message(
                "This chapter exceeds Discord's 4,000-character modal "
                "limit. You can still review it and use `/post`.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            ChapterEditModal(self.session_id)
        )


# ==================================================
# /NOVEL
# ==================================================

@bot.tree.command(
    name="novel",
    description="Start today's novel using a collection board.",
)
@app_commands.describe(board="Upload today's collection board.")
async def novel(interaction: discord.Interaction, board: discord.Attachment):
    await interaction.response.defer(thinking=True)

    if get_todays_session(interaction.user.id):
        await interaction.followup.send(
            "🖤 You already have a story session today. "
            "Your existing session has been preserved.",
            ephemeral=True,
        )
        return

    filename = board.filename.lower()
    extensions = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    is_image = (
        bool(board.content_type and board.content_type.startswith("image/"))
        or filename.endswith(extensions)
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
        board_path.write_bytes(image_bytes)

        analysis_result = ai_manager.analyze_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=VISUAL_ANALYSIS_PROMPT,
        )
        visual_analysis, provider = unpack_ai_result(analysis_result)

        session_id = save_session(
            interaction.user.id,
            board_path,
            board.filename,
            mime_type,
            visual_analysis,
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
                    "Your collection board is saved for today's story.\n\n"
                    f"**{decision['question']}**"
                ),
                color=EMBED_COLOR,
            )
            embed.set_thumbnail(url=board.url)
            embed.set_footer(text="One question at a time • Your story")

            await interaction.followup.send(
                embed=embed,
                view=StoryQuestionView(
                    session_id,
                    decision["question"],
                    decision["options"],
                ),
            )
        else:
            save_plan(session_id, decision["plan"], decision["titles"])

            await interaction.followup.send(
                "🖤 **Yeosang has enough information to prepare your plan.**",
                embed=build_plan_embed(session_id),
                view=StoryPlanView(session_id),
            )

        logging.info(
            "Story session %s created using %s",
            session_id,
            provider,
        )

    except Exception:
        logging.exception("Failed to start novel session")
        await interaction.followup.send(
            "I couldn't prepare the story session. "
            "Please check the Render logs.",
            ephemeral=True,
        )


# ==================================================
# /WRITE
# Generates exactly one unapproved chapter at a time.
# ==================================================

@bot.tree.command(
    name="write",
    description="Generate the next chapter of your approved novel.",
)
async def write(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True)

    session = get_todays_session(interaction.user.id)

    if not session:
        await interaction.followup.send(
            "Start today's story first with `/novel`.",
            ephemeral=True,
        )
        return

    if session["plan_status"] != "approved":
        await interaction.followup.send(
            "Approve your story plan and choose a title before using `/write`.",
            ephemeral=True,
        )
        return

    if not session["chosen_title"]:
        await interaction.followup.send(
            "Choose your novel's title before writing chapters.",
            ephemeral=True,
        )
        return

    if session["current_draft"]:
        chapter_number = session["current_draft_chapter"]
        embeds = chapter_embeds(
            session,
            chapter_number,
            session["current_draft"],
            draft=True,
        )

        await interaction.followup.send(
            content=(
                f"Your Chapter {chapter_number} draft is still waiting "
                "for approval. Review it below, edit it if needed, "
                "then use `/post`."
            ),
            embeds=embeds,
            view=ChapterDraftView(session["id"]),
        )
        return

    approved = get_approved_chapters(session)

    if len(approved) >= 10:
        await interaction.followup.send(
            "🎉 All 10 chapters have already been approved. "
            "Your novel is complete!",
            ephemeral=True,
        )
        return

    chapter_number = len(approved) + 1
    plan = json.loads(session["story_plan"] or "{}")
    memory = get_memory(session)

    prompt = f"""
You are Yeosang, an experienced novelist and writing partner.

Write Chapter {chapter_number} of the user's novel.

NOVEL TITLE:
{session["chosen_title"]}

APPROVED STORY PLAN:
{json.dumps(plan, ensure_ascii=False, indent=2)}

PRIVATE COLLECTION-BOARD ANALYSIS:
{session["visual_analysis"][:10000]}

USER'S STORY CHOICES:
{json.dumps(memory.get("user_choices", []), ensure_ascii=False)}

PREVIOUS APPROVED CHAPTERS:
{json.dumps(approved, ensure_ascii=False)}

CHAPTER NUMBER:
{chapter_number} of 10

INSTRUCTIONS:
- Write only Chapter {chapter_number}, not multiple chapters.
- Continue naturally from the previous approved chapter.
- For Chapter 1, establish the setting, characters, atmosphere,
  and central narrative without rushing.
- Follow the approved premise, relationships, conflict, and ending.
- Maintain consistent character personalities and established facts.
- Do not contradict previous approved chapters.
- Do not reveal secrets earlier than the story plan intends.
- Use immersive prose, meaningful dialogue, and developed scenes.
- Avoid summaries in place of actual scenes.
- Avoid one-line prose paragraphs throughout the chapter.
- Do not add an out-of-story explanation or ask the user questions.
- Do not write the next chapter.
- Return the chapter title followed by the complete chapter prose.
- Aim for a substantial chapter with a natural beginning and ending.
- The chapter should feel like part of one continuous novel.
"""

    try:
        chapter_text, provider = generate_text(prompt)
        chapter_text = chapter_text.strip()

        if not chapter_text:
            raise ValueError("The AI returned an empty chapter.")

        # Save the draft before displaying it, so a failed Discord
        # response does not lose the generated text.
        with connect_db() as db:
            db.execute("""
                UPDATE story_sessions
                SET current_draft = ?,
                    current_draft_chapter = ?,
                    status = 'chapter_draft',
                    updated_at = ?
                WHERE id = ?
            """, (
                chapter_text,
                chapter_number,
                now_string(),
                session["id"],
            ))

        refreshed = get_session(session["id"])
        embeds = chapter_embeds(
            refreshed,
            chapter_number,
            chapter_text,
            draft=True,
        )

        await interaction.followup.send(
            content=(
                f"📖 **Chapter {chapter_number} draft is ready.**\n"
                "Review it before approving. Use the Edit Draft button "
                "for short edits, or `/post` when you're satisfied."
            ),
            embeds=embeds,
            view=ChapterDraftView(session["id"]),
        )

        logging.info(
            "Generated Chapter %s for session %s using %s",
            chapter_number,
            session["id"],
            provider,
        )

    except Exception:
        logging.exception("Failed to generate chapter")
        await interaction.followup.send(
            "I couldn't generate the chapter. Your existing approved "
            "chapters have not been changed. Please check the Render logs.",
            ephemeral=True,
        )


# ==================================================
# /POST
# Approves and saves the current draft as official.
# ==================================================

@bot.tree.command(
    name="post",
    description="Approve and save your current chapter.",
)
async def post(interaction: discord.Interaction):
    await interaction.response.defer(thinking=True, ephemeral=True)

    session = get_todays_session(interaction.user.id)

    if not session:
        await interaction.followup.send(
            "There is no story session for today. Start with `/novel`.",
            ephemeral=True,
        )
        return

    if not session["current_draft"]:
        await interaction.followup.send(
            "There is no chapter draft waiting for approval. "
            "Use `/write` to generate the next chapter.",
            ephemeral=True,
        )
        return

    if session["plan_status"] != "approved" or not session["chosen_title"]:
        await interaction.followup.send(
            "Approve your story plan and choose a title first.",
            ephemeral=True,
        )
        return

    approved = get_approved_chapters(session)
    expected_number = len(approved) + 1
    draft_number = session["current_draft_chapter"]

    if draft_number != expected_number:
        await interaction.followup.send(
            "The draft number doesn't match the next chapter. "
            "Nothing was posted. Please check the Render logs.",
            ephemeral=True,
        )
        return

    if expected_number > 10:
        await interaction.followup.send(
            "All 10 chapters are already approved.",
            ephemeral=True,
        )
        return

    chapter_text = session["current_draft"].strip()

    if not chapter_text:
        await interaction.followup.send(
            "The chapter draft is empty. Generate it again with `/write`.",
            ephemeral=True,
        )
        return

    chapter_record = {
        "chapter_number": expected_number,
        "text": chapter_text,
        "approved_at": now_string(),
    }

    memory = get_memory(session)
    memory.setdefault("approved_chapters", []).append(chapter_record)

    completed = expected_number == 10

    # A single transaction saves the chapter and clears the draft.
    with connect_db() as db:
        current = db.execute("""
            SELECT current_draft, current_draft_chapter, story_memory
            FROM story_sessions
            WHERE id = ?
        """, (session["id"],)).fetchone()

        if (
            not current
            or current["current_draft"] != session["current_draft"]
            or current["current_draft_chapter"] != expected_number
        ):
            await interaction.followup.send(
                "The draft changed while you were approving it. "
                "Nothing was saved. Please run `/write` again.",
                ephemeral=True,
            )
            return

        db.execute("""
            UPDATE story_sessions
            SET story_memory = ?,
                current_draft = NULL,
                current_draft_chapter = NULL,
                status = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            json.dumps(memory, ensure_ascii=False),
            "completed" if completed else "ready_to_write",
            now_string(),
            session["id"],
        ))

    if completed:
        embed = discord.Embed(
            title="🎉 Your Novel Is Complete",
            description=(
                f"**{session['chosen_title']}**\n\n"
                "Chapter 10 has been approved. All ten official chapters "
                "are saved in your story memory.\n\n"
                "The novel is complete. Public website publishing "
                "will be added separately."
            ),
            color=EMBED_COLOR,
        )
    else:
        embed = discord.Embed(
            title=f"Chapter {expected_number} Approved",
            description=(
                f"**{session['chosen_title']}**\n\n"
                "This chapter is now part of the official novel.\n"
                f"**Progress:** {expected_number}/10 chapters\n\n"
                "When you're ready, use `/write` for the next chapter."
            ),
            color=EMBED_COLOR,
        )

    await interaction.followup.send(embed=embed, ephemeral=True)

    logging.info(
        "Approved Chapter %s for session %s",
        expected_number,
        session["id"],
    )


# ==================================================
# BOT STARTUP
# ==================================================

@bot.event
async def on_ready():
    logging.info("Logged in as %s (%s)", bot.user, bot.user.id)

    try:
        synced = await bot.tree.sync()
        logging.info("Synced %s slash command(s).", len(synced))
    except Exception:
        logging.exception("Failed to sync slash commands")


if __name__ == "__main__":
    threading.Thread(
        target=run_web,
        daemon=True,
    ).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot.run(token)
