import asyncio
import base64
import os
import re
from collections import deque
from pathlib import Path

import aiohttp
import discord
import imageio_ffmpeg
import yt_dlp
from discord import app_commands
from discord.ext import commands

FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
COOKIES_PATH = "/tmp/yt_cookies.txt"
TEMP_AUDIO_DIR = Path("/tmp/norse_audio")
TEMP_AUDIO_DIR.mkdir(exist_ok=True)

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://piped-api.garudalinux.org",
    "https://api.piped.projectsegfau.lt",
    "https://pipedapi.adminforge.de",
    "https://pipedapi.tokhmi.xyz",
    "https://api.piped.yt",
]

INVIDIOUS_INSTANCES = [
    "https://inv.nadeko.net",
    "https://invidious.private.coffee",
    "https://yt.cdaut.de",
    "https://invidious.perennialte.ch",
    "https://yewtu.be",
    "https://invidious.tiekoetter.com",
    "https://iv.datura.network",
]

# Local-file options — no reconnect flags needed, no headers needed
FFMPEG_LOCAL_OPTS = {
    "executable": FFMPEG_EXE,
    "before_options": "-nostdin",
    "options": "-vn -af aresample=48000",
}


async def _download_audio(url: str, req_headers: dict, video_id: str) -> Path:
    """
    Download audio from url via aiohttp into a temp file and return its path.
    imageio_ffmpeg's bundled binary crashes (SIGSEGV) on HTTP URLs, so we
    download via Python and let FFmpeg read a local file instead.
    """
    fetch_headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        **(req_headers or {}),
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers=fetch_headers,
            timeout=aiohttp.ClientTimeout(total=300),
            allow_redirects=True,
        ) as r:
            if r.status != 200:
                raise RuntimeError(f"Download HTTP {r.status}")
            ct = r.headers.get("Content-Type", "")
            if "webm" in ct or "opus" in ct:
                ext = ".webm"
            elif "mp4" in ct:
                ext = ".mp4"
            elif "mpeg" in ct or "mp3" in ct:
                ext = ".mp3"
            else:
                ext = ".audio"
            path = TEMP_AUDIO_DIR / f"{video_id}{ext}"
            with open(path, "wb") as f:
                async for chunk in r.content.iter_chunked(32768):
                    f.write(chunk)
    size_kb = path.stat().st_size // 1024
    print(f"[YouTube] Downloaded {path.name} ({size_kb} KB)")
    return path


def _setup_cookies() -> str | None:
    env_b64 = os.getenv("YOUTUBE_COOKIES_B64")
    if env_b64:
        try:
            content = base64.b64decode(env_b64).decode("utf-8")
            with open(COOKIES_PATH, "w") as f:
                f.write(content)
            return COOKIES_PATH
        except Exception as e:
            print(f"[YouTube] Failed to decode YOUTUBE_COOKIES_B64: {e}")
    env_cookies = os.getenv("YOUTUBE_COOKIES")
    if env_cookies:
        content = env_cookies.replace("\\n", "\n").replace("\\t", "\t")
        with open(COOKIES_PATH, "w") as f:
            f.write(content)
        return COOKIES_PATH
    local = Path(__file__).resolve().parent.parent.parent / "cookies.txt"
    return str(local) if local.exists() else None


def _extract_video_id(url: str) -> str | None:
    m = re.search(
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})',
        url,
    )
    return m.group(1) if m else None


async def _piped_get(session: aiohttp.ClientSession, path: str) -> dict:
    for base in PIPED_INSTANCES:
        try:
            async with session.get(f"{base}{path}", timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status == 200:
                    return await r.json()
        except Exception:
            continue
    raise RuntimeError("All Piped instances failed")


async def piped_search(query: str) -> dict:
    async with aiohttp.ClientSession() as session:
        data = await _piped_get(session, f"/search?q={aiohttp.helpers.quote(query)}&filter=videos")
    items = [i for i in data.get("items", []) if i.get("type") == "stream"]
    if not items:
        raise ValueError("No results found")
    item = items[0]
    video_id = _extract_video_id(item.get("url", "")) or item.get("url", "").split("=")[-1]
    return {
        "id": video_id,
        "title": item.get("title", "Unknown"),
        "thumbnail": item.get("thumbnail"),
        "duration": item.get("duration"),
        "uploader": item.get("uploaderName"),
        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
    }


async def piped_stream_url(video_id: str) -> tuple[str, dict]:
    """Returns (url, headers) — headers empty since Piped URLs need none."""
    async with aiohttp.ClientSession() as session:
        data = await _piped_get(session, f"/streams/{video_id}")
    streams = data.get("audioStreams", [])
    if not streams:
        raise ValueError("No audio streams from Piped")
    best = sorted(streams, key=lambda x: x.get("bitrate", 0), reverse=True)[0]
    return best["url"], {}


async def cobalt_stream_url(video_id: str) -> tuple[str, dict]:
    """Get audio stream via cobalt.tools — their servers fetch from YouTube."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.cobalt.tools/",
            json={
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "downloadMode": "audio",
                "audioFormat": "best",
                "alwaysProxy": True,
            },
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            text = await r.text()
            if r.status == 429:
                raise RuntimeError("cobalt.tools rate limited")
            if r.status != 200:
                raise RuntimeError(f"cobalt.tools HTTP {r.status}: {text[:200]}")
            try:
                data = __import__("json").loads(text)
            except Exception:
                raise RuntimeError(f"cobalt.tools bad JSON: {text[:200]}")
    status = data.get("status")
    if status in ("redirect", "tunnel", "stream"):
        url = data.get("url")
        if url:
            return url, {}
    if status == "error":
        code = data.get("error", {}).get("code", "unknown")
        raise RuntimeError(f"cobalt.tools: {code}")
    raise RuntimeError(f"cobalt.tools: status={status!r} body={text[:200]}")


async def invidious_stream_url(video_id: str) -> tuple[str, dict]:
    """Get a proxied audio stream URL from an Invidious instance."""
    async with aiohttp.ClientSession() as session:
        for base in INVIDIOUS_INSTANCES:
            try:
                async with session.get(
                    f"{base}/api/v1/videos/{video_id}",
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
                audio = [
                    f for f in data.get("adaptiveFormats", [])
                    if "audio" in f.get("type", "") and "video" not in f.get("type", "")
                ]
                if not audio:
                    continue
                best = sorted(audio, key=lambda f: f.get("bitrate", 0), reverse=True)[0]
                itag = best.get("itag")
                if not itag:
                    continue
                # local=true forces Invidious to proxy through its own server
                proxy_url = f"{base}/latest_version?id={video_id}&itag={itag}&local=true"
                return proxy_url, {}
            except Exception:
                continue
    raise RuntimeError("All Invidious instances failed")


def _ytdlp_stream_info(video_url: str, opts: dict) -> tuple[str, dict]:
    """Extract stream URL + required HTTP headers. Blocking — call in executor."""
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        if "entries" in info:
            info = info["entries"][0]

        base_headers = info.get("http_headers", {})
        formats = info.get("formats", [])

        # Prefer audio-only formats with highest bitrate
        audio_only = [
            f for f in formats
            if f.get("vcodec") in ("none", None)
            and f.get("acodec") not in ("none", None)
            and f.get("url")
        ]
        if audio_only:
            best = sorted(
                audio_only,
                key=lambda f: f.get("abr") or f.get("tbr") or 0,
                reverse=True,
            )[0]
            print(f"[YouTube] yt-dlp selected audio format: {best.get('format_id')} "
                  f"ext={best.get('ext')} abr={best.get('abr')}")
            return best["url"], best.get("http_headers", base_headers)

        # Fall back to whatever yt-dlp selected via the format string
        if info.get("url"):
            return info["url"], base_headers

        if formats and formats[-1].get("url"):
            f = formats[-1]
            return f["url"], f.get("http_headers", base_headers)

    raise ValueError("yt-dlp returned no usable stream URL")


def _ytdlp_search_info(query: str, opts: dict) -> dict:
    with yt_dlp.YoutubeDL({**opts, "skip_download": True}) as ydl:
        info = ydl.extract_info(query, download=False)
        if "entries" in info:
            info = info["entries"][0]
        return info


class QueueEntry:
    def __init__(self, title: str, webpage_url: str, video_id: str,
                 thumbnail: str = None, duration: int = None, uploader: str = None):
        self.title = title
        self.webpage_url = webpage_url
        self.video_id = video_id
        self.thumbnail = thumbnail
        self.duration = duration
        self.uploader = uploader


class GuildPlayer:
    def __init__(self):
        self.queue: deque[QueueEntry] = deque()
        self.current: QueueEntry | None = None
        self.text_channel: discord.TextChannel | None = None
        self.downloading: bool = False
        self.skip_requested: bool = False

    def stop(self):
        """Clear all state so _advance exits cleanly."""
        self.queue.clear()
        self.current = None
        self.downloading = False
        self.skip_requested = True


class YouTube(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}
        cookies_path = _setup_cookies()

        self.ytdl_meta_opts = {
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "default_search": "ytsearch",
            "skip_download": True,
            "extract_flat": True,
        }
        # android/ios/tv_embedded clients bypass PO token requirement
        self.ytdl_stream_opts = {
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "format": "bestaudio/best",
            "extractor_args": {"youtube": {"player_client": ["android", "ios", "tv_embedded"]}},
        }
        if cookies_path:
            self.ytdl_meta_opts["cookiefile"] = cookies_path
            self.ytdl_stream_opts["cookiefile"] = cookies_path

        status = f"loaded from {cookies_path}" if cookies_path else "not found"
        print(f"[YouTube] Cookies: {status}")

    def get_player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer()
        return self.players[guild_id]

    async def _resolve_stream(self, entry: QueueEntry) -> tuple[str, dict]:
        """Returns (stream_url, headers). Tries multiple strategies in order."""
        loop = asyncio.get_running_loop()
        errors: list[str] = []
        video_url = f"https://www.youtube.com/watch?v={entry.video_id}"

        # Attempt 1: android/ios/tv_embedded clients — bypass PO token
        try:
            url, headers = await loop.run_in_executor(
                None, _ytdlp_stream_info, video_url, self.ytdl_stream_opts,
            )
            print(f"[YouTube] yt-dlp android OK: '{entry.title}' headers={list(headers.keys())}")
            return url, headers
        except Exception as e:
            errors.append(f"yt-dlp(android): {e}")
            print(f"[YouTube] yt-dlp android failed: {e}")

        # Attempt 2: format 18/17 — progressive MP4, no DASH, works from any IP
        try:
            opts_prog = {**self.ytdl_stream_opts, "format": "18/17"}
            url, headers = await loop.run_in_executor(
                None, _ytdlp_stream_info, video_url, opts_prog,
            )
            print(f"[YouTube] yt-dlp progressive OK: '{entry.title}'")
            return url, headers
        except Exception as e:
            errors.append(f"yt-dlp(fmt18): {e}")
            print(f"[YouTube] yt-dlp progressive failed: {e}")

        # Attempt 3: cobalt.tools — proxied through their servers, bypasses IP block
        try:
            result = await cobalt_stream_url(entry.video_id)
            print(f"[YouTube] cobalt.tools OK: '{entry.title}'")
            return result
        except Exception as e:
            errors.append(f"cobalt: {e}")
            print(f"[YouTube] cobalt.tools failed: {e}")

        # Attempt 4: Invidious proxy
        try:
            result = await invidious_stream_url(entry.video_id)
            print(f"[YouTube] Invidious OK: '{entry.title}'")
            return result
        except Exception as e:
            errors.append(f"invidious: {e}")
            print(f"[YouTube] Invidious failed: {e}")

        # Attempt 5: Piped API
        try:
            result = await piped_stream_url(entry.video_id)
            print(f"[YouTube] Piped OK: '{entry.title}'")
            return result
        except Exception as e:
            errors.append(f"Piped: {e}")
            print(f"[YouTube] Piped failed: {e}")

        raise RuntimeError(" | ".join(errors))

    async def _advance(self, guild_id: int):
        player = self.get_player(guild_id)
        guild = self.bot.get_guild(guild_id)
        vc = guild.voice_client if guild else None

        if not vc:
            return

        if not player.queue:
            player.current = None
            for _ in range(60):
                await asyncio.sleep(5)
                if player.queue or vc.is_playing() or vc.is_paused():
                    return
            if vc.is_connected() and not vc.is_playing():
                await vc.disconnect()
            return

        entry = player.queue.popleft()
        player.current = entry

        try:
            stream_url, headers = await self._resolve_stream(entry)
        except Exception as e:
            print(f"[YouTube] All stream methods failed for '{entry.title}': {e}")
            if player.text_channel:
                await player.text_channel.send(f"⚠️ Could not stream **{entry.title}**: `{e}`")
            await self._advance(guild_id)
            return

        # imageio_ffmpeg's bundled binary crashes (SIGSEGV) on HTTP URLs.
        # Download via Python first, then play from local file.
        if player.text_channel:
            await player.text_channel.send(f"⬇️ Downloading **{entry.title}**...")
        player.downloading = True
        player.skip_requested = False
        try:
            local_path = await _download_audio(stream_url, headers, entry.video_id)
        except Exception as e:
            player.downloading = False
            print(f"[YouTube] Download failed for '{entry.title}': {e}")
            if player.text_channel:
                await player.text_channel.send(f"⚠️ Download failed for **{entry.title}**: `{e}`")
            await self._advance(guild_id)
            return
        finally:
            player.downloading = False

        # Abort if the bot was disconnected or a skip/stop was requested during download
        if player.skip_requested or not vc.is_connected():
            local_path.unlink(missing_ok=True)
            player.skip_requested = False
            return

        source = discord.FFmpegOpusAudio(str(local_path), **FFMPEG_LOCAL_OPTS)

        def after(error):
            try:
                local_path.unlink(missing_ok=True)
            except Exception:
                pass
            if error:
                print(f"[YouTube] Playback error for '{entry.title}': {error}")
                if player.text_channel:
                    asyncio.run_coroutine_threadsafe(
                        player.text_channel.send(f"❌ FFmpeg error: `{error}`"),
                        self.bot.loop,
                    )
            asyncio.run_coroutine_threadsafe(self._advance(guild_id), self.bot.loop)

        try:
            vc.play(source, after=after)
        except Exception as e:
            local_path.unlink(missing_ok=True)
            print(f"[YouTube] vc.play failed: {e}")
            asyncio.ensure_future(self._advance(guild_id))
            return

        if player.text_channel:
            await player.text_channel.send(f"🎵 Now playing: **{entry.title}**")

    @commands.hybrid_command(name="ytcheck", description="Check YouTube cookie/Piped status")
    @commands.has_permissions(administrator=True)
    async def ytcheck(self, ctx: commands.Context):
        cookie_file = self.ytdl_stream_opts.get("cookiefile")
        cookie_status = (
            f"✅ `{cookie_file}` ({Path(cookie_file).stat().st_size} bytes)"
            if cookie_file and Path(cookie_file).exists()
            else "❌ Not loaded"
        )
        try:
            async with aiohttp.ClientSession() as session:
                await _piped_get(session, "/search?q=test&filter=videos")
            piped_status = "✅ Reachable"
        except Exception as e:
            piped_status = f"❌ {e}"
        await ctx.send(
            f"**Cookies:** {cookie_status}\n**Piped API:** {piped_status}",
            ephemeral=True,
        )

    @commands.hybrid_command(name="play", description="Play a song from YouTube by URL or search query")
    @app_commands.describe(query="YouTube URL or search terms")
    async def play(self, ctx: commands.Context, *, query: str):
        if not ctx.author.voice:
            return await ctx.send("You need to be in a voice channel.")

        await ctx.defer()

        vc = ctx.guild.voice_client
        if vc and vc.channel != ctx.author.voice.channel:
            await vc.move_to(ctx.author.voice.channel)
        elif not vc:
            vc = await ctx.author.voice.channel.connect()

        video_id = _extract_video_id(query)

        try:
            if video_id:
                try:
                    async with aiohttp.ClientSession() as session:
                        data = await _piped_get(session, f"/streams/{video_id}")
                    info = {
                        "id": video_id,
                        "title": data.get("title", "Unknown"),
                        "thumbnail": data.get("thumbnailUrl"),
                        "duration": data.get("duration"),
                        "uploader": data.get("uploader"),
                        "webpage_url": f"https://www.youtube.com/watch?v={video_id}",
                    }
                except Exception:
                    loop = asyncio.get_running_loop()
                    raw = await loop.run_in_executor(
                        None, _ytdlp_search_info,
                        f"https://www.youtube.com/watch?v={video_id}",
                        self.ytdl_meta_opts,
                    )
                    info = {
                        "id": video_id,
                        "title": raw.get("title", "Unknown"),
                        "thumbnail": raw.get("thumbnail"),
                        "duration": raw.get("duration"),
                        "uploader": raw.get("uploader"),
                        "webpage_url": raw.get("webpage_url", f"https://www.youtube.com/watch?v={video_id}"),
                    }
            else:
                try:
                    info = await piped_search(query)
                except Exception:
                    loop = asyncio.get_running_loop()
                    raw = await loop.run_in_executor(
                        None, _ytdlp_search_info,
                        f"ytsearch:{query}",
                        self.ytdl_meta_opts,
                    )
                    vid = _extract_video_id(raw.get("webpage_url", "")) or raw.get("id", "")
                    info = {
                        "id": vid,
                        "title": raw.get("title", "Unknown"),
                        "thumbnail": raw.get("thumbnail"),
                        "duration": raw.get("duration"),
                        "uploader": raw.get("uploader"),
                        "webpage_url": raw.get("webpage_url", f"https://www.youtube.com/watch?v={vid}"),
                    }
        except Exception as e:
            return await ctx.send(f"Could not find that song: `{e}`")

        entry = QueueEntry(
            title=info.get("title", "Unknown"),
            webpage_url=info.get("webpage_url", ""),
            video_id=info.get("id", ""),
            thumbnail=info.get("thumbnail") or info.get("thumbnailUrl"),
            duration=info.get("duration"),
            uploader=info.get("uploader") or info.get("uploaderName"),
        )

        player = self.get_player(ctx.guild.id)
        player.text_channel = ctx.channel
        player.queue.append(entry)

        if not vc.is_playing() and not vc.is_paused():
            await ctx.send(f"⏳ Loading **{entry.title}**...")
            await self._advance(ctx.guild.id)
        else:
            await ctx.send(f"📋 Added to queue: **{entry.title}** (position {len(player.queue)})")

    @commands.hybrid_command(name="next", description="Skip to the next song in the queue")
    async def next(self, ctx: commands.Context):
        player = self.get_player(ctx.guild.id)
        vc = ctx.guild.voice_client
        if not vc:
            return await ctx.send("Nothing is playing.")
        if player.downloading:
            player.skip_requested = True
            await ctx.send("⏭️ Skipping current download...")
            return
        if not vc.is_playing() and not vc.is_paused():
            return await ctx.send("Nothing is playing.")
        vc.stop()
        await ctx.send("⏭️ Skipped.")

    @commands.hybrid_command(name="ytstop", description="Stop YouTube music and disconnect")
    async def ytstop(self, ctx: commands.Context):
        player = self.get_player(ctx.guild.id)
        player.stop()
        vc = ctx.guild.voice_client
        if vc:
            vc.stop()
            await vc.disconnect()
            await ctx.send("⏹️ Stopped.")
        else:
            await ctx.send("Not connected.")

    @commands.hybrid_command(name="queue", description="Show the current music queue")
    async def queue(self, ctx: commands.Context):
        player = self.get_player(ctx.guild.id)
        if not player.current and not player.queue:
            return await ctx.send("The queue is empty.")
        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color(0x0B1F3A))
        if player.current:
            embed.add_field(
                name="Now Playing",
                value=f"[{player.current.title}]({player.current.webpage_url})",
                inline=False,
            )
        if player.queue:
            lines = [f"`{i}.` {e.title}" for i, e in enumerate(list(player.queue)[:10], 1)]
            if len(player.queue) > 10:
                lines.append(f"...and {len(player.queue) - 10} more")
            embed.add_field(name="Up Next", value="\n".join(lines), inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="nowplaying", description="Show what's currently playing")
    async def nowplaying(self, ctx: commands.Context):
        player = self.get_player(ctx.guild.id)
        if not player.current:
            return await ctx.send("Nothing is playing.")
        entry = player.current
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"**[{entry.title}]({entry.webpage_url})**",
            color=discord.Color(0x0B1F3A),
        )
        if entry.thumbnail:
            embed.set_thumbnail(url=entry.thumbnail)
        if entry.duration:
            mins, secs = divmod(entry.duration, 60)
            embed.add_field(name="Duration", value=f"{mins}:{secs:02d}")
        if entry.uploader:
            embed.add_field(name="Channel", value=entry.uploader)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="pause", description="Pause the current song")
    async def pause(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await ctx.send("⏸️ Paused.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.hybrid_command(name="resume", description="Resume the paused song")
    async def resume(self, ctx: commands.Context):
        vc = ctx.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await ctx.send("▶️ Resumed.")
        else:
            await ctx.send("Nothing is paused.")

    @commands.hybrid_command(name="yttest", description="Test audio download + playback pipeline")
    @commands.has_permissions(administrator=True)
    async def yttest(self, ctx: commands.Context):
        if not ctx.author.voice:
            return await ctx.send("Join a voice channel first.")
        await ctx.defer()
        vc = ctx.guild.voice_client
        if not vc:
            vc = await ctx.author.voice.channel.connect()
        test_url = "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3"
        await ctx.send("⬇️ Downloading test audio...")
        try:
            path = await _download_audio(test_url, {}, "test")
        except Exception as e:
            return await ctx.send(f"❌ Download failed: `{e}`")
        source = discord.FFmpegOpusAudio(str(path), **FFMPEG_LOCAL_OPTS)

        def after(error):
            path.unlink(missing_ok=True)
            msg = "✅ Test OK — audio pipeline works!" if not error else f"❌ FFmpeg error: `{error}`"
            asyncio.run_coroutine_threadsafe(ctx.channel.send(msg), self.bot.loop)
            asyncio.run_coroutine_threadsafe(vc.disconnect(), self.bot.loop)

        vc.play(source, after=after)
        await ctx.send("🔊 Playing test audio — you should hear music now.")


async def setup(bot: commands.Bot):
    await bot.add_cog(YouTube(bot))
