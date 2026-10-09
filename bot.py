
import os
import json
import sqlite3
import threading
import logging
from datetime import datetime, timezone
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
MAX_CHAPTERS = 10

app = Flask(__name__)
ai_manager = AIManager()


@app.route("/")
def home():
    return "Yeosang Novel is alive. 🖤"


def run_web():
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )


# ==================================================
# DISCORD BOT
# ==================================================

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
)


# ==================================================
# DATABASE
# ==================================================

def now_string():
    return datetime.now(timezone.utc).isoformat()


def today_string():
    return datetime.now(timezone.utc).date().isoformat()


def connect_db():
    db = sqlite3.connect(DATABASE_PATH, timeout=30)
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
# STORY MEMORY
# ==================================================

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


def get_memory(session):
    try:
        memory = json.loads(session["story_memory"] or "{}")
    except (TypeError, json.JSONDecodeError):
        memory = {}

    defaults = {
        "collection": {},
        "user_choices": [],
        "story_plan": None,
        "title": None,
        "approved_chapters": [],
        "character_tracker": [],
        "brainstormed_ideas": [],
        "approved_twists": [],
        "foreshadowing": [],
        "continuity_rules": [],
        "unresolved_threads": [],
        "chapter_notes": [],
        "pending_memory_update": None,
        "rejected_ideas": [],
    }

    for key, default in defaults.items():
        if key not in memory:
            memory[key] = default.copy() if isinstance(default, (list, dict)) else default

    return memory


def save_memory(session_id, memory):
    with connect_db() as db:
        db.execute("""
            UPDATE story_sessions
            SET story_memory = ?, updated_at = ?
            WHERE id = ?
        """, (
            json.dumps(memory, ensure_ascii=False),
            now_string(),
            session_id,
        ))


def get_approved_chapters(session):
    chapters = get_memory(session).get("approved_chapters", [])
    return chapters if isinstance(chapters, list) else []


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


def unpack_ai_result(result):
    if isinstance(result, tuple):
        text_result = str(result[0])
        provider = str(result[1]) if len(result) > 1 else "AI"
        return text_result, provider

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
        "character_tracker": [],
        "brainstormed_ideas": [],
        "approved_twists": [],
        "foreshadowing": [],
        "continuity_rules": [],
        "unresolved_threads": [],
        "chapter_notes": [],
        "pending_memory_update": None,
        "rejected_ideas": [],
    }

    with connect_db() as db:
        existing = db.execute("""
            SELECT id FROM story_sessions
            WHERE user_id = ? AND session_date = ?
        """, (str(user_id), today_string())).fetchone()

        if existing:
            raise ValueError(
                "You already have a story session today."
            )

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


def save_answer(session_id, question, answer):
    with connect_db() as db:
        row = db.execute("""
            SELECT story_memory
            FROM story_sessions
            WHERE id = ?
        """, (session_id,)).fetchone()

        if not row:
            raise ValueError("Story session not found.")

        try:
            memory = json.loads(row["story_memory"] or "{}")
        except json.JSONDecodeError:
            memory = {}

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

Identify distinct visible characters when possible. Describe each
one separately so the writing system can track the intended cast.

Separate direct observations from interpretations.
Do not invent names or unsupported facts.
Do not assume visual interpretations are confirmed canon.
Do not write a story or expose this analysis directly to the user.

This analysis will help the user develop an original novel.
"""


# ==================================================
# CREATIVE CO-AUTHOR INSTRUCTIONS
# ==================================================

CREATIVE_DIRECTIVE = """
CREATIVE CO-AUTHOR MODE: YEOSANG

You are not merely a question generator or a passive writing tool.
You are an imaginative, proactive novel-writing partner.

INDEPENDENT BRAINSTORMING:
- Develop your own original plot ideas, secrets, conflicts,
  mysteries, reversals, emotional moments, and revelations.
- Do not wait for the user to invent every development.
- Look for less obvious but coherent possibilities.
- Prefer twists that change the meaning of earlier events.
- Plant clues before revelations and make consequences matter.
- Avoid random shock twists, convenient coincidences, and clichés
  unless deliberately transformed into something fresh.
- Maintain a private pool of possible ideas and remember them.
- Distinguish suggestions from user-approved story canon.

ALL SIX CHARACTERS ARE ESSENTIAL:
- The intended novel has six important characters.
- Track all six separately throughout planning and writing.
- Give each a distinct identity, motivation, personality,
  relationships, strengths, weaknesses, and meaningful agency.
- Give every character a genuine contribution to the plot.
- Do not reduce any of the six to a decorative background role.
- Do not let only two or three characters dominate the entire novel.
- Track each character's appearances, decisions, development,
  secrets, relationships, and consequences.
- Use the board analysis and user answers to identify the cast.
- Never fabricate character names or claim uncertain identities
  are confirmed.
- If the six intended characters cannot be identified confidently,
  ask the user a focused question before finalising the plan.

STORY QUALITY:
- The novel must have exactly ten chapters.
- Every chapter must advance the plot, a character arc,
  a relationship, a mystery, or the stakes.
- Balance tension, emotional depth, atmosphere, dialogue,
  quiet scenes, and meaningful surprises.
- Avoid repetitive scenes, empty exposition, rushed resolutions,
  and summaries that replace actual dramatic scenes.
- Give chapter endings a reason to keep reading.
- Make the ending feel earned by the story's earlier events.

CONTINUITY:
- Treat approved story decisions and approved chapters as canon.
- Track clues, promises, unresolved questions, relationships,
  character changes, world rules, and planned revelations.
- Never silently contradict established facts.
- Preserve the user's final decisions.
- Do not turn a brainstormed possibility into canon without approval.
- When writing a chapter, consult the approved plan, character
  tracker, continuity rules, and every previous approved chapter.
"""


# ==================================================
# ADAPTIVE STORY DEVELOPMENT
# ==================================================

def evaluate_story(session_id):
    session = get_session(session_id)

    if not session:
        raise ValueError("Story session not found.")

    memory = get_memory(session)

    prompt = f"""
{CREATIVE_DIRECTIVE}

Help the user develop an original novel using their collection
board and their answers.

Ask ONE useful question at a time. After each answer, decide
whether essential story information is missing or the story is
ready for a strong ten-chapter plan.

Do not use a fixed question count. Respect user choices.
You may make creative decisions for details the user leaves open,
but do not invent the identities of the six intended characters.

Think independently before responding:
1. Identify the most interesting story possibilities.
2. Consider hidden motives, conflicts, reversals, and emotional stakes.
3. Consider how all six characters can influence events.
4. Look for foreshadowing that can pay off later.
5. Choose the next question only if the answer will improve the story.

PRIVATE VISUAL ANALYSIS:
{session["visual_analysis"][:12000]}

USER ANSWERS:
{json.dumps(get_answers(session_id), ensure_ascii=False)}

EXISTING STORY MEMORY:
{json.dumps(memory, ensure_ascii=False)[:14000]}

Return valid JSON only.

IF MORE INFORMATION IS NEEDED:
{{
  "status": "ask",
  "question": "One useful question",
  "options": [
    {{"label": "Choice A", "value": "Meaning of A"}},
    {{"label": "Choice B", "value": "Meaning of B"}},
    {{"label": "Choice C", "value": "Meaning of C"}},
    {{"label": "Something else", "value": "Let me choose another direction"}}
  ]
}}

IF READY:
{{
  "status": "ready",
  "plan": {{
    "premise": "Central premise",
    "characters": "All six intended characters and their identities or roles",
    "character_arcs": "A distinct arc and meaningful contribution for each of the six",
    "relationship": "Important relationships and how they change",
    "setting": "Setting and relevant world rules",
    "conflict": "Central conflict and escalating stakes",
    "major_secret": "Main mystery, hidden motives, or secret",
    "brainstormed_ideas": ["Original idea one", "Original idea two"],
    "major_twists": ["A coherent major twist", "Another possible twist"],
    "foreshadowing": ["Clue and its planned payoff"],
    "continuity_rules": ["Facts that must remain consistent"],
    "unresolved_threads": ["Questions intentionally left open"],
    "ending": "Ending direction",
    "chapter_arc": "A coherent outline of exactly ten chapters",
    "character_tracker": [
      {{
        "character": "Character identity grounded in the board or user answers",
        "personality": "Personality",
        "motivation": "Motivation",
        "relationships": "Relationships",
        "plot_contribution": "Meaningful contribution",
        "arc": "Development across the story"
      }}
    ]
  }},
  "titles": ["Title one", "Title two", "Title three", "Title four"]
}}

Requirements:
- The plan must support exactly ten chapters.
- The character_tracker must contain six distinct intended characters.
- Do not invent identities for characters who have not been established.
- If the six characters cannot be identified confidently, return
  status "ask" and ask the user for the missing information.
- Include original creative ideas, meaningful twists, and foreshadowing.
- Suggestions are not user-approved canon until the plan is approved.
- Return JSON only, without Markdown.
"""

    result, provider = generate_text(prompt)
    data = extract_json(result)

    if data.get("status") == "ask":
        question = str(data.get("question", "")).strip()
        options = data.get("options", [])
        cleaned = []

        for option in options[:5]:
            if not isinstance(option, dict):
                continue

            label = str(option.get("label", "")).strip()
            value = str(option.get("value", "")).strip()

            if label and value:
                cleaned.append({
                    "label": label[:100],
                    "value": value[:500],
                })

        if not question or not cleaned:
            raise ValueError("The AI returned an incomplete question.")

        return {
            "status": "ask",
            "question": question,
            "options": cleaned,
            "provider": provider,
        }

    if data.get("status") == "ready":
        plan = data.get("plan")
        titles = data.get("titles", [])

        required = [
            "premise",
            "characters",
            "character_arcs",
            "relationship",
            "setting",
            "conflict",
            "ending",
            "chapter_arc",
        ]

        if not isinstance(plan, dict):
            raise ValueError("The AI returned an incomplete plan.")

        for key in required:
            if not str(plan.get(key, "")).strip():
                raise ValueError(f"Missing plan field: {key}")

        tracker = plan.get("character_tracker", [])
        if not isinstance(tracker, list) or len(tracker) != 6:
            raise ValueError(
                "The plan must track all six intended characters. "
                "The AI did not return six distinct character records."
            )

        identities = [
            str(item.get("character", "")).strip().casefold()
            for item in tracker
            if isinstance(item, dict)
        ]

        if len(identities) != 6 or any(not name for name in identities):
            raise ValueError(
                "The six-character tracker is incomplete."
            )

        if len(set(identities)) != 6:
            raise ValueError(
                "The character tracker contains duplicate identities."
            )

        for key in (
            "brainstormed_ideas",
            "major_twists",
            "foreshadowing",
            "continuity_rules",
            "unresolved_threads",
        ):
            value = plan.get(key, [])
            if not isinstance(value, list):
                plan[key] = []
            else:
                plan[key] = [
                    str(item).strip()
                    for item in value
                    if str(item).strip()
                ][:15]

        titles = titles if isinstance(titles, list) else []
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

        try:
            memory = json.loads(row["story_memory"] or "{}")
        except json.JSONDecodeError:
            memory = {}

        memory["story_plan"] = plan
        memory["character_tracker"] = plan.get("character_tracker", [])
        memory["brainstormed_ideas"] = plan.get("brainstormed_ideas", [])
        memory["approved_twists"] = []
        memory["foreshadowing"] = plan.get("foreshadowing", [])
        memory["continuity_rules"] = plan.get("continuity_rules", [])
        memory["unresolved_threads"] = plan.get("unresolved_threads", [])

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
        description=(
            "Review the story, the six-character cast, and the creative "
            "direction before approving it."
        ),
        color=EMBED_COLOR,
    )

    fields = [
        ("Premise", "premise", 650),
        ("All Six Characters", "characters", 800),
        ("Character Arcs", "character_arcs", 800),
        ("Relationships", "relationship", 500),
        ("Setting and Conflict", "setting", 350),
        ("Central Conflict", "conflict", 450),
        ("Main Mystery", "major_secret", 400),
        ("Creative Twists", "major_twists", 550),
        ("Foreshadowing", "foreshadowing", 400),
        ("Ten-Chapter Journey", "chapter_arc", 900),
        ("Ending Direction", "ending", 450),
    ]

    remaining = 5200

    for label, key, limit in fields:
        value = plan.get(key, "")
        if isinstance(value, list):
            value = "\n".join(f"• {item}" for item in value)
        value = str(value).strip()

        if not value or remaining <= 0:
            continue

        value = value[:min(limit, remaining)]
        embed.add_field(
            name=label,
            value=value,
            inline=False,
        )
        remaining -= len(value) + len(label) + 10

    embed.set_footer(text="Approve the plan or request changes.")
    return embed


# ==================================================
# STORY QUESTIONS
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
# STORY PLAN REVISION AND APPROVAL
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
                "I couldn't revise the plan. Check the Render logs.",
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
                FROM story_sessions
                WHERE id = ?
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
                "Your plan, cast, and creative notes are saved.\n"
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
# CHAPTER DISPLAY
# ==================================================

def split_chapter_text(text, limit=3500):
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
    chunks = split_chapter_text(chapter_text)
    embeds = []

    for index, chunk in enumerate(chunks):
        heading = f"Chapter {chapter_number}"

        embed = discord.Embed(
            title=(
                f"{title} | {heading}"
                if index == 0
                else f"{heading} | Part {index + 1}"
            ),
            description=chunk,
            color=EMBED_COLOR,
        )

        if index == 0:
            embed.set_author(name="Yeosang Novel")
            embed.set_footer(
                text=(
                    "Draft • Review before /post"
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


async def send_chapter_messages(
    destination,
    session,
    chapter_number,
    chapter_text,
    draft=True,
    view=None,
    content=None,
):
    embeds = chapter_embeds(
        session,
        chapter_number,
        chapter_text,
        draft=draft,
    )

    first_message = await destination.send(
        content=content,
        embed=embeds[0],
        view=view,
    )

    for embed in embeds[1:]:
        await destination.send(embed=embed)

    return first_message


# ==================================================
# PROVISIONAL CHAPTER MEMORY
# ==================================================

def update_pending_story_memory(session_id, chapter_number, chapter_text):
    """
    Ask the AI to record continuity notes for the current draft.

    These notes remain provisional until /post approves the chapter.
    If this additional AI call fails, the chapter draft is still saved.
    """
    session = get_session(session_id)
    if not session:
        return

    memory = get_memory(session)
    plan = json.loads(session["story_plan"] or "{}")
    approved = get_approved_chapters(session)

    prompt = f"""
{CREATIVE_DIRECTIVE}

You are maintaining the private story bible for this novel.

Read the approved plan, existing memory, previous approved chapters,
and the newest unapproved chapter draft.

Do not rewrite the chapter. Return valid JSON only with this structure:

{{
  "chapter_note": "Important events and changes in this draft",
  "character_updates": [
    {{
      "character": "One of the six established characters",
      "change": "New action, decision, emotional change, or relationship change"
    }}
  ],
  "new_ideas": ["Useful future possibilities, not yet canon"],
  "twists_to_consider": ["Potential twist with setup and payoff"],
  "foreshadowing": ["Clue planted and possible later payoff"],
  "continuity_rules": ["New fact that must remain consistent"],
  "unresolved_threads": ["Question or conflict still unresolved"]
}}

NOVEL:
{session["chosen_title"]}

APPROVED PLAN:
{json.dumps(plan, ensure_ascii=False)[:10000]}

EXISTING MEMORY:
{json.dumps(memory, ensure_ascii=False)[:10000]}

PREVIOUS APPROVED CHAPTERS:
{json.dumps(approved, ensure_ascii=False)[:12000]}

NEW DRAFT, CHAPTER {chapter_number}:
{chapter_text[:14000]}

Rules:
- Track the six established characters; do not invent new identities.
- Distinguish events that actually occur in the draft from future ideas.
- New ideas and possible twists are suggestions, not canon.
- Do not overwrite established facts.
- Keep each list concise and useful.
- Return JSON only.
"""

    try:
        result, provider = generate_text(prompt)
        data = extract_json(result)

        if not isinstance(data, dict):
            raise ValueError("Invalid chapter-memory response.")

        # Store these notes as provisional. /post commits them.
        pending = {
            "chapter_number": chapter_number,
            "chapter_note": str(data.get("chapter_note", "")).strip()[:2500],
            "character_updates": data.get("character_updates", [])[:12],
            "new_ideas": data.get("new_ideas", [])[:10],
            "twists_to_consider": data.get("twists_to_consider", [])[:10],
            "foreshadowing": data.get("foreshadowing", [])[:10],
            "continuity_rules": data.get("continuity_rules", [])[:10],
            "unresolved_threads": data.get("unresolved_threads", [])[:10],
            "provider": provider,
        }

        memory["pending_memory_update"] = pending
        save_memory(session_id, memory)

        logging.info(
            "Prepared provisional story memory for chapter %s using %s",
            chapter_number,
            provider,
        )

    except Exception:
        logging.exception(
            "Could not prepare provisional story memory for chapter %s",
            chapter_number,
        )


def commit_pending_story_memory(memory, chapter_number):
    pending = memory.get("pending_memory_update")

    if not isinstance(pending, dict):
        return memory

    if pending.get("chapter_number") != chapter_number:
        return memory

    memory.setdefault("chapter_notes", []).append({
        "chapter_number": chapter_number,
        "note": pending.get("chapter_note", ""),
    })

    # Character developments are added to the existing character records.
    tracker = memory.get("character_tracker", [])
    updates = pending.get("character_updates", [])

    if isinstance(tracker, list) and isinstance(updates, list):
        for update in updates:
            if not isinstance(update, dict):
                continue

            name = str(update.get("character", "")).strip().casefold()
            change = str(update.get("change", "")).strip()

            if not name or not change:
                continue

            for character in tracker:
                if (
                    isinstance(character, dict)
                    and str(character.get("character", "")).strip().casefold() == name
                ):
                    character.setdefault("chapter_developments", []).append({
                        "chapter_number": chapter_number,
                        "change": change,
                    })
                    break

    for source_key, target_key in [
        ("new_ideas", "brainstormed_ideas"),
        ("twists_to_consider", "brainstormed_ideas"),
        ("foreshadowing", "foreshadowing"),
        ("continuity_rules", "continuity_rules"),
        ("unresolved_threads", "unresolved_threads"),
    ]:
        destination = memory.setdefault(target_key, [])
        existing = {str(item) for item in destination}

        for item in pending.get(source_key, []):
            value = str(item).strip()
            if value and value not in existing:
                destination.append(value)
                existing.add(value)

        memory[target_key] = destination[-50:]

    memory["pending_memory_update"] = None
    return memory


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
                "There is no active chapter draft.",
                ephemeral=True,
            )
            return

        if len(session["current_draft"]) > 4000:
            await interaction.response.send_message(
                "This chapter exceeds Discord's modal editing limit. "
                "You can still review it and use `/post` when ready.",
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
            "🖤 Your revised draft has been saved. Use `/post` when ready.",
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
                "This chapter is too long for the built-in Discord "
                "editing window. You can still review and approve it.",
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

        if len(image_bytes) > MAX_IMAGE_SIZE:
            raise ValueError("The downloaded image exceeds 15 MB.")

        mime_type = board.content_type or {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".gif": "image/gif",
        }.get(Path(filename).suffix, "image/jpeg")

        safe_filename = Path(board.filename).name
        board_path = BOARD_FOLDER / (
            f"{interaction.user.id}_{today_string()}_{safe_filename}"
        )
        board_path.write_bytes(image_bytes)

        result = ai_manager.analyze_image(
            image_bytes=image_bytes,
            mime_type=mime_type,
            prompt=VISUAL_ANALYSIS_PROMPT,
        )
        visual_analysis, provider = unpack_ai_result(result)

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

    if session["plan_status"] != "approved" or not session["chosen_title"]:
        await interaction.followup.send(
            "Approve your story plan and choose a title before `/write`.",
            ephemeral=True,
        )
        return

    if session["current_draft"]:
        chapter_number = session["current_draft_chapter"]

        await send_chapter_messages(
            interaction.followup,
            session,
            chapter_number,
            session["current_draft"],
            draft=True,
            view=ChapterDraftView(session["id"]),
            content=(
                f"Your Chapter {chapter_number} draft is waiting for approval. "
                "Review it, edit it if needed, then use `/post`."
            ),
        )
        return

    approved = get_approved_chapters(session)

    if len(approved) >= MAX_CHAPTERS:
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
{CREATIVE_DIRECTIVE}

Write Chapter {chapter_number} of the user's novel.

NOVEL TITLE:
{session["chosen_title"]}

APPROVED STORY PLAN:
{json.dumps(plan, ensure_ascii=False, indent=2)[:14000]}

PRIVATE COLLECTION-BOARD ANALYSIS:
{session["visual_analysis"][:8000]}

USER'S STORY CHOICES:
{json.dumps(memory.get("user_choices", []), ensure_ascii=False)[:8000]}

SAVED CHARACTER TRACKER:
{json.dumps(memory.get("character_tracker", []), ensure_ascii=False)[:8000]}

SAVED BRAINSTORMED IDEAS:
{json.dumps(memory.get("brainstormed_ideas", []), ensure_ascii=False)[:5000]}

APPROVED TWISTS:
{json.dumps(memory.get("approved_twists", []), ensure_ascii=False)[:4000]}

FORESHADOWING:
{json.dumps(memory.get("foreshadowing", []), ensure_ascii=False)[:4000]}

CONTINUITY RULES:
{json.dumps(memory.get("continuity_rules", []), ensure_ascii=False)[:5000]}

UNRESOLVED THREADS:
{json.dumps(memory.get("unresolved_threads", []), ensure_ascii=False)[:5000]}

PREVIOUS APPROVED CHAPTERS:
{json.dumps(approved, ensure_ascii=False)[:18000]}

CHAPTER NUMBER: {chapter_number} of 10

CHAPTER WRITING REQUIREMENTS:
- Write only Chapter {chapter_number}.
- Continue naturally from the previous approved chapter.
- Follow the approved premise, relationships, world rules, and ending.
- Preserve all established facts and character personalities.
- Give all six established characters meaningful presence across
  the novel. A character need not appear in every single chapter,
  but their overall arcs and planned contributions must not disappear.
- Use the chapter outline to decide who should act and what should change.
- Check which character has agency in this chapter. Do not let the same
  characters solve every problem while the others become background.
- Develop original ideas and interesting turns that fit the approved plan.
- Plant or develop clues when appropriate. Do not reveal a major secret
  before its planned payoff.
- New ideas must not contradict canon or silently override user choices.
- Advance at least one meaningful plot thread or character arc.
- Use immersive prose, meaningful dialogue, and developed scenes.
- Avoid summaries in place of scenes.
- Avoid making the entire chapter a sequence of one-line paragraphs.
- Do not add an out-of-story explanation or ask questions.
- Return the chapter heading followed by complete chapter prose.
- Aim for a substantial chapter with a natural beginning and ending.
- Do not write the next chapter.
"""

    try:
        chapter_text, provider = generate_text(prompt)
        chapter_text = chapter_text.strip()

        if not chapter_text:
            raise ValueError("The AI returned an empty chapter.")

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

        # Generate optional continuity notes. The draft remains saved
        # even if this secondary AI request fails.
        update_pending_story_memory(
            session["id"],
            chapter_number,
            chapter_text,
        )

        refreshed = get_session(session["id"])

        await send_chapter_messages(
            interaction.followup,
            refreshed,
            chapter_number,
            chapter_text,
            draft=True,
            view=ChapterDraftView(session["id"]),
            content=(
                f"📖 **Chapter {chapter_number} draft is ready.**\n"
                "Review the chapter before approving it. Use the Edit Draft "
                "button for short edits, or `/post` when you're satisfied."
            ),
        )

        logging.info(
            "Generated Chapter %s for session %s using %s",
            chapter_number,
            session["id"],
            provider,
        )

    except Exception:
        logging.exception("Failed to generate or display chapter")

        await interaction.followup.send(
            "I couldn't finish displaying the chapter. The generated draft "
            "was saved if generation completed. Run `/write` to display "
            "the saved draft again, and check the Render logs if it fails.",
            ephemeral=True,
        )


# ==================================================
# /POST
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

    if draft_number != expected_number or expected_number > MAX_CHAPTERS:
        await interaction.followup.send(
            "The draft number does not match the next chapter. "
            "Nothing was approved. Please check the Render logs.",
            ephemeral=True,
        )
        return

    chapter_text = session["current_draft"].strip()

    if not chapter_text:
        await interaction.followup.send(
            "The draft is empty. Generate it again with `/write`.",
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

    # Commit continuity notes only when the user approves the chapter.
    memory = commit_pending_story_memory(memory, expected_number)

    completed = expected_number == MAX_CHAPTERS

    with connect_db() as db:
        current = db.execute("""
            SELECT current_draft, current_draft_chapter
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
                "Nothing was approved. Run `/write` again.",
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
                "and their committed story notes are saved in your memory.\n\n"
                "Public website publishing will be added separately."
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
    threading.Thread(target=run_web, daemon=True).start()

    token = os.environ.get("DISCORD_TOKEN")

    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set.")

    bot.run(token)
