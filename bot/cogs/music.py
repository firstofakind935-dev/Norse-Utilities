import asyncio
from pathlib import Path
from typing import Union

import discord
import imageio_ffmpeg
from discord import app_commands
from discord.ext import commands

SOUNDS_DIR = Path(__file__).resolve().parent.parent / "sounds"
FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()

SOUND_FILES = {
    "airport":     SOUNDS_DIR / "airport.mp3",
    "boarding":    SOUNDS_DIR / "boarding.mp3",
    "anywhere":    SOUNDS_DIR / "anywhere.mp3",
    "safety":      SOUNDS_DIR / "safety.mp3",
    "enginestart": SOUNDS_DIR / "enginestart.mp3",
    "enginehum":   SOUNDS_DIR / "enginehum.mp3",
}

FLIGHT_SEQUENCE = [
    ("airport",     "Airport Ambience"),
    ("boarding",    "Norse Air Boarding Music"),
    ("anywhere",    "Norse Air – Anywhere is Possible"),
    ("safety",      "Norse Air Safety Briefing"),
    ("enginestart", "Engine Start"),
    ("enginehum",   "Engine Hum"),
]

FLIGHT_SEQUENCE_PAUSE = [
    ("airport",     "Airport Ambience"),
    ("boarding",    "Norse Air Boarding Music"),
    ("anywhere",    "Norse Air – Anywhere is Possible"),
    ("pause:300",   "Safety Briefing (5-Minute Pause)"),
    ("enginestart", "Engine Start"),
    ("enginehum",   "Engine Hum"),
]

AnyVoiceChannel = Union[discord.VoiceChannel, discord.StageChannel]


def make_source(path: Path):
    return discord.FFmpegOpusAudio(
        str(path),
        executable=FFMPEG_EXE,
        bitrate=128,
        before_options="-nostdin",
        options="-vn -af aresample=48000",
    )


async def connect_to_channel(guild: discord.Guild, channel: AnyVoiceChannel) -> discord.VoiceClient:
    vc = guild.voice_client
    if vc:
        await vc.move_to(channel)
    else:
        vc = await channel.connect()

    if isinstance(channel, discord.StageChannel):
        await guild.me.edit(suppress=False)

    return vc


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._pause_task: asyncio.Task | None = None
        self._delay_count: int = 0

    async def _play_sequence(self, vc: discord.VoiceClient, index: int, channel: discord.TextChannel, sequence: list):
        if not vc.is_connected():
            return
        if index >= len(sequence):
            await channel.send("✈️ Flight sequence complete. Safe travels!")
            await vc.disconnect()
            return

        sound_key, label = sequence[index]

        if sound_key.startswith("pause:"):
            duration = int(sound_key.split(":")[1])
            minutes = duration // 60
            await channel.send(f"⏸️ **{label}** — resuming in {minutes} minute{'s' if minutes != 1 else ''}...")
            self._pause_task = asyncio.ensure_future(asyncio.sleep(duration))
            try:
                await self._pause_task
            except asyncio.CancelledError:
                pass
            finally:
                self._pause_task = None
            await self._play_sequence(vc, index + 1, channel, sequence)
            return

        path = SOUND_FILES.get(sound_key)
        if not path or not path.exists():
            await self._play_sequence(vc, index + 1, channel, sequence)
            return

        source = make_source(path)
        total = len(sequence)
        current = index + 1

        def after(error):
            if error:
                print(f"[Music] Sequence error at {label}: {error}")
            if self._delay_count > 0:
                self._delay_count -= 1
                next_index = index  # replay current track
            else:
                next_index = index + 1
            asyncio.run_coroutine_threadsafe(
                self._play_sequence(vc, next_index, channel, sequence), self.bot.loop
            )

        vc.play(source, after=after)
        await channel.send(f"🎵 Now playing **{label}** `[{current}/{total}]`")

    # ── Flight sequence ──────────────────────────────────────────────────────

    @commands.hybrid_command(name="flight", description="Play the full Norse Air flight sequence with safety briefing")
    @app_commands.describe(channel="The voice or stage channel to play in")
    async def flight(self, ctx: commands.Context, channel: AnyVoiceChannel):
        await ctx.defer()
        vc = ctx.guild.voice_client
        if vc and vc.is_playing():
            return await ctx.send("Already playing. Use `/stop` first.")
        vc = await connect_to_channel(ctx.guild, channel)
        await ctx.send(f"✈️ Starting flight sequence in **{channel.name}**!")
        await self._play_sequence(vc, 0, ctx.channel, FLIGHT_SEQUENCE)

    @commands.hybrid_command(name="flightpause", description="Flight sequence with a 5-minute pause in place of the safety briefing")
    @app_commands.describe(channel="The voice or stage channel to play in")
    async def flightpause(self, ctx: commands.Context, channel: AnyVoiceChannel):
        await ctx.defer()
        vc = ctx.guild.voice_client
        if vc and vc.is_playing():
            return await ctx.send("Already playing. Use `/stop` first.")
        vc = await connect_to_channel(ctx.guild, channel)
        await ctx.send(f"✈️ Starting flight sequence (with pause) in **{channel.name}**!")
        await self._play_sequence(vc, 0, ctx.channel, FLIGHT_SEQUENCE_PAUSE)

    # ── Playback controls ────────────────────────────────────────────────────

    @commands.hybrid_command(name="delay", description="Repeat the current track one more time before moving on")
    async def delay(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if not vc or not vc.is_playing():
            return await ctx.send("Nothing is currently playing.")
        self._delay_count += 1
        await ctx.send(f"⏱️ Current track will repeat **{self._delay_count}** more time{'s' if self._delay_count != 1 else ''} before moving on.")

    @commands.hybrid_command(name="skip", description="Skip the current sound and play the next in the sequence")
    async def skip(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if not vc:
            return await ctx.send("Nothing is playing.")
        if vc.is_playing():
            vc.stop()
            await ctx.send("⏭️ Skipped.")
        elif self._pause_task and not self._pause_task.done():
            self._pause_task.cancel()
            await ctx.send("⏭️ Skipped pause.")
        else:
            await ctx.send("Nothing to skip.")

    @commands.hybrid_command(name="stopsound", description="Stop playback and disconnect")
    async def stopsound(self, ctx: commands.Context):
        await self._stop(ctx)

    @commands.hybrid_command(name="stop", description="Stop playback and disconnect")
    async def stop(self, ctx: commands.Context):
        await self._stop(ctx)

    async def _stop(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc:
            if self._pause_task and not self._pause_task.done():
                self._pause_task.cancel()
            vc.stop()
            await vc.disconnect()
            await ctx.send("Stopped.")
        else:
            await ctx.send("Nothing is playing.")

    # ── Individual sounds ────────────────────────────────────────────────────

    @commands.hybrid_command(name="airportsound", description="Play airport ambience in a voice channel")
    @app_commands.describe(channel="The voice or stage channel to play in")
    async def airportsound(self, ctx: commands.Context, channel: AnyVoiceChannel):
        await self._play_single(ctx, channel, "airport", "Airport Ambience")

    @commands.hybrid_command(name="boarding", description="Play Norse Air boarding music")
    @app_commands.describe(channel="The voice or stage channel to play in")
    async def boarding(self, ctx: commands.Context, channel: AnyVoiceChannel):
        await self._play_single(ctx, channel, "boarding", "Norse Air Boarding Music")

    @commands.hybrid_command(name="anywhere", description="Play Norse Air – Anywhere is Possible")
    @app_commands.describe(channel="The voice or stage channel to play in")
    async def anywhere(self, ctx: commands.Context, channel: AnyVoiceChannel):
        await self._play_single(ctx, channel, "anywhere", "Norse Air – Anywhere is Possible")

    async def _play_single(self, ctx: commands.Context, channel: AnyVoiceChannel, sound_key: str, label: str):
        await ctx.defer()
        vc = ctx.guild.voice_client
        if vc and vc.is_playing():
            return await ctx.send("Already playing.")

        path = SOUND_FILES[sound_key]
        if not path.exists():
            return await ctx.send(f"Audio file `{path.name}` not found. Contact an admin.")

        try:
            vc = await connect_to_channel(ctx.guild, channel)

            def after(error):
                if error:
                    print(f"[Music] Playback error: {error}")
                asyncio.run_coroutine_threadsafe(vc.disconnect(), self.bot.loop)

            vc.play(make_source(path), after=after)
            await ctx.send(f"Playing **{label}** in **{channel.name}**!")
        except Exception as e:
            await ctx.send(f"Playback error: `{e}`")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
