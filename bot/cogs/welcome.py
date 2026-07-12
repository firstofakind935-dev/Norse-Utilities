import os

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands
from db.database import DB_PATH

BANNER_URL = os.getenv(
    "BANNER_URL",
    "https://i.postimg.cc/fL2Q5LNV/banner1.webp"
)


def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS welcome_config (
                    guild_id INTEGER PRIMARY KEY,
                    welcome_channel_id INTEGER,
                    helpdesk_channel_id INTEGER,
                    verify_channel_id INTEGER
                )
            """)
            try:
                await db.execute("ALTER TABLE welcome_config ADD COLUMN verify_channel_id INTEGER")
            except Exception:
                pass
            await db.commit()

    async def get_config(self, guild_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT welcome_channel_id, helpdesk_channel_id, verify_channel_id FROM welcome_config WHERE guild_id = ?",
                (guild_id,)
            ) as cur:
                return await cur.fetchone()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        config = await self.get_config(member.guild.id)
        if not config or not config[0]:
            return

        welcome_channel_id = config[0]
        helpdesk_channel_id = config[1]
        verify_channel_id = config[2] if len(config) > 2 else None

        channel = member.guild.get_channel(welcome_channel_id)
        if not channel:
            return

        helpdesk = member.guild.get_channel(helpdesk_channel_id) if helpdesk_channel_id else None
        helpdesk_mention = helpdesk.mention if helpdesk else "#helpdesk"

        verify = member.guild.get_channel(verify_channel_id) if verify_channel_id else None
        verify_mention = verify.mention if verify else "#verify-here"

        member_number = ordinal(member.guild.member_count)

        embed = discord.Embed(
            title="<:Flag:1504848692195365065> Welcome!",
            description=(
                f"<:KE_Arrow:1510682534189731910> We're pleased to have you here. Kindly proceed to {verify_mention} "
                f"to complete your verification and gain full access.\n\n"
                f"<:KE_Arrow:1510682534189731910> If you require any assistance, feel free to reach out at any time at "
                f"{helpdesk_mention}."
            ),
            color=discord.Color(0x0B1F3A),
        )
        embed.set_image(url=BANNER_URL)

        await channel.send(
            f"<:Flag:1504848692195365065> Welcome to **Norse Air PTFS** {member.mention}! "
            f"you are our **{member_number}** member",
            embed=embed,
        )

    @commands.hybrid_command(name="setwelcome", description="Set the welcome channel for this server")
    @app_commands.describe(channel="The channel to send welcome messages in")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def setwelcome(self, ctx: commands.Context, channel: discord.TextChannel):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO welcome_config (guild_id, welcome_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id = ?
            """, (ctx.guild.id, channel.id, channel.id))
            await db.commit()
        await ctx.send(f"Welcome channel set to {channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="sethelpdesk", description="Set the helpdesk channel for this server")
    @app_commands.describe(channel="The helpdesk channel to link to in welcome messages")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def sethelpdesk(self, ctx: commands.Context, channel: discord.TextChannel):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO welcome_config (guild_id, helpdesk_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET helpdesk_channel_id = ?
            """, (ctx.guild.id, channel.id, channel.id))
            await db.commit()
        await ctx.send(f"Helpdesk channel set to {channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="setverify", description="Set the verification channel linked in welcome messages")
    @app_commands.describe(channel="The verification channel")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def setverify(self, ctx: commands.Context, channel: discord.TextChannel):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                INSERT INTO welcome_config (guild_id, verify_channel_id)
                VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET verify_channel_id = ?
            """, (ctx.guild.id, channel.id, channel.id))
            await db.commit()
        await ctx.send(f"Verify channel set to {channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="testwelcome", description="Test the welcome message")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def testwelcome(self, ctx: commands.Context):
        """Send a test welcome message for the current user."""
        await ctx.defer(ephemeral=True)
        try:
            await self.on_member_join(ctx.author)
            await ctx.send("Test welcome sent!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"Error: `{e}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
