import asyncio
import json
import aiosqlite
import discord
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import commands

from db.database import DB_PATH

NORSE_BLUE = 0x0B1F3A
QUESTION_COLOR = 0x9DD9E5
ANSWER_TIMEOUT = 300

STATUS_EMOJI = {
    "pending": "⏳",
    "approved": "✅",
    "rejected": "❌",
}


def application_embed(app_row: dict) -> discord.Embed:
    answers = app_row["answers"]
    if not isinstance(answers, list):
        answers = json.loads(answers)

    emoji = STATUS_EMOJI.get(app_row["status"], "❓")
    embed = discord.Embed(title=f"Application #{app_row['id']}", color=NORSE_BLUE)
    embed.add_field(name="Status", value=f"{emoji} {app_row['status'].capitalize()}", inline=True)
    embed.add_field(name="Applicant", value=app_row["user_name"], inline=True)
    embed.add_field(name="Submitted At", value=app_row["submitted_at"], inline=True)
    if app_row.get("source"):
        embed.add_field(name="Applied Via", value=app_row["source"], inline=True)

    for i, qa in enumerate(answers, start=1):
        embed.add_field(
            name=f"Q{i}. {qa['question']}",
            value=(qa["answer"] or "—")[:1024],
            inline=False,
        )

    if app_row.get("reviewed_by"):
        embed.add_field(name="Reviewed By", value=app_row["reviewed_by"], inline=True)
    if app_row.get("reviewed_at"):
        embed.add_field(name="Reviewed At", value=app_row["reviewed_at"], inline=True)
    if app_row.get("review_notes"):
        embed.add_field(name="Review Notes", value=app_row["review_notes"], inline=False)

    embed.set_footer(text=f"Application ID: {app_row['id']}")
    return embed


# ---------------------------------------------------------------------------
# Setup modals
# ---------------------------------------------------------------------------

class PanelSetupModal(discord.ui.Modal, title="Application Panel Setup"):
    panel_title = discord.ui.TextInput(
        label="Embed Title",
        placeholder="e.g. Norse Air Applications",
        max_length=256,
    )
    panel_description = discord.ui.TextInput(
        label="Embed Description",
        style=discord.TextStyle.paragraph,
        placeholder="Describe what applicants can apply for...",
        max_length=2000,
    )

    def __init__(self, channel: discord.TextChannel, notification: discord.TextChannel | None):
        super().__init__()
        self.channel = channel
        self.notification = notification

    async def on_submit(self, interaction: discord.Interaction):
        title = self.panel_title.value.strip()
        description = self.panel_description.value.strip()
        notif_id = str(self.notification.id) if self.notification else None

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """INSERT INTO application_panels (guild_id, title, description, notification_channel_id)
                   VALUES (?, ?, ?, ?)""",
                (interaction.guild.id, title, description, notif_id),
            )
            panel_id = cur.lastrowid
            await db.commit()

        notif_mention = self.notification.mention if self.notification else "not set"
        await interaction.response.send_message(
            f"Panel **#{panel_id}** created!\n"
            f"Notifications → {notif_mention}\n\n"
            f"Next steps:\n"
            f"• `/addbutton {panel_id} label:\"ATC24 Application\"` to add a button\n"
            f"• `/postpanel {panel_id}` to post the embed in {self.channel.mention}",
            ephemeral=True,
        )
        # store channel for postpanel later
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE application_panels SET channel_id = ? WHERE id = ?",
                (self.channel.id, panel_id),
            )
            await db.commit()


class ButtonQuestionsModal(discord.ui.Modal, title="Add Application Button"):
    q1 = discord.ui.TextInput(label="Question 1", required=True, max_length=300)
    q2 = discord.ui.TextInput(label="Question 2", required=False, max_length=300, default="")
    q3 = discord.ui.TextInput(label="Question 3", required=False, max_length=300, default="")
    q4 = discord.ui.TextInput(label="Question 4", required=False, max_length=300, default="")
    q5 = discord.ui.TextInput(label="Question 5", required=False, max_length=300, default="")

    def __init__(self, panel_id: int, label: str, emoji: str | None):
        super().__init__()
        self.panel_id = panel_id
        self.btn_label = label
        self.btn_emoji = emoji

    async def on_submit(self, interaction: discord.Interaction):
        questions = [
            q.value.strip()
            for q in (self.q1, self.q2, self.q3, self.q4, self.q5)
            if q.value.strip()
        ]

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM application_buttons WHERE panel_id = ?",
                (self.panel_id,),
            ) as cur:
                (count,) = await cur.fetchone()

            cur = await db.execute(
                "INSERT INTO application_buttons (panel_id, label, emoji, btn_order) VALUES (?, ?, ?, ?)",
                (self.panel_id, self.btn_label, self.btn_emoji, count),
            )
            button_id = cur.lastrowid

            for i, text in enumerate(questions, start=1):
                await db.execute(
                    "INSERT INTO application_questions (button_id, question_order, question_text) VALUES (?, ?, ?)",
                    (button_id, i, text),
                )
            await db.commit()

        await interaction.response.send_message(
            f"Button **\"{self.btn_label}\"** added to Panel #{self.panel_id} with **{len(questions)}** question(s).\n"
            f"Run `/postpanel {self.panel_id}` to (re)post the embed with the updated buttons.",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Edit questions modal (pre-filled with existing answers)
# ---------------------------------------------------------------------------

class EditQuestionsModal(discord.ui.Modal, title="Edit Button Questions"):
    def __init__(self, button_id: int, existing: list):
        super().__init__()
        self.button_id = button_id

        qs = (existing + ["", "", "", "", ""])[:5]
        self.q1 = discord.ui.TextInput(label="Question 1", required=True,  max_length=300, default=qs[0])
        self.q2 = discord.ui.TextInput(label="Question 2", required=False, max_length=300, default=qs[1])
        self.q3 = discord.ui.TextInput(label="Question 3", required=False, max_length=300, default=qs[2])
        self.q4 = discord.ui.TextInput(label="Question 4", required=False, max_length=300, default=qs[3])
        self.q5 = discord.ui.TextInput(label="Question 5", required=False, max_length=300, default=qs[4])
        for field in (self.q1, self.q2, self.q3, self.q4, self.q5):
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        questions = [
            q.value.strip()
            for q in (self.q1, self.q2, self.q3, self.q4, self.q5)
            if q.value.strip()
        ]
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM application_questions WHERE button_id = ?", (self.button_id,)
            )
            for i, text in enumerate(questions, start=1):
                await db.execute(
                    "INSERT INTO application_questions (button_id, question_order, question_text) VALUES (?, ?, ?)",
                    (self.button_id, i, text),
                )
            await db.commit()

        await interaction.response.send_message(
            f"Updated **{len(questions)}** question(s) for button #{self.button_id}.", ephemeral=True
        )


# ---------------------------------------------------------------------------
# Application panel view (one per panel, contains all its buttons)
# ---------------------------------------------------------------------------

async def build_panel_view(panel_id: int) -> "ApplicationPanelView | None":
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, label, emoji FROM application_buttons WHERE panel_id = ? ORDER BY btn_order",
            (panel_id,),
        ) as cur:
            rows = await cur.fetchall()

    if not rows:
        return None

    buttons = [{"id": r[0], "label": r[1], "emoji": r[2]} for r in rows]
    return ApplicationPanelView(panel_id, buttons)


class ApplicationPanelView(discord.ui.View):
    def __init__(self, panel_id: int, buttons: list):
        super().__init__(timeout=None)
        for btn in buttons:
            self.add_item(ApplyButton(btn["id"], btn["label"], btn.get("emoji")))


class ApplyButton(discord.ui.Button):
    def __init__(self, button_id: int, label: str, emoji: str | None = None):
        super().__init__(
            label=label,
            custom_id=f"app:btn:{button_id}",
            style=discord.ButtonStyle.primary,
            emoji=emoji or None,
        )

    async def callback(self, interaction: discord.Interaction):
        button_id = int(self.custom_id.split(":")[-1])
        cog: "Applications" = interaction.client.cogs.get("Applications")
        if cog is None:
            return await interaction.response.send_message("Bot error — please contact an admin.", ephemeral=True)

        guild = interaction.guild
        user = interaction.user

        if user.id in cog._active_interviews:
            return await interaction.response.send_message(
                "You already have an interview in progress — check your DMs.", ephemeral=True
            )

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id FROM applications WHERE guild_id = ? AND user_id = ? AND status = 'pending'",
                (guild.id, str(user.id)),
            ) as cur:
                pending = await cur.fetchone()

            async with db.execute(
                """SELECT ab.label, ap.notification_channel_id
                   FROM application_buttons ab
                   JOIN application_panels ap ON ap.id = ab.panel_id
                   WHERE ab.id = ?""",
                (button_id,),
            ) as cur:
                btn_row = await cur.fetchone()

            async with db.execute(
                "SELECT question_text FROM application_questions WHERE button_id = ? ORDER BY question_order",
                (button_id,),
            ) as cur:
                q_rows = await cur.fetchall()

        if pending:
            return await interaction.response.send_message(
                f"You already have a pending application (`#{pending[0]}`). "
                "Please wait for it to be reviewed before applying again.",
                ephemeral=True,
            )

        if not btn_row:
            return await interaction.response.send_message(
                "This button no longer exists. Please contact an admin.", ephemeral=True
            )

        btn_label, notif_channel_id = btn_row
        questions = [r[0] for r in q_rows]

        if not questions:
            return await interaction.response.send_message(
                "No questions have been set up for this section yet. Ask an admin.",
                ephemeral=True,
            )

        try:
            intro = discord.Embed(
                title="✈️ Norse Air Application",
                description=(
                    f"Welcome! I'll ask you **{len(questions)} question(s)** for **{btn_label}**.\n"
                    f"Reply to each one in this DM. You have "
                    f"**{ANSWER_TIMEOUT // 60} minutes** per question.\n\n"
                    "Type `cancel` at any time to abort."
                ),
                color=NORSE_BLUE,
            )
            await user.send(embed=intro)
        except discord.Forbidden:
            return await interaction.response.send_message(
                "I couldn't DM you. Enable **Direct Messages** from server members "
                "in your privacy settings, then try again.",
                ephemeral=True,
            )

        await interaction.response.send_message(
            "📬 Check your DMs — your application interview has started!", ephemeral=True
        )

        cog._active_interviews.add(user.id)
        try:
            await cog._run_interview(user, guild, questions, btn_label, notif_channel_id)
        finally:
            cog._active_interviews.discard(user.id)


# ---------------------------------------------------------------------------
# Review UI
# ---------------------------------------------------------------------------

class RejectReasonModal(discord.ui.Modal, title="Reject Application"):
    reason = discord.ui.TextInput(
        label="Reason for Denial",
        style=discord.TextStyle.paragraph,
        placeholder="Why is this application being rejected?",
        required=True,
        max_length=1000,
    )

    def __init__(self, app_id, user_id, user_name, cog, review_view, original_message):
        super().__init__()
        self.app_id = app_id
        self.user_id = user_id
        self.user_name = user_name
        self.cog = cog
        self.review_view = review_view
        self.original_message = original_message

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason.value.strip()
        reviewer = interaction.user.display_name

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """UPDATE applications
                   SET status='rejected', reviewed_by=?, reviewed_at=datetime('now'), review_notes=?
                   WHERE id=?""",
                (reviewer, reason, self.app_id),
            )
            await db.commit()

        try:
            applicant = await self.cog.bot.fetch_user(int(self.user_id))
            dm_embed = discord.Embed(
                title="Rejected",
                description=(
                    "Your application for the Norse24 Program has been rejected. "
                    "Please do not be disheartened. "
                    "You can always come back and apply again."
                ),
                color=0xE74C3C,
            )
            dm_embed.add_field(name="Reason For Denial", value=reason, inline=False)
            await applicant.send(embed=dm_embed)
        except Exception:
            pass

        result_embed = discord.Embed(
            title=f"Application #{self.app_id} — ❌ Rejected",
            color=0xE74C3C,
        )
        result_embed.add_field(name="Applicant", value=self.user_name, inline=True)
        result_embed.add_field(name="Reviewed By", value=reviewer, inline=True)
        result_embed.add_field(name="Reason", value=reason, inline=False)

        for item in self.review_view.children:
            item.disabled = True
        try:
            await self.original_message.edit(embed=result_embed, view=self.review_view)
        except Exception:
            pass

        await interaction.response.send_message(
            f"Application #{self.app_id} rejected.", ephemeral=True
        )


class ApplicationReviewView(discord.ui.View):
    def __init__(self, app_id, user_id, user_name, cog):
        super().__init__(timeout=None)
        self.app_id = app_id
        self.user_id = user_id
        self.user_name = user_name
        self.cog = cog

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success)
    async def approve_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        reviewer = interaction.user.display_name

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """UPDATE applications
                   SET status='approved', reviewed_by=?, reviewed_at=datetime('now')
                   WHERE id=?""",
                (reviewer, self.app_id),
            )
            await db.commit()

        try:
            applicant = await self.cog.bot.fetch_user(int(self.user_id))
            dm_embed = discord.Embed(
                title="Accepted",
                description=(
                    "Congratulations! Your application for the Norse24 Program has been accepted.\n\n"
                    "Please proceed to the Pilot Hub, where you'll find all the information and "
                    "resources you need to begin your journey. We look forward to seeing you in the "
                    "skies, happy flying!"
                ),
                color=0x2ECC71,
            )
            await applicant.send(embed=dm_embed)
        except Exception:
            pass

        result_embed = discord.Embed(
            title=f"Application #{self.app_id} — ✅ Approved",
            color=0x2ECC71,
        )
        result_embed.add_field(name="Applicant", value=self.user_name, inline=True)
        result_embed.add_field(name="Reviewed By", value=reviewer, inline=True)

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(embed=result_embed, view=self)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger)
    async def reject_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = RejectReasonModal(
            self.app_id, self.user_id, self.user_name,
            self.cog, self, interaction.message,
        )
        await interaction.response.send_modal(modal)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Applications(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._active_interviews: set[int] = set()

    async def cog_load(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id      INTEGER NOT NULL,
                    user_id       TEXT    NOT NULL,
                    user_name     TEXT    NOT NULL,
                    submitted_at  TEXT    NOT NULL,
                    status        TEXT    NOT NULL DEFAULT 'pending',
                    answers       TEXT    NOT NULL,
                    source        TEXT,
                    reviewed_by   TEXT,
                    reviewed_at   TEXT,
                    review_notes  TEXT
                )
            """)
            try:
                await db.execute("ALTER TABLE applications ADD COLUMN source TEXT")
            except Exception:
                pass

            # Drop old incompatible schemas and recreate cleanly
            try:
                await db.execute("SELECT id, channel_id FROM application_panels LIMIT 1")
                # also check for application_buttons
                try:
                    await db.execute("SELECT id FROM application_buttons LIMIT 1")
                except Exception:
                    # panels table exists but buttons table doesn't — need buttons table
                    pass
            except Exception:
                await db.execute("DROP TABLE IF EXISTS application_panels")
                await db.execute("DROP TABLE IF EXISTS application_buttons")
                await db.execute("DROP TABLE IF EXISTS application_questions")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS application_panels (
                    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id                INTEGER NOT NULL,
                    channel_id              INTEGER,
                    title                   TEXT NOT NULL,
                    description             TEXT NOT NULL DEFAULT '',
                    notification_channel_id TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS application_buttons (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    panel_id  INTEGER NOT NULL,
                    label     TEXT    NOT NULL,
                    emoji     TEXT,
                    btn_order INTEGER NOT NULL DEFAULT 0
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS application_questions (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    button_id      INTEGER NOT NULL,
                    question_order INTEGER NOT NULL,
                    question_text  TEXT    NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS applications_config (
                    guild_id                INTEGER PRIMARY KEY,
                    notification_channel_id TEXT
                )
            """)
            await db.commit()

            async with db.execute("SELECT id FROM application_panels") as cur:
                panel_ids = [r[0] for r in await cur.fetchall()]

        for panel_id in panel_ids:
            view = await build_panel_view(panel_id)
            if view:
                self.bot.add_view(view)

    async def _run_interview(
        self,
        user: discord.User,
        guild: discord.Guild,
        questions: list,
        source: str = "",
        notification_channel_id: str | None = None,
    ):
        def check(m: discord.Message) -> bool:
            return m.author.id == user.id and m.guild is None

        answers = []
        for i, question in enumerate(questions, start=1):
            q_embed = discord.Embed(
                title=f"Question {i} of {len(questions)}",
                description=question,
                color=QUESTION_COLOR,
            )
            await user.send(embed=q_embed)

            try:
                reply = await self.bot.wait_for("message", check=check, timeout=ANSWER_TIMEOUT)
            except asyncio.TimeoutError:
                await user.send(embed=discord.Embed(
                    title="⏰ Application Timed Out",
                    description="You took too long to answer. Click the Apply button in the server to start over.",
                    color=0xE74C3C,
                ))
                return

            content = reply.content.strip()
            if content.lower() == "cancel":
                await user.send(embed=discord.Embed(
                    title="❌ Application Cancelled",
                    description="Your application has been cancelled and is not sent to the team for review.",
                    color=0xE74C3C,
                ))
                return

            answers.append({"question": question, "answer": content[:1000]})

        confirm_embed = discord.Embed(
            title="📋 Ready to Submit",
            description=(
                "You've answered all the questions.\n"
                "Press **Submit** to send your application, or **Cancel** to discard it."
            ),
            color=QUESTION_COLOR,
        )

        class ConfirmView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=120)
                self.choice: str | None = None

            @discord.ui.button(label="Submit", style=discord.ButtonStyle.success)
            async def submit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.choice = "submit"
                self.stop()
                await interaction.response.defer()

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
            async def cancel_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
                self.choice = "cancel"
                self.stop()
                await interaction.response.defer()

        view = ConfirmView()
        confirm_msg = await user.send(embed=confirm_embed, view=view)
        await view.wait()

        for item in view.children:
            item.disabled = True
        await confirm_msg.edit(view=view)

        if view.choice != "submit":
            await user.send(embed=discord.Embed(
                title="Cancelled",
                description="Your application has been cancelled and is not sent to the team for review.",
                color=0xE74C3C,
            ))
            return

        submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        answers_json = json.dumps(answers, ensure_ascii=False)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """INSERT INTO applications (guild_id, user_id, user_name, submitted_at, status, answers, source)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (guild.id, str(user.id), user.display_name, submitted_at, answers_json, source),
            )
            app_id = cursor.lastrowid
            await db.commit()

            if not notification_channel_id:
                async with db.execute(
                    "SELECT notification_channel_id FROM applications_config WHERE guild_id = ?",
                    (guild.id,),
                ) as cur:
                    row = await cur.fetchone()
                    notification_channel_id = row[0] if row else None

        await user.send(embed=discord.Embed(
            title="Submitted",
            description=(
                "Your application has been successfully submitted and is now pending review. "
                "Please allow up to 24 hours for our team to process your application.\n\n"
                "You will receive a direct message from me once a decision has been made. "
                "To help us manage applications efficiently, please do not make a ticket, "
                "DM, or contact staff members regarding the status of your application."
            ),
            color=QUESTION_COLOR,
        ))

        if notification_channel_id:
            channel = guild.get_channel(int(notification_channel_id))
            if channel:
                app_row = {
                    "id": app_id,
                    "guild_id": guild.id,
                    "user_id": str(user.id),
                    "user_name": user.display_name,
                    "submitted_at": submitted_at,
                    "status": "pending",
                    "answers": answers,
                    "source": source,
                    "reviewed_by": None,
                    "reviewed_at": None,
                    "review_notes": None,
                }
                notify_embed = application_embed(app_row)
                notify_embed.set_author(
                    name="New Application Submitted",
                    icon_url=user.display_avatar.url,
                )
                review_view = ApplicationReviewView(app_id, str(user.id), user.display_name, self)
                try:
                    await channel.send(embed=notify_embed, view=review_view)
                except discord.Forbidden:
                    pass

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------

    @commands.hybrid_command(
        name="createpanel",
        description="[Admin] Create a new application panel (embed + buttons)",
    )
    @app_commands.describe(
        channel="Channel to post the panel in",
        notification="Channel where submissions are posted for staff review",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    async def createpanel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        notification: discord.TextChannel = None,
    ):
        if ctx.interaction is None:
            await ctx.send("Please use the slash command `/createpanel`.", ephemeral=True)
            return
        await ctx.interaction.response.send_modal(PanelSetupModal(channel, notification))

    @commands.hybrid_command(
        name="addbutton",
        description="[Admin] Add a button with its own questions to a panel",
    )
    @app_commands.describe(
        panel_id="Panel ID from /createpanel",
        label="Button label (e.g. ATC24 Application)",
        emoji="Optional emoji for the button",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    async def addbutton(
        self,
        ctx: commands.Context,
        panel_id: int,
        label: str,
        emoji: str = None,
    ):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id FROM application_panels WHERE id = ? AND guild_id = ?",
                (panel_id, ctx.guild.id),
            ) as cur:
                row = await cur.fetchone()

        if not row:
            await ctx.send(f"Panel #{panel_id} not found in this server.", ephemeral=True)
            return

        if ctx.interaction is None:
            await ctx.send("Please use the slash command `/addbutton`.", ephemeral=True)
            return

        await ctx.interaction.response.send_modal(ButtonQuestionsModal(panel_id, label, emoji))

    @commands.hybrid_command(
        name="postpanel",
        description="[Admin] Post (or re-post) a panel embed with all its buttons",
    )
    @app_commands.describe(panel_id="Panel ID from /createpanel")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    async def postpanel(self, ctx: commands.Context, panel_id: int):
        await ctx.defer(ephemeral=True)

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT title, description, channel_id FROM application_panels WHERE id = ? AND guild_id = ?",
                (panel_id, ctx.guild.id),
            ) as cur:
                panel_row = await cur.fetchone()

        if not panel_row:
            await ctx.send(f"Panel #{panel_id} not found in this server.", ephemeral=True)
            return

        title, description, channel_id = panel_row
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        if not channel:
            await ctx.send("Panel channel not found. Please specify a channel.", ephemeral=True)
            return

        view = await build_panel_view(panel_id)
        if not view:
            await ctx.send(
                f"Panel #{panel_id} has no buttons yet. Use `/addbutton {panel_id}` first.",
                ephemeral=True,
            )
            return

        self.bot.add_view(view)
        embed = discord.Embed(title=title, description=description, color=NORSE_BLUE)
        await channel.send(embed=embed, view=view)
        await ctx.send(f"Panel posted in {channel.mention}.", ephemeral=True)

    @commands.hybrid_command(
        name="listpanels",
        description="[Admin] List all application panels in this server",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    async def listpanels(self, ctx: commands.Context):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id, title, notification_channel_id FROM application_panels WHERE guild_id = ?",
                (ctx.guild.id,),
            ) as cur:
                panels = await cur.fetchall()

        if not panels:
            await ctx.send("No panels yet. Use `/createpanel` to get started.", ephemeral=True)
            return

        embed = discord.Embed(title="Application Panels", color=NORSE_BLUE)
        async with aiosqlite.connect(DB_PATH) as db:
            for panel_id, title, notif_id in panels:
                async with db.execute(
                    "SELECT id, label FROM application_buttons WHERE panel_id = ? ORDER BY btn_order",
                    (panel_id,),
                ) as cur:
                    btns = await cur.fetchall()
                notif = f"<#{notif_id}>" if notif_id else "not set"
                if btns:
                    btn_list = "\n".join(f"  `#{r[0]}` — {r[1]}" for r in btns)
                else:
                    btn_list = f"none — use `/addbutton {panel_id}`"
                embed.add_field(
                    name=f"Panel #{panel_id} — {title}",
                    value=f"Notifications: {notif}\nButtons:\n{btn_list}",
                    inline=False,
                )
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="editbutton",
        description="[Admin] Edit a button's label or emoji",
    )
    @app_commands.describe(
        button_id="Button ID from /listpanels",
        label="New button label",
        emoji="New emoji (leave blank to keep existing)",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    async def editbutton(self, ctx: commands.Context, button_id: int, label: str, emoji: str = None):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT ab.id FROM application_buttons ab
                   JOIN application_panels ap ON ap.id = ab.panel_id
                   WHERE ab.id = ? AND ap.guild_id = ?""",
                (button_id, ctx.guild.id),
            ) as cur:
                row = await cur.fetchone()

        if not row:
            await ctx.send(f"Button #{button_id} not found in this server.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            if emoji:
                await db.execute(
                    "UPDATE application_buttons SET label = ?, emoji = ? WHERE id = ?",
                    (label, emoji, button_id),
                )
            else:
                await db.execute(
                    "UPDATE application_buttons SET label = ? WHERE id = ?",
                    (label, button_id),
                )
            # fetch panel_id so we can tell the user which panel to re-post
            async with db.execute(
                "SELECT panel_id FROM application_buttons WHERE id = ?", (button_id,)
            ) as cur:
                (panel_id,) = await cur.fetchone()
            await db.commit()

        await ctx.send(
            f"Button #{button_id} updated to **\"{label}\"**.\n"
            f"Run `/postpanel {panel_id}` to re-post the embed with the new label.",
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="editbuttonquestions",
        description="[Admin] Edit the interview questions for a button",
    )
    @app_commands.describe(button_id="Button ID from /listpanels")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    async def editbuttonquestions(self, ctx: commands.Context, button_id: int):
        if ctx.interaction is None:
            await ctx.send("Please use the slash command `/editbuttonquestions`.", ephemeral=True)
            return

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT ab.id FROM application_buttons ab
                   JOIN application_panels ap ON ap.id = ab.panel_id
                   WHERE ab.id = ? AND ap.guild_id = ?""",
                (button_id, ctx.guild.id),
            ) as cur:
                row = await cur.fetchone()

            if not row:
                await ctx.send(f"Button #{button_id} not found in this server.", ephemeral=True)
                return

            async with db.execute(
                "SELECT question_text FROM application_questions WHERE button_id = ? ORDER BY question_order",
                (button_id,),
            ) as cur:
                existing = [r[0] for r in await cur.fetchall()]

        await ctx.interaction.response.send_modal(EditQuestionsModal(button_id, existing))

    @commands.hybrid_command(
        name="removebutton",
        description="[Admin] Remove a button from a panel",
    )
    @app_commands.describe(button_id="Button ID from /listpanels")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    async def removebutton(self, ctx: commands.Context, button_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT ab.label, ab.panel_id FROM application_buttons ab
                   JOIN application_panels ap ON ap.id = ab.panel_id
                   WHERE ab.id = ? AND ap.guild_id = ?""",
                (button_id, ctx.guild.id),
            ) as cur:
                row = await cur.fetchone()

        if not row:
            await ctx.send(f"Button #{button_id} not found in this server.", ephemeral=True)
            return

        btn_label, panel_id = row
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("DELETE FROM application_questions WHERE button_id = ?", (button_id,))
            await db.execute("DELETE FROM application_buttons WHERE id = ?", (button_id,))
            await db.commit()

        await ctx.send(
            f"Removed button **\"{btn_label}\"** from Panel #{panel_id}.\n"
            f"Run `/postpanel {panel_id}` to re-post the embed without this button.",
            ephemeral=True,
        )

    @commands.hybrid_command(
        name="setapplicationchannel",
        description="[Admin] Set a fallback notification channel for all panels",
    )
    @app_commands.describe(channel="Channel to receive application notifications")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def setapplicationchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO applications_config (guild_id, notification_channel_id)
                   VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET notification_channel_id = excluded.notification_channel_id""",
                (ctx.guild.id, str(channel.id)),
            )
            await db.commit()
        await ctx.send(
            f"Fallback application notifications → {channel.mention}.", ephemeral=True
        )

    @commands.hybrid_command(
        name="myapplication",
        description="View the status of your most recent application",
    )
    @commands.guild_only()
    async def myapplication(self, ctx: commands.Context):
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM applications WHERE guild_id = ? AND user_id = ?
                   ORDER BY id DESC LIMIT 1""",
                (ctx.guild.id, str(ctx.author.id)),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            await ctx.send(
                "You haven't submitted an application yet. Click an Apply button in the applications channel.",
                ephemeral=True,
            )
            return

        embed = application_embed(dict(row))
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Applications(bot))
