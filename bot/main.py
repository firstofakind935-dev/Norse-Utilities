import asyncio
import os
import sys
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

load_dotenv(Path(__file__).parent.parent / ".env")

COGS = [
    "cogs.music",
    "cogs.economy",
    "cogs.moderation",
    "cogs.welcome",
    "cogs.events",
    "cogs.tickets",
    "cogs.youtube",
    "cogs.flightplan",
    "cogs.applications",
    "cogs.security",
    "cogs.warnings",
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guild_scheduled_events = True


class NORSEBot(commands.Bot):
    async def setup_hook(self):
        from db.database import init_db
        await init_db()
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"  [OK] Loaded: {cog}")
            except Exception as e:
                print(f"  [ERROR] Failed to load {cog}: {e}")
        cmds = self.tree.get_commands()
        print(f"  Commands in tree: {[c.name for c in cmds]}")

    async def on_ready(self):
        print(f"\nLogged in as {self.user} (ID: {self.user.id})")
        print(f"Serving {len(self.guilds)} guild(s)")

        if getattr(self, "_synced", False):
            return
        self._synced = True

        # Sync to each guild for instant slash-command availability
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                print(f"  Synced {len(synced)} commands to: {guild.name}")
            except Exception as e:
                print(f"  [ERROR] Guild sync failed for {guild.name}: {e}")

        # Remove any global commands from Discord to prevent duplicates.
        # Backup the in-memory tree, clear + sync globally (removes from Discord),
        # then restore in memory so copy_global_to keeps working.
        backup = self.tree.get_commands()
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        for cmd in backup:
            self.tree.add_command(cmd)
        print("  Global commands cleared from Discord (guild-only mode)")
        print()

    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have permission to use that command.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"Missing argument: `{error.param.name}`. Check `/help` or `!help`.")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"Invalid argument. Check `/help` or `!help`.")
        elif isinstance(error, commands.CommandNotFound):
            pass
        elif isinstance(error, commands.CommandInvokeError):
            await ctx.send(f"Something went wrong: `{error.original}`")

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ):
        msg = "You don't have permission to use that command." \
            if isinstance(error, app_commands.MissingPermissions) \
            else f"An error occurred: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)


bot = NORSEBot(command_prefix="!", intents=intents)


@bot.hybrid_command(name="ping", description="Check the bot's latency")
async def ping(ctx: commands.Context):
    """Check the bot's latency."""
    await ctx.send(f"Pong! Latency: **{round(bot.latency * 1000)}ms**")


@bot.hybrid_command(name="sync", description="[Admin] Re-sync slash commands to this server")
@commands.has_permissions(administrator=True)
@app_commands.default_permissions(administrator=True)
async def sync(ctx: commands.Context):
    bot.tree.copy_global_to(guild=ctx.guild)
    synced = await bot.tree.sync(guild=ctx.guild)
    # Keep global commands cleared from Discord so there are no duplicates
    backup = bot.tree.get_commands()
    bot.tree.clear_commands(guild=None)
    await bot.tree.sync()
    for cmd in backup:
        bot.tree.add_command(cmd)
    await ctx.send(f"Synced {len(synced)} slash commands to this server.", ephemeral=True)


async def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set in .env")
    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
