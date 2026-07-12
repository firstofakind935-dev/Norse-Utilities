from datetime import date

import discord
from discord import app_commands
from discord.ext import commands

from db.database import (
    add_balance,
    get_balance,
    get_last_daily,
    get_leaderboard,
    set_balance,
    set_last_daily,
)

CURRENCY = "🪙"
DAILY_AMOUNT = 100


class Economy(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="balance", description="Check your or another member's coin balance")
    @app_commands.describe(member="The member to check (leave empty for yourself)")
    async def balance(self, ctx: commands.Context, member: discord.Member = None):
        """Check a balance with !balance or /balance."""
        target = member or ctx.author
        bal = await get_balance(target.id, ctx.guild.id)
        embed = discord.Embed(title=f"{target.display_name}'s Balance", color=discord.Color.gold())
        embed.add_field(name="Balance", value=f"{CURRENCY} **{bal:,}**")
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="daily", description="Claim your daily coin reward")
    async def daily(self, ctx: commands.Context):
        """Claim your daily reward with !daily or /daily."""
        today = str(date.today())
        last = await get_last_daily(ctx.author.id, ctx.guild.id)
        if last == today:
            return await ctx.send("You already claimed your daily reward today. Come back tomorrow!", ephemeral=True)
        new_bal = await add_balance(ctx.author.id, ctx.guild.id, DAILY_AMOUNT)
        await set_last_daily(ctx.author.id, ctx.guild.id, today)
        await ctx.send(
            f"{ctx.author.mention} claimed their daily {CURRENCY} **{DAILY_AMOUNT}**! "
            f"New balance: {CURRENCY} **{new_bal:,}**"
        )

    @commands.hybrid_command(name="transfer", description="Transfer coins to another member")
    @app_commands.describe(member="Who to send coins to", amount="How many coins to send")
    async def transfer(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Transfer coins with !transfer @user 100 or /transfer."""
        if member == ctx.author:
            return await ctx.send("You can't transfer to yourself.", ephemeral=True)
        if amount <= 0:
            return await ctx.send("Amount must be positive.", ephemeral=True)
        sender_bal = await get_balance(ctx.author.id, ctx.guild.id)
        if sender_bal < amount:
            return await ctx.send(
                f"Insufficient funds. Your balance: {CURRENCY} **{sender_bal:,}**", ephemeral=True
            )
        await add_balance(ctx.author.id, ctx.guild.id, -amount)
        await add_balance(member.id, ctx.guild.id, amount)
        await ctx.send(f"{ctx.author.mention} transferred {CURRENCY} **{amount:,}** to {member.mention}.")

    @commands.hybrid_command(name="leaderboard", description="Show the top 10 richest members")
    async def leaderboard(self, ctx: commands.Context):
        """Show the leaderboard with !leaderboard or /leaderboard."""
        rows = await get_leaderboard(ctx.guild.id)
        if not rows:
            return await ctx.send("No balances recorded yet. Use `/daily` to get started!")
        embed = discord.Embed(title="💰 Leaderboard", color=discord.Color.gold())
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        lines = []
        for i, (user_id, bal) in enumerate(rows, start=1):
            m = ctx.guild.get_member(user_id)
            name = m.display_name if m else f"Unknown ({user_id})"
            lines.append(f"{medals.get(i, f'`{i}.`')} **{name}** — {CURRENCY} {bal:,}")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="addmoney", description="[Admin] Add coins to a member")
    @app_commands.describe(member="The member to add coins to", amount="How many coins to add")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def addmoney(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Add coins with !addmoney @user 100 or /addmoney."""
        if amount <= 0:
            return await ctx.send("Amount must be positive.", ephemeral=True)
        new_bal = await add_balance(member.id, ctx.guild.id, amount)
        await ctx.send(f"Added {CURRENCY} **{amount:,}** to {member.mention}. New balance: {CURRENCY} **{new_bal:,}**")

    @commands.hybrid_command(name="removemoney", description="[Admin] Remove coins from a member")
    @app_commands.describe(member="The member to remove coins from", amount="How many coins to remove")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def removemoney(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Remove coins with !removemoney @user 100 or /removemoney."""
        if amount <= 0:
            return await ctx.send("Amount must be positive.", ephemeral=True)
        new_bal = await add_balance(member.id, ctx.guild.id, -amount)
        await ctx.send(f"Removed {CURRENCY} **{amount:,}** from {member.mention}. New balance: {CURRENCY} **{new_bal:,}**")

    @commands.hybrid_command(name="setbalance", description="[Admin] Set a member's balance to an exact amount")
    @app_commands.describe(member="The member to set balance for", amount="The exact amount to set")
    @commands.has_permissions(administrator=True)
    @app_commands.default_permissions(administrator=True)
    async def setbalance(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Set balance with !setbalance @user 500 or /setbalance."""
        if amount < 0:
            return await ctx.send("Balance cannot be negative.", ephemeral=True)
        await set_balance(member.id, ctx.guild.id, amount)
        await ctx.send(f"Set {member.mention}'s balance to {CURRENCY} **{amount:,}**.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
