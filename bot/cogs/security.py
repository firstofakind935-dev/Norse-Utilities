import re
import json
import asyncio
from collections import deque
from datetime import datetime, timezone
from typing import Optional

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from db.database import DB_PATH

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPAM_THRESHOLD = 5   # messages within the window = threat
SPAM_WINDOW    = 5   # seconds
MENTION_LIMIT  = 5   # individual @user mentions in one message

IP_LOGGER_DOMAINS = {
    "grabify.link", "grabify.co", "grabify.gg",
    "iplogger.org", "iplogger.co", "iplogger.ru",
    "2no.co", "yip.su", "blasze.com", "blasze.tk",
    "ps3cfw.com", "crabrave.pw", "ipgrabber.gr",
    "sexyphotos.xyz", "yourphotos.us", "leakedzone.com",
    "screenshot.host", "lovebird.guru", "trk.pt",
    "stopify.co", "linkvertise.com", "dis.gd",
    "bit.ly",  # commonly used to mask IP loggers
}

URL_RE = re.compile(r"https?://(?:www\.)?([^/\s]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Security(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._msg_log: dict[int, deque] = {}
        self._handled: set[int] = set()

    # -----------------------------------------------------------------------
    # Setup
    # -----------------------------------------------------------------------

    async def cog_load(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS security_config (
                    guild_id     INTEGER PRIMARY KEY,
                    role_ids     TEXT NOT NULL DEFAULT '[]',
                    category_id  TEXT
                )
            """)
            await db.commit()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def _get_config(self, guild_id: int) -> dict:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT role_ids, category_id FROM security_config WHERE guild_id = ?",
                (guild_id,),
            ) as cur:
                row = await cur.fetchone()
        if row:
            return {"role_ids": json.loads(row[0]), "category_id": row[1]}
        return {"role_ids": [], "category_id": None}

    def _check_spam(self, user_id: int) -> bool:
        now = datetime.now().timestamp()
        log = self._msg_log.setdefault(user_id, deque())
        log.append(now)
        while log and log[0] < now - SPAM_WINDOW:
            log.popleft()
        return len(log) >= SPAM_THRESHOLD

    @staticmethod
    def _find_ip_loggers(content: str) -> list:
        found = []
        for match in URL_RE.finditer(content):
            domain = match.group(1).lower()
            if any(domain == d or domain.endswith("." + d) for d in IP_LOGGER_DOMAINS):
                found.append(match.group(0))
        return found

    # -----------------------------------------------------------------------
    # Threat handler
    # -----------------------------------------------------------------------

    async def _handle_threat(
        self,
        guild: discord.Guild,
        member: discord.Member,
        threats: list,
        trigger_msg: Optional[discord.Message] = None,
    ):
        if member.id in self._handled:
            return
        self._handled.add(member.id)

        ban_reason = "Security Auto-Ban: " + " | ".join(t[1] for t in threats)

        # 1. Ban immediately (delete 7 days of messages)
        ban_success = True
        try:
            await guild.ban(member, reason=ban_reason, delete_message_days=7)
        except (discord.Forbidden, discord.HTTPException):
            ban_success = False

        cfg = await self._get_config(guild.id)

        # 2. Build incident embed
        embed = discord.Embed(
            title="🚨 Security Incident Report",
            colour=0xE74C3C,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Account",
            value=f"{member.mention} — `{member}` (`{member.id}`)",
            inline=False,
        )
        embed.add_field(
            name="Account Type",
            value="🤖 Bot" if member.bot else "👤 User",
            inline=True,
        )
        embed.add_field(
            name="Action Taken",
            value=f"{'✅ Banned' if ban_success else '❌ Ban failed (missing perms)'} + 7-day message purge",
            inline=True,
        )
        embed.add_field(
            name="Threats Detected",
            value="\n".join(f"• {t[1]}" for t in threats),
            inline=False,
        )

        ip_leak = any(t[0] == "ip_logger" for t in threats)
        if ip_leak:
            embed.add_field(
                name="⚠️ IP Leak Warning",
                value=(
                    "IP logger links were sent in this server. "
                    "Any member who **clicked** those links may have had their IP address recorded. "
                    "Advise all members to **avoid clicking** any links sent by this account "
                    "and to consider using a VPN."
                ),
                inline=False,
            )

        if trigger_msg:
            preview = (trigger_msg.content or "[no text content]")[:500]
            embed.add_field(
                name="Triggering Message",
                value=f"```{preview}```",
                inline=False,
            )
            embed.add_field(
                name="Channel",
                value=trigger_msg.channel.mention,
                inline=True,
            )

        embed.add_field(
            name="Next Steps",
            value=(
                "• Review the incident details above\n"
                "• Check if any members clicked suspicious links\n"
                "• Use `/closeincident` when resolved to delete this channel"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Guild: {guild.name} | Auto-ban executed")

        # 3. Build channel permission overwrites
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                embed_links=True,
                read_message_history=True,
                manage_channels=True,
            ),
        }

        alert_roles = []
        for role_id in cfg["role_ids"]:
            role = guild.get_role(int(role_id))
            if role:
                alert_roles.append(role)
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        category = None
        if cfg["category_id"]:
            category = guild.get_channel(int(cfg["category_id"]))

        ts = datetime.now(timezone.utc).strftime("%m%d-%H%M")
        try:
            incident_channel = await guild.create_text_channel(
                name=f"security-incident-{ts}",
                overwrites=overwrites,
                category=category,
                topic=f"Security incident — {member} ({member.id}) auto-banned.",
                reason="Automated security incident channel",
            )
        except (discord.Forbidden, discord.HTTPException):
            return

        # 4. Ping roles and post report
        mentions = " ".join(r.mention for r in alert_roles) if alert_roles else ""
        await incident_channel.send(
            content=f"{mentions}\n🚨 **Automatic security action has been taken.** See the report below.".strip(),
            embed=embed,
        )

    # -----------------------------------------------------------------------
    # Listeners
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        if message.author.id == self.bot.user.id:
            return

        member = message.guild.get_member(message.author.id)
        if member is None:
            return
        if member.id in self._handled:
            return
        if member.guild_permissions.administrator:
            return

        threats = []

        # Spam detection
        if self._check_spam(member.id):
            threats.append(("spam", f"Message spam — {SPAM_THRESHOLD}+ messages in {SPAM_WINDOW}s"))

        # Mass mention detection
        if message.mention_everyone:
            threats.append(("mass_mention", "@everyone / @here used"))
        elif len(message.mentions) >= MENTION_LIMIT:
            threats.append(("mass_mention", f"Mass mention — {len(message.mentions)} users pinged in one message"))

        # IP logger detection
        bad_urls = self._find_ip_loggers(message.content)
        if bad_urls:
            threats.append(("ip_logger", f"IP logger link(s): {', '.join(bad_urls[:3])}"))

        if threats:
            await self._handle_threat(message.guild, member, threats, trigger_msg=message)

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------

    @commands.hybrid_command(
        name="setsecurityroles",
        description="[Admin] Set the roles pinged in security incident channels",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        role1="First role to alert (e.g. Board of Directors)",
        role2="Second role to alert (e.g. Norse Air Leadership)",
    )
    async def setsecurityroles(
        self,
        ctx: commands.Context,
        role1: discord.Role,
        role2: Optional[discord.Role] = None,
    ):
        """Set the roles that get pinged during a security incident."""
        role_ids = [str(role1.id)]
        if role2:
            role_ids.append(str(role2.id))

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO security_config (guild_id, role_ids)
                   VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET role_ids = excluded.role_ids""",
                (ctx.guild.id, json.dumps(role_ids)),
            )
            await db.commit()

        names = role1.name + (f", {role2.name}" if role2 else "")
        await ctx.send(f"Security alert roles set: **{names}**", ephemeral=True)

    @commands.hybrid_command(
        name="setsecuritycategory",
        description="[Admin] Set the category where incident channels are created",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(category="The category to create incident channels under")
    async def setsecuritycategory(
        self, ctx: commands.Context, category: discord.CategoryChannel
    ):
        """Set the category for incident channels."""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO security_config (guild_id, category_id)
                   VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET category_id = excluded.category_id""",
                (ctx.guild.id, str(category.id)),
            )
            await db.commit()

        await ctx.send(
            f"Incident channels will be created under **{category.name}**.", ephemeral=True
        )

    @commands.hybrid_command(
        name="testsecurity",
        description="[Admin] Simulate a security incident to test the alert system",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(silent="If True, creates the channel and report without pinging roles")
    async def testsecurity(self, ctx: commands.Context, silent: bool = False):
        """Fire a fake incident — creates the channel and report without banning anyone."""
        await ctx.send(
            f"🔧 Running security system test {'(silent)' if silent else ''}…",
            ephemeral=True,
        )

        fake_threats = [
            ("spam", "TEST — Message spam: 5+ messages in 5s"),
            ("mass_mention", "TEST — @everyone used"),
            ("ip_logger", "TEST — IP logger link: grabify.link/test123"),
        ]

        cfg = await self._get_config(ctx.guild.id)

        embed = discord.Embed(
            title="🚨 Security Incident Report — TEST",
            description="This is a **test** triggered by staff. No action has been taken.",
            colour=0xF39C12,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="Account",
            value=f"{ctx.author.mention} — `{ctx.author}` (`{ctx.author.id}`)",
            inline=False,
        )
        embed.add_field(name="Account Type", value="👤 User (test)", inline=True)
        embed.add_field(name="Action Taken", value="⚠️ None — this is a test", inline=True)
        embed.add_field(
            name="Simulated Threats",
            value="\n".join(f"• {t[1]}" for t in fake_threats),
            inline=False,
        )
        embed.add_field(
            name="⚠️ IP Leak Warning (simulated)",
            value=(
                "IP logger links were sent in this server. "
                "Any member who **clicked** those links may have had their IP address recorded. "
                "Advise all members to **avoid clicking** any links sent by this account "
                "and to consider using a VPN."
            ),
            inline=False,
        )
        embed.add_field(
            name="Next Steps",
            value=(
                "• Review the incident details above\n"
                "• Check if any members clicked suspicious links\n"
                "• Use `/closeincident` when resolved to delete this channel"
            ),
            inline=False,
        )
        embed.set_footer(text=f"TEST | Guild: {ctx.guild.name}")

        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(view_channel=False),
            ctx.guild.me: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                manage_messages=True,
                embed_links=True,
                read_message_history=True,
                manage_channels=True,
            ),
        }

        alert_roles = []
        for role_id in cfg["role_ids"]:
            role = ctx.guild.get_role(int(role_id))
            if role:
                alert_roles.append(role)
                overwrites[role] = discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                )

        category = None
        if cfg["category_id"]:
            category = ctx.guild.get_channel(int(cfg["category_id"]))

        ts = datetime.now(timezone.utc).strftime("%m%d-%H%M")
        try:
            incident_channel = await ctx.guild.create_text_channel(
                name=f"security-incident-{ts}",
                overwrites=overwrites,
                category=category,
                topic="Security system test — no real threat.",
                reason="Security system test",
            )
        except (discord.Forbidden, discord.HTTPException) as e:
            await ctx.send(f"❌ Could not create incident channel: `{e}`", ephemeral=True)
            return

        mentions = " ".join(r.mention for r in alert_roles) if (alert_roles and not silent) else ""
        await incident_channel.send(
            content=f"{mentions}\n🔧 **Security system test** — see the simulated report below.".strip(),
            embed=embed,
        )
        await ctx.send(
            f"✅ Test complete — incident channel created: {incident_channel.mention}",
            ephemeral=True,
        )

    @commands.hybrid_command(
        description="[Admin] Mark incident as resolved and delete this channel",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def closeincident(self, ctx: commands.Context):
        """Close the current incident channel."""
        if "incident" not in ctx.channel.name:
            await ctx.send(
                "This command can only be used in a security incident channel.", ephemeral=True
            )
            return
        await ctx.send("✅ Incident marked as resolved. Deleting this channel in 5 seconds…")
        await asyncio.sleep(5)
        try:
            await ctx.channel.delete(reason="Incident closed by staff")
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Security(bot))
