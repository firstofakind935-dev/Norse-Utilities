from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Literal

import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands

from db.database import DB_PATH

STRIKE_THRESHOLDS = [3, 6, 8]  # warn counts that trigger strikes 1, 2, 3


def _get_strike_level(active_warn_count: int) -> int:
    """Returns current strike level (0-3) based on active warning count."""
    level = 0
    for t in STRIKE_THRESHOLDS:
        if active_warn_count >= t:
            level += 1
    return level


def _threshold_crossed(old_count: int, new_count: int) -> Optional[int]:
    """Returns strike number (1-3) if new_count crossed a threshold, else None."""
    for i, t in enumerate(STRIKE_THRESHOLDS, 1):
        if old_count < t <= new_count:
            return i
    return None


def _parse_expires_at(amount: int, unit: str) -> Optional[str]:
    """Returns ISO 8601 expiry timestamp string, or None for unknown unit."""
    unit_seconds = {"hours": 3600, "days": 86400, "weeks": 604800}
    seconds = unit_seconds.get(unit)
    if seconds is None:
        return None
    dt = datetime.now(timezone.utc) + timedelta(seconds=amount * seconds)
    return dt.isoformat()


@dataclass
class _Emojis:
    exclamation: object
    user: object
    badge: object
    arrow: object


class Warnings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS warn_config (
                    guild_id       INTEGER PRIMARY KEY,
                    log_channel_id INTEGER NOT NULL
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS warnings (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id   INTEGER NOT NULL,
                    user_id    INTEGER NOT NULL,
                    reason     TEXT NOT NULL,
                    issued_by  INTEGER NOT NULL,
                    issued_at  TEXT NOT NULL,
                    expires_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS strikes (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id           INTEGER NOT NULL,
                    user_id            INTEGER NOT NULL,
                    strike_number      INTEGER NOT NULL,
                    reason             TEXT NOT NULL,
                    issued_by          INTEGER NOT NULL,
                    issued_at          TEXT NOT NULL,
                    triggering_warn_id INTEGER NOT NULL
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_warnings_guild_user
                    ON warnings (guild_id, user_id)
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_strikes_guild_user
                    ON strikes (guild_id, user_id)
            """)
            await db.commit()

    async def _get_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT log_channel_id FROM warn_config WHERE guild_id = ?",
                (guild.id,),
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        return guild.get_channel(row[0])

    async def _get_active_warn_count(self, guild_id: int, user_id: int) -> int:
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT COUNT(*) FROM warnings
                   WHERE guild_id = ? AND user_id = ?
                   AND (expires_at IS NULL OR expires_at > ?)""",
                (guild_id, user_id, now),
            ) as cur:
                row = await cur.fetchone()
        return row[0] if row else 0

    async def _post_embed(self, channel: discord.TextChannel, embed: discord.Embed):
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    def _get_emojis(self, guild: discord.Guild) -> "_Emojis":
        return _Emojis(
            exclamation=discord.utils.get(guild.emojis, name="KE_Exclamation"),
            user=discord.utils.get(guild.emojis, name="KE_User"),
            badge=discord.utils.get(guild.emojis, name="KE_Badge"),
            arrow=discord.utils.get(guild.emojis, name="KE_Arrow"),
        )

    def _warn_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        warn_num: int,
        reason: str,
        expires_at: Optional[str],
        issued_by: discord.Member,
    ) -> discord.Embed:
        e = self._get_emojis(guild)

        top_role = next((r for r in reversed(member.roles) if r.name != "@everyone"), None)
        position = top_role.mention if top_role else "No role"

        if expires_at:
            dt = datetime.fromisoformat(expires_at)
            expires_str = discord.utils.format_dt(dt, style="R")
        else:
            expires_str = "Permanent"

        embed = discord.Embed(
            title=f"{e.exclamation or '⚠️'} Warning #{warn_num}",
            color=0xF1C40F,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name=f"{e.user} User" if e.user else "User", value=member.mention, inline=False)
        embed.add_field(name=f"{e.badge} Position" if e.badge else "Position", value=position, inline=False)
        embed.add_field(name=f"{e.arrow} Reason" if e.arrow else "Reason", value=reason, inline=False)
        embed.add_field(name="Expires", value=expires_str, inline=True)
        embed.add_field(name="Issued by", value=issued_by.mention, inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed

    def _strike_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        strike_num: int,
        warn_count: int,
        reason: str,
        expires_at: Optional[str],
        issued_by: discord.Member,
    ) -> discord.Embed:
        e = self._get_emojis(guild)

        top_role = next((r for r in reversed(member.roles) if r.name != "@everyone"), None)
        position = top_role.mention if top_role else "No role"

        color = 0xE74C3C if strike_num == 3 else 0xE67E22

        if expires_at:
            dt = datetime.fromisoformat(expires_at)
            expires_str = discord.utils.format_dt(dt, style="R")
        else:
            expires_str = "Permanent"

        embed = discord.Embed(
            title=f"{e.exclamation or '🚨'} Strike #{strike_num}",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name=f"{e.user} User" if e.user else "User", value=member.mention, inline=False)
        embed.add_field(name=f"{e.badge} Position" if e.badge else "Position", value=position, inline=False)
        embed.add_field(name=f"{e.arrow} Reason" if e.arrow else "Reason", value=reason, inline=False)
        embed.add_field(name="Active Warnings", value=str(warn_count), inline=True)
        embed.add_field(name="Expires", value=expires_str, inline=True)
        embed.add_field(name="Issued by", value=issued_by.mention, inline=True)
        if strike_num == 3:
            embed.add_field(
                name="⚠️ Action Required",
                value="This member has reached 3 strikes. Admin action (role removal or termination) is required.",
                inline=False,
            )
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed

    def _removal_embed(
        self,
        guild: discord.Guild,
        member: discord.Member,
        action: str,
        issued_by: discord.Member,
    ) -> discord.Embed:
        e = self._get_emojis(guild)
        check = discord.utils.get(guild.emojis, name="KE_Check")

        embed = discord.Embed(
            title=f"{check or '✅'} {action}",
            color=0x2ECC71,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name=f"{e.user} User" if e.user else "User", value=member.mention, inline=False)
        embed.add_field(name=f"{e.arrow} Action by" if e.arrow else "Action by", value=issued_by.mention, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        return embed


    @commands.hybrid_command(name="setwarnlog", description="[Admin] Set the channel for warn/strike logs")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(channel="Channel to post warn/strike embeds in")
    async def setwarnlog(self, ctx: commands.Context, channel: discord.TextChannel):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """INSERT INTO warn_config (guild_id, log_channel_id) VALUES (?, ?)
                   ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id""",
                (ctx.guild.id, channel.id),
            )
            await db.commit()
        await ctx.send(f"Warn log channel set to {channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="warn", description="[Admin] Issue a warning to a member")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        member="The member to warn",
        reason="Reason for the warning",
        amount="Duration amount (e.g. 3) — leave blank for permanent",
        unit="Duration unit — leave blank for permanent",
        strike_reason="Reason for the strike if this warn triggers one (defaults to warn reason)",
    )
    async def warn(
        self,
        ctx: commands.Context,
        member: discord.Member,
        reason: str,
        amount: Optional[int] = None,
        unit: Optional[Literal["hours", "days", "weeks"]] = None,
        strike_reason: Optional[str] = None,
    ):
        log_channel = await self._get_log_channel(ctx.guild)
        if not log_channel:
            return await ctx.send("No warn log channel set. Use `/setwarnlog` first.", ephemeral=True)

        if (amount is None) != (unit is None):
            return await ctx.send("Provide both `amount` and `unit`, or neither (for permanent).", ephemeral=True)

        expires_at = _parse_expires_at(amount, unit) if (amount is not None and unit is not None) else None

        async with aiosqlite.connect(DB_PATH) as db:
            now_iso = datetime.now(timezone.utc).isoformat()
            async with db.execute(
                """SELECT COUNT(*) FROM warnings
                   WHERE guild_id = ? AND user_id = ?
                   AND (expires_at IS NULL OR expires_at > ?)""",
                (ctx.guild.id, member.id, now_iso),
            ) as cur:
                count_row = await cur.fetchone()
            old_count = count_row[0] if count_row else 0
            new_count = old_count + 1
            strike_num = _threshold_crossed(old_count, new_count)
            effective_reason = strike_reason or reason if strike_num else None

            cursor = await db.execute(
                """INSERT INTO warnings (guild_id, user_id, reason, issued_by, issued_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ctx.guild.id, member.id, reason, ctx.author.id, now_iso, expires_at),
            )
            warn_id = cursor.lastrowid

            if strike_num:
                await db.execute(
                    """INSERT INTO strikes
                       (guild_id, user_id, strike_number, reason, issued_by, issued_at, triggering_warn_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (ctx.guild.id, member.id, strike_num, effective_reason, ctx.author.id, now_iso, warn_id),
                )
            await db.commit()

        if strike_num:
            embed = self._strike_embed(ctx.guild, member, strike_num, new_count, effective_reason, expires_at, ctx.author)
            label = f"Strike #{strike_num}"
        else:
            embed = self._warn_embed(ctx.guild, member, new_count, reason, expires_at, ctx.author)
            label = f"Warning #{new_count}"

        await self._post_embed(log_channel, embed)
        await ctx.send(f"{label} issued for {member.mention}.", ephemeral=True)

    @commands.hybrid_command(name="warnings", description="[Admin] View a member's warn/strike history")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(member="The member to check")
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT id, reason, issued_at, expires_at FROM warnings
                   WHERE guild_id = ? AND user_id = ? ORDER BY issued_at""",
                (ctx.guild.id, member.id),
            ) as cur:
                warn_rows = await cur.fetchall()
            async with db.execute(
                """SELECT id, strike_number, reason, issued_at FROM strikes
                   WHERE guild_id = ? AND user_id = ? ORDER BY issued_at""",
                (ctx.guild.id, member.id),
            ) as cur:
                strike_rows = await cur.fetchall()

        active_count = sum(
            1 for _, _, _, expires_at in warn_rows
            if expires_at is None or expires_at > now
        )

        embed = discord.Embed(
            title=f"Warn/Strike History — {member.display_name}",
            color=0x3498DB,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Active Warnings", value=str(active_count), inline=True)
        embed.add_field(name="Total Strikes", value=str(len(strike_rows)), inline=True)

        if warn_rows:
            warn_lines = []
            for wid, reason, issued_at, expires_at in warn_rows:
                status = "✅" if (expires_at is None or expires_at > now) else "❌ Expired"
                warn_lines.append(f"`ID {wid}` {status} — {reason[:50]}")
            embed.add_field(name="Warnings", value="\n".join(warn_lines[:10]), inline=False)

        if strike_rows:
            strike_lines = [
                f"`ID {sid}` Strike #{snum} — {reason[:50]}"
                for sid, snum, reason, _ in strike_rows
            ]
            embed.add_field(name="Strikes", value="\n".join(strike_lines), inline=False)

        if not warn_rows and not strike_rows:
            embed.description = "No warnings or strikes on record."

        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="removewarn", description="[Admin] Remove a specific warning by ID")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        member="The member to remove the warning from",
        warn_id="The warning ID — use /warnings to find it",
    )
    async def removewarn(self, ctx: commands.Context, member: discord.Member, warn_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT id FROM warnings WHERE id = ? AND guild_id = ? AND user_id = ?",
                (warn_id, ctx.guild.id, member.id),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return await ctx.send("Warning not found for this member.", ephemeral=True)
            await db.execute("DELETE FROM warnings WHERE id = ?", (warn_id,))
            await db.commit()

        log_channel = await self._get_log_channel(ctx.guild)
        if log_channel:
            embed = self._removal_embed(ctx.guild, member, f"Warning #{warn_id} Removed", ctx.author)
            await self._post_embed(log_channel, embed)

        await ctx.send(
            f"Warning `{warn_id}` removed for {member.mention}.\n"
            "If this warning triggered a strike, remove it separately with `/removestrike`.",
            ephemeral=True,
        )

    @commands.hybrid_command(name="removestrike", description="[Admin] Remove a specific strike by ID")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        member="The member to remove the strike from",
        strike_id="The strike ID — use /warnings to find it",
    )
    async def removestrike(self, ctx: commands.Context, member: discord.Member, strike_id: int):
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """SELECT id, strike_number FROM strikes
                   WHERE id = ? AND guild_id = ? AND user_id = ?""",
                (strike_id, ctx.guild.id, member.id),
            ) as cur:
                row = await cur.fetchone()
            if not row:
                return await ctx.send("Strike not found for this member.", ephemeral=True)
            strike_num = row[1]
            await db.execute("DELETE FROM strikes WHERE id = ?", (strike_id,))
            await db.commit()

        log_channel = await self._get_log_channel(ctx.guild)
        if log_channel:
            embed = self._removal_embed(ctx.guild, member, f"Strike #{strike_num} Removed", ctx.author)
            await self._post_embed(log_channel, embed)

        await ctx.send(f"Strike `{strike_id}` removed for {member.mention}.", ephemeral=True)

    @commands.hybrid_command(name="clearstrikes", description="[Admin] Clear all warnings and strikes for a member")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(member="The member to clear")
    async def clearstrikes(self, ctx: commands.Context, member: discord.Member):
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?",
                (ctx.guild.id, member.id),
            )
            await db.execute(
                "DELETE FROM strikes WHERE guild_id = ? AND user_id = ?",
                (ctx.guild.id, member.id),
            )
            await db.commit()

        log_channel = await self._get_log_channel(ctx.guild)
        if log_channel:
            embed = self._removal_embed(ctx.guild, member, "All Warnings & Strikes Cleared", ctx.author)
            await self._post_embed(log_channel, embed)

        await ctx.send(f"All warnings and strikes cleared for {member.mention}.", ephemeral=True)

    @commands.hybrid_command(name="testwarn", description="[Admin] Post a fake warn embed to verify setup")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def testwarn(self, ctx: commands.Context):
        log_channel = await self._get_log_channel(ctx.guild)
        if not log_channel:
            return await ctx.send("No warn log channel set. Use `/setwarnlog` first.", ephemeral=True)

        embed = self._warn_embed(
            ctx.guild,
            ctx.author,
            warn_num=1,
            reason="TEST — This is a test warning. No DB changes were made.",
            expires_at=None,
            issued_by=ctx.author,
        )
        embed.title = embed.title.replace("Warning", "TEST Warning")
        embed.description = "This is a **test** triggered by staff. No action has been taken."

        await self._post_embed(log_channel, embed)
        await ctx.send(f"✅ Test embed posted to {log_channel.mention}.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Warnings(bot))
