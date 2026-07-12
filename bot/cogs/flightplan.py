import json
import aiosqlite
import discord
from datetime import datetime, timezone
from discord import app_commands
from discord.ext import commands

from db.database import DB_PATH

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLORS = {
    "commercial": 0x0B1F3A,
    "training": 0x2ECC71,
    "evaluation": 0xE74C3C,
}

STATUS_EMOJI = {
    "pending": "⏳",
    "approved": "✅",
    "rejected": "❌",
}

PLAN_TYPES = ["commercial", "training", "evaluation"]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def plan_embed(plan: dict) -> discord.Embed:
    """Build a discord.Embed for any plan type from a DB row dict."""
    plan_type = plan["type"]
    status = plan["status"]
    data = plan["data"] if isinstance(plan["data"], dict) else json.loads(plan["data"])

    color = COLORS.get(plan_type, 0x95A5A6)
    emoji = STATUS_EMOJI.get(status, "❓")

    embed = discord.Embed(
        title=f"{plan_type.capitalize()} Flight Plan #{plan['id']}",
        color=color,
    )
    embed.add_field(name="Status", value=f"{emoji} {status.capitalize()}", inline=True)
    embed.add_field(name="Submitted By", value=plan["submitted_by_name"], inline=True)
    embed.add_field(name="Submitted At", value=plan["submitted_at"], inline=True)

    if plan_type == "commercial":
        embed.add_field(name="Flight Number", value=data.get("flight_number", "N/A"), inline=True)
        embed.add_field(name="Route", value=data.get("route", "N/A"), inline=True)
        embed.add_field(name="Aircraft", value=data.get("aircraft", "N/A"), inline=True)
        embed.add_field(name="Departure Time (UTC)", value=data.get("departure_time", "N/A"), inline=True)
        if data.get("remarks"):
            embed.add_field(name="Remarks", value=data["remarks"], inline=False)

    elif plan_type == "training":
        embed.add_field(name="Trainee", value=data.get("trainee", "N/A"), inline=True)
        embed.add_field(name="Trainer", value=data.get("trainer", "N/A"), inline=True)
        embed.add_field(name="Training Type", value=data.get("training_type", "N/A"), inline=True)
        embed.add_field(name="Scheduled Time (UTC)", value=data.get("scheduled_time", "N/A"), inline=True)
        if data.get("objectives"):
            embed.add_field(name="Objectives", value=data["objectives"], inline=False)

    elif plan_type == "evaluation":
        embed.add_field(name="Examinee", value=data.get("examinee", "N/A"), inline=True)
        embed.add_field(name="Examiner", value=data.get("examiner", "N/A"), inline=True)
        embed.add_field(name="Evaluation Type", value=data.get("eval_type", "N/A"), inline=True)
        embed.add_field(name="Scheduled Time (UTC)", value=data.get("scheduled_time", "N/A"), inline=True)
        if data.get("scope"):
            embed.add_field(name="Scope", value=data["scope"], inline=False)

    if plan.get("reviewed_by"):
        embed.add_field(name="Reviewed By", value=plan["reviewed_by"], inline=True)
    if plan.get("reviewed_at"):
        embed.add_field(name="Reviewed At", value=plan["reviewed_at"], inline=True)
    if plan.get("review_notes"):
        embed.add_field(name="Review Notes", value=plan["review_notes"], inline=False)

    embed.set_footer(text=f"Plan ID: {plan['id']} | Guild: {plan['guild_id']}")
    return embed


# ---------------------------------------------------------------------------
# Modals
# ---------------------------------------------------------------------------

class CommercialModal(discord.ui.Modal, title="Commercial Flight Plan"):
    flight_number = discord.ui.TextInput(
        label="Flight Number",
        placeholder="e.g. KE001",
        max_length=20,
    )
    route = discord.ui.TextInput(
        label="Route",
        placeholder="e.g. ICN → LAX",
        max_length=100,
    )
    aircraft = discord.ui.TextInput(
        label="Aircraft",
        placeholder="e.g. B777-300ER",
        max_length=50,
    )
    departure_time = discord.ui.TextInput(
        label="Departure Time (UTC)",
        placeholder="e.g. 2026-06-15 14:30",
        max_length=50,
    )
    remarks = discord.ui.TextInput(
        label="Remarks (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Any additional remarks...",
        required=False,
        max_length=1000,
    )

    def __init__(self, cog: "FlightPlan"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "flight_number": self.flight_number.value.strip(),
            "route": self.route.value.strip(),
            "aircraft": self.aircraft.value.strip(),
            "departure_time": self.departure_time.value.strip(),
            "remarks": self.remarks.value.strip() or None,
        }
        await self.cog._submit_plan(interaction, "commercial", data)


class TrainingModal(discord.ui.Modal, title="Training Flight Plan"):
    trainee = discord.ui.TextInput(
        label="Trainee",
        placeholder="Full name or callsign",
        max_length=100,
    )
    trainer = discord.ui.TextInput(
        label="Trainer",
        placeholder="Full name or callsign",
        max_length=100,
    )
    training_type = discord.ui.TextInput(
        label="Training Type",
        placeholder="e.g. Line Training, Simulator",
        max_length=100,
    )
    scheduled_time = discord.ui.TextInput(
        label="Scheduled Time (UTC)",
        placeholder="e.g. 2026-06-15 14:30",
        max_length=50,
    )
    objectives = discord.ui.TextInput(
        label="Objectives (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Training objectives...",
        required=False,
        max_length=1000,
    )

    def __init__(self, cog: "FlightPlan"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "trainee": self.trainee.value.strip(),
            "trainer": self.trainer.value.strip(),
            "training_type": self.training_type.value.strip(),
            "scheduled_time": self.scheduled_time.value.strip(),
            "objectives": self.objectives.value.strip() or None,
        }
        await self.cog._submit_plan(interaction, "training", data)


class EvaluationModal(discord.ui.Modal, title="Evaluation Flight Plan"):
    examinee = discord.ui.TextInput(
        label="Examinee",
        placeholder="Full name or callsign",
        max_length=100,
    )
    examiner = discord.ui.TextInput(
        label="Examiner",
        placeholder="Full name or callsign",
        max_length=100,
    )
    eval_type = discord.ui.TextInput(
        label="Evaluation Type",
        placeholder="e.g. Line Check, OPC, PC",
        max_length=100,
    )
    scheduled_time = discord.ui.TextInput(
        label="Scheduled Time (UTC)",
        placeholder="e.g. 2026-06-15 14:30",
        max_length=50,
    )
    scope = discord.ui.TextInput(
        label="Scope (optional)",
        style=discord.TextStyle.paragraph,
        placeholder="Evaluation scope and areas to be assessed...",
        required=False,
        max_length=1000,
    )

    def __init__(self, cog: "FlightPlan"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction):
        data = {
            "examinee": self.examinee.value.strip(),
            "examiner": self.examiner.value.strip(),
            "eval_type": self.eval_type.value.strip(),
            "scheduled_time": self.scheduled_time.value.strip(),
            "scope": self.scope.value.strip() or None,
        }
        await self.cog._submit_plan(interaction, "evaluation", data)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class FlightPlan(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    async def cog_load(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS flight_plans (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id           INTEGER NOT NULL,
                    type               TEXT    NOT NULL,
                    status             TEXT    NOT NULL DEFAULT 'pending',
                    submitted_by_id    TEXT    NOT NULL,
                    submitted_by_name  TEXT    NOT NULL,
                    submitted_at       TEXT    NOT NULL,
                    data               TEXT    NOT NULL,
                    reviewed_by        TEXT,
                    reviewed_at        TEXT,
                    review_notes       TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS flightplan_config (
                    guild_id                 INTEGER PRIMARY KEY,
                    notification_channel_id  TEXT
                )
            """)
            await db.commit()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    async def _submit_plan(
        self,
        interaction: discord.Interaction,
        plan_type: str,
        data: dict,
    ):
        """Insert a plan row, reply ephemerally, then post to the notification channel."""
        submitted_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        data_json = json.dumps(data, ensure_ascii=False)

        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """
                INSERT INTO flight_plans
                    (guild_id, type, status, submitted_by_id, submitted_by_name, submitted_at, data)
                VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (
                    interaction.guild_id,
                    plan_type,
                    str(interaction.user.id),
                    interaction.user.display_name,
                    submitted_at,
                    data_json,
                ),
            )
            plan_id = cursor.lastrowid
            await db.commit()

            async with db.execute(
                "SELECT notification_channel_id FROM flightplan_config WHERE guild_id = ?",
                (interaction.guild_id,),
            ) as cur:
                row = await cur.fetchone()
                notification_channel_id = row[0] if row else None

        plan = {
            "id": plan_id,
            "guild_id": interaction.guild_id,
            "type": plan_type,
            "status": "pending",
            "submitted_by_id": str(interaction.user.id),
            "submitted_by_name": interaction.user.display_name,
            "submitted_at": submitted_at,
            "data": data,
            "reviewed_by": None,
            "reviewed_at": None,
            "review_notes": None,
        }
        embed = plan_embed(plan)

        await interaction.response.send_message(
            content=f"Your **{plan_type}** flight plan (ID `{plan_id}`) has been submitted and is pending review.",
            embed=embed,
            ephemeral=True,
        )

        if notification_channel_id:
            channel = interaction.guild.get_channel(int(notification_channel_id))
            if channel:
                notify_embed = plan_embed(plan)
                notify_embed.set_author(
                    name=f"New {plan_type.capitalize()} Flight Plan Submitted",
                    icon_url=interaction.user.display_avatar.url,
                )
                try:
                    await channel.send(embed=notify_embed)
                except discord.Forbidden:
                    pass

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------

    @commands.hybrid_command(
        name="flightplan",
        description="Submit a flight plan (commercial, training, or evaluation)",
    )
    @app_commands.describe(type="The type of flight plan to submit")
    @app_commands.choices(type=[
        app_commands.Choice(name="Commercial", value="commercial"),
        app_commands.Choice(name="Training", value="training"),
        app_commands.Choice(name="Evaluation", value="evaluation"),
    ])
    async def flightplan(self, ctx: commands.Context, type: str):
        """Open a flight plan submission modal. Slash command only."""
        if ctx.interaction is None:
            await ctx.send(
                "This command must be used as a slash command (`/flightplan`).",
                ephemeral=True,
            )
            return

        if type not in PLAN_TYPES:
            await ctx.interaction.response.send_message(
                f"Invalid plan type. Choose from: {', '.join(PLAN_TYPES)}.",
                ephemeral=True,
            )
            return

        modal_map = {
            "commercial": CommercialModal,
            "training": TrainingModal,
            "evaluation": EvaluationModal,
        }
        modal = modal_map[type](self)
        await ctx.interaction.response.send_modal(modal)

    @commands.hybrid_command(
        name="myplans",
        description="View your last 10 submitted flight plans",
    )
    async def myplans(self, ctx: commands.Context):
        """Show the caller's last 10 flight plans (ephemeral)."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, type, status, submitted_at
                FROM flight_plans
                WHERE guild_id = ? AND submitted_by_id = ?
                ORDER BY id DESC
                LIMIT 10
                """,
                (ctx.guild.id, str(ctx.author.id)),
            ) as cur:
                rows = await cur.fetchall()

        embed = discord.Embed(
            title=f"{ctx.author.display_name}'s Flight Plans",
            color=0x0B1F3A,
        )

        if not rows:
            embed.description = "You have no submitted flight plans."
        else:
            lines = []
            for row in rows:
                emoji = STATUS_EMOJI.get(row["status"], "❓")
                lines.append(
                    f"`#{row['id']}` **{row['type'].capitalize()}** — "
                    f"{emoji} {row['status'].capitalize()} — {row['submitted_at']}"
                )
            embed.description = "\n".join(lines)

        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="viewplan",
        description="View the full details of a flight plan by ID",
    )
    @app_commands.describe(plan_id="The numeric ID of the flight plan")
    async def viewplan(self, ctx: commands.Context, plan_id: int):
        """Show the full embed for a given plan ID (visible to all)."""
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM flight_plans WHERE id = ? AND guild_id = ?",
                (plan_id, ctx.guild.id),
            ) as cur:
                row = await cur.fetchone()

        if row is None:
            await ctx.send(
                f"No flight plan with ID `{plan_id}` found in this server.",
                ephemeral=True,
            )
            return

        plan = dict(row)
        embed = plan_embed(plan)
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="setflightplanchannel",
        description="[Admin] Set the channel where new flight plans are posted",
    )
    @app_commands.describe(channel="The text channel to receive flight plan notifications")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def setflightplanchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Save the notification channel for flight plan submissions."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO flightplan_config (guild_id, notification_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET notification_channel_id = excluded.notification_channel_id
                """,
                (ctx.guild.id, str(channel.id)),
            )
            await db.commit()

        await ctx.send(
            f"Flight plan notifications will now be posted to {channel.mention}.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(FlightPlan(bot))
