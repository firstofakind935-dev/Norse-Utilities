import discord
from discord import app_commands
from discord.ext import commands


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="promote", description="Promote a member by assigning them a role")
    @app_commands.describe(member="The member to promote", role="The role to assign")
    @commands.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    async def promote(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """Promote with !promote @user Role or /promote."""
        if role >= ctx.guild.me.top_role:
            return await ctx.send("I can't assign a role at or above my highest role.", ephemeral=True)
        if role in member.roles:
            return await ctx.send(f"{member.mention} already has **{role.name}**.", ephemeral=True)
        await member.add_roles(role, reason=f"Promoted by {ctx.author}")
        embed = discord.Embed(
            title="Promotion",
            description=f"{member.mention} has been promoted to **{role.name}**!",
            color=discord.Color.green(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Promoted by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="demote", description="Demote a member by removing a role from them")
    @app_commands.describe(member="The member to demote", role="The role to remove")
    @commands.has_permissions(manage_roles=True)
    @app_commands.default_permissions(manage_roles=True)
    async def demote(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """Demote with !demote @user Role or /demote."""
        if role >= ctx.guild.me.top_role:
            return await ctx.send("I can't remove a role at or above my highest role.", ephemeral=True)
        if role not in member.roles:
            return await ctx.send(f"{member.mention} doesn't have **{role.name}**.", ephemeral=True)
        await member.remove_roles(role, reason=f"Demoted by {ctx.author}")
        embed = discord.Embed(
            title="Demotion",
            description=f"{member.mention} has been demoted from **{role.name}**.",
            color=discord.Color.red(),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Demoted by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="roles", description="List all assignable server roles")
    async def roles(self, ctx: commands.Context):
        """List roles with !roles or /roles."""
        assignable = [
            r for r in reversed(ctx.guild.roles)
            if r.name != "@everyone" and r < ctx.guild.me.top_role
        ]
        if not assignable:
            return await ctx.send("No assignable roles found.")
        embed = discord.Embed(title="Server Roles", color=discord.Color.blurple())
        lines = [f"{r.mention} (`{r.id}`)" for r in assignable[:25]]
        embed.description = "\n".join(lines)
        if len(assignable) > 25:
            embed.set_footer(text=f"... and {len(assignable) - 25} more")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
