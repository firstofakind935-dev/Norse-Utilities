from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands


FORMATS = [
    "%d/%m/%Y %H:%M",
    "%d/%m/%y %H:%M",
    "%d/%m %H:%M",
    "%d-%m-%Y %H:%M",
    "%Y-%m-%d %H:%M",
]


def parse_when(when: str) -> datetime:
    """Try multiple date/time formats. Assumes current year if year is omitted."""
    when = when.strip()
    for fmt in FORMATS:
        try:
            dt = datetime.strptime(when, fmt)
            if dt.year == 1900:
                dt = dt.replace(year=datetime.now(timezone.utc).year)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Could not parse: {when}")


class Events(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="createevent", description="Create a new scheduled event in this server")
    @app_commands.describe(
        name="Event name",
        when='Date and time in UTC — e.g. "25/12 20:00" or "25/12/2026 20:00"',
        duration="Duration in minutes (default: 60)",
    )
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def createevent(
        self,
        ctx: commands.Context,
        name: str,
        when: str,
        duration: int = 60,
    ):
        await ctx.defer()

        try:
            start = parse_when(when)
        except ValueError:
            return await ctx.send(
                'Invalid date/time. Examples: `25/12 20:00` · `25/12/2026 20:00` · `2026-12-25 20:00`'
            )

        if start < datetime.now(timezone.utc):
            return await ctx.send("Start time must be in the future.")

        end = start + timedelta(minutes=duration)

        try:
            data = await ctx.bot.http.create_guild_scheduled_event(
                ctx.guild.id,
                name=name,
                privacy_level=2,
                scheduled_start_time=start.isoformat(),
                scheduled_end_time=end.isoformat(),
                entity_type=3,
                entity_metadata={"location": "TBD"},
            )
        except discord.Forbidden:
            return await ctx.send("I don't have permission to create events.")
        except Exception as e:
            return await ctx.send(f"Failed to create event: `{e}`")

        embed = discord.Embed(
            title="✅ Event Created",
            description=f"**{data['name']}**",
            color=discord.Color(0x0B1F3A),
        )
        embed.add_field(name="Start", value=f"<t:{int(start.timestamp())}:F>", inline=True)
        embed.add_field(name="Duration", value=f"{duration} min", inline=True)

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="events", description="List all upcoming scheduled events in this server")
    async def events(self, ctx: commands.Context):
        await ctx.defer()

        scheduled = await ctx.guild.fetch_scheduled_events()
        upcoming = sorted(
            [e for e in scheduled if e.start_time > datetime.now(timezone.utc)],
            key=lambda e: e.start_time,
        )

        if not upcoming:
            return await ctx.send("No upcoming events scheduled.")

        embed = discord.Embed(
            title=f"📅 Upcoming Events — {ctx.guild.name}",
            color=discord.Color(0x0B1F3A),
        )

        for event in upcoming[:10]:
            location = ""
            if event.channel:
                location = f" • {event.channel.name}"
            elif event.location:
                location = f" • {event.location}"

            interested = event.user_count or 0
            embed.add_field(
                name=event.name,
                value=(
                    f"<t:{int(event.start_time.timestamp())}:F>\n"
                    f"{event.description or ''}"
                    f"{location}\n"
                    f"👥 {interested} interested"
                ).strip(),
                inline=False,
            )

        if len(upcoming) > 10:
            embed.set_footer(text=f"Showing 10 of {len(upcoming)} events")

        await ctx.send(embed=embed)

    @commands.hybrid_command(name="cancelevent", description="Cancel a scheduled event by name")
    @app_commands.describe(name="The name of the event to cancel (case-insensitive)")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def cancelevent(self, ctx: commands.Context, *, name: str):
        await ctx.defer()

        scheduled = await ctx.guild.fetch_scheduled_events()
        matches = [e for e in scheduled if e.name.lower() == name.lower()]

        if not matches:
            close = [e for e in scheduled if name.lower() in e.name.lower()]
            if close:
                names = "\n".join(f"• {e.name}" for e in close[:5])
                return await ctx.send(f"No exact match found. Did you mean:\n{names}")
            return await ctx.send(f"No event named **{name}** found.")

        event = matches[0]
        await event.delete()
        await ctx.send(f"🗑️ Event **{event.name}** has been cancelled.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Events(bot))
