import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from db.database import DB_PATH


SECTION_PROMPTS = {
    "General Support": "Please describe your issue and a staff member will assist you shortly.",
    "Partnerships": (
        "Thank you for your interest in partnering with Norse Air Virtual Airlines!\n\n"
        "Please provide the following:\n"
        "• Your organization/group name\n"
        "• Type of partnership you're seeking\n"
        "• Any additional details"
    ),
}

DEFAULT_PROMPT = "Please describe your request and a staff member will assist you shortly."


async def get_config(guild_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT support_role_id, category_id, section1_label, section2_label FROM ticket_config WHERE guild_id = ?",
            (guild_id,),
        ) as cur:
            return await cur.fetchone()


async def create_ticket_channel(guild: discord.Guild, user: discord.Member, section: str):
    config = await get_config(guild.id)
    role_id = config[0] if config else None
    cat_id  = config[1] if config else None
    support_role = guild.get_role(role_id) if role_id else None
    category     = guild.get_channel(cat_id) if cat_id else None

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        user: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True),
    }
    if support_role:
        overwrites[support_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO tickets (guild_id, user_id, channel_id, section) VALUES (?, ?, ?, ?)",
            (guild.id, user.id, 0, section),
        )
        ticket_id = cur.lastrowid
        await db.commit()

    channel = await guild.create_text_channel(
        f"ticket-{ticket_id:04d}",
        overwrites=overwrites,
        category=category,
        topic=f"[{section}] Support ticket for {user} ({user.id})",
    )

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tickets SET channel_id = ? WHERE id = ?",
            (channel.id, ticket_id),
        )
        await db.commit()

    prompt = SECTION_PROMPTS.get(section, DEFAULT_PROMPT)
    embed = discord.Embed(
        title=f"🎫 {section}",
        description=f"Welcome {user.mention}!\n\n{prompt}\n\nClick **Close Ticket** or use `/closeticket` when resolved.",
        color=discord.Color(0x0B1F3A),
    )
    await channel.send(embed=embed, view=CloseTicketView())
    if support_role:
        await channel.send(support_role.mention, delete_after=3)

    return channel


class TicketPanel(discord.ui.View):
    def __init__(self, label1: str = "General Support", label2: str = "Flight Support"):
        super().__init__(timeout=None)
        self.label1 = label1
        self.label2 = label2

        self.add_item(TicketSectionButton(label1, "ticket:section1", discord.ButtonStyle.primary, "🎫"))
        self.add_item(TicketSectionButton(label2, "ticket:section2", discord.ButtonStyle.secondary, "🤝"))


class TicketSectionButton(discord.ui.Button):
    def __init__(self, label: str, custom_id: str, style: discord.ButtonStyle, emoji: str):
        super().__init__(label=label, custom_id=custom_id, style=style, emoji=emoji)

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        user  = interaction.user
        section = self.label

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT channel_id FROM tickets WHERE guild_id = ? AND user_id = ? AND closed = 0",
                (guild.id, user.id),
            ) as cur:
                existing = await cur.fetchone()

        if existing:
            ch = guild.get_channel(existing[0])
            if ch:
                return await interaction.response.send_message(
                    f"You already have an open ticket: {ch.mention}", ephemeral=True
                )

        await interaction.response.defer(ephemeral=True)
        channel = await create_ticket_channel(guild, user, section)
        await interaction.followup.send(f"Ticket opened: {channel.mention}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Close Ticket",
        style=discord.ButtonStyle.danger,
        emoji="🔒",
        custom_id="ticket:close",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await close_ticket_channel(interaction.channel, interaction.user)


async def close_ticket_channel(channel: discord.TextChannel, closer: discord.User):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM tickets WHERE channel_id = ? AND closed = 0",
            (channel.id,),
        ) as cur:
            row = await cur.fetchone()

    if not row:
        return

    embed = discord.Embed(
        description=f"🔒 Ticket closed by {closer.mention}.",
        color=discord.Color.red(),
    )
    await channel.send(embed=embed)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE tickets SET closed = 1 WHERE channel_id = ?",
            (channel.id,),
        )
        await db.commit()

    await channel.delete(reason=f"Ticket closed by {closer}")


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ticket_config (
                    guild_id        INTEGER PRIMARY KEY,
                    support_role_id INTEGER,
                    category_id     INTEGER,
                    section1_label  TEXT NOT NULL DEFAULT 'General Support',
                    section2_label  TEXT NOT NULL DEFAULT 'Flight Support'
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id   INTEGER NOT NULL,
                    user_id    INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    section    TEXT,
                    closed     INTEGER NOT NULL DEFAULT 0
                )
            """)
            await db.execute(
                "INSERT OR IGNORE INTO sqlite_sequence (name, seq) VALUES ('tickets', 57)"
            )
            await db.commit()

        self.bot.add_view(TicketPanel())
        self.bot.add_view(CloseTicketView())

    @commands.hybrid_command(name="settickets", description="Post a ticket panel with 2 sections")
    @app_commands.describe(
        channel="Channel to post the ticket panel in",
        category="Category where ticket channels will be created",
        support_role="Role to ping and grant access to all tickets",
        section1="Label for the first ticket type (default: General Support)",
        section2="Label for the second ticket type (default: Flight Support)",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def settickets(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        category: discord.CategoryChannel,
        support_role: discord.Role = None,
        section1: str = "General Support",
        section2: str = "Partnerships",
    ):
        await ctx.defer(ephemeral=True)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO ticket_config (guild_id, support_role_id, category_id, section1_label, section2_label)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET
                    support_role_id = excluded.support_role_id,
                    category_id     = excluded.category_id,
                    section1_label  = excluded.section1_label,
                    section2_label  = excluded.section2_label
            """, (ctx.guild.id, support_role.id if support_role else None, category.id, section1, section2))
            await db.commit()

        embed = discord.Embed(
            title="🎫 Norse Air Support",
            description=(
                f"Need help? Choose a category below to open a private support ticket.\n\n"
                f"🎫 **{section1}** — general questions and assistance\n"
                f"🤝 **{section2}** — partnership inquiries and collaborations"
            ),
            color=discord.Color(0x0B1F3A),
        )
        await channel.send(embed=embed, view=TicketPanel(section1, section2))
        await ctx.send(
            f"Ticket panel posted in {channel.mention}. Channels will be created in **{category.name}**.",
            ephemeral=True,
        )

    @commands.hybrid_command(name="closeticket", description="Close the current support ticket")
    async def closeticket(self, ctx: commands.Context):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT user_id FROM tickets WHERE channel_id = ? AND closed = 0",
                (ctx.channel.id,),
            ) as cur:
                row = await cur.fetchone()

        if not row:
            return await ctx.send("This is not an open ticket channel.", ephemeral=True)

        await ctx.send("Closing ticket...")
        await close_ticket_channel(ctx.channel, ctx.author)

    @commands.hybrid_command(name="addtoticket", description="Add a user to the current ticket")
    @app_commands.describe(user="User to add to this ticket")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def addtoticket(self, ctx: commands.Context, user: discord.Member):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id FROM tickets WHERE channel_id = ? AND closed = 0",
                (ctx.channel.id,),
            ) as cur:
                row = await cur.fetchone()

        if not row:
            return await ctx.send("This is not an open ticket channel.", ephemeral=True)

        await ctx.channel.set_permissions(user, view_channel=True, send_messages=True)
        await ctx.send(f"Added {user.mention} to the ticket.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
