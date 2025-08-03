#
# Copyright (C) 2024-2025 by TheTeamVivek@Github, < https://github.com/TheTeamVivek >.
#
# This file is part of < https://github.com/TheTeamVivek/YukkiMusic > project,
# and is released under the MIT License.
# Please see < https://github.com/TheTeamVivek/YukkiMusic/blob/master/LICENSE >
#
# All rights reserved.
#
import asyncio
import os
import re
from typing import Dict, List, Optional, Tuple

import aiohttp
from async_lru import alru_cache
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from yt_dlp import YoutubeDL

import config
from config import cookies
from YukkiMusic.utils.database import is_on_off
from YukkiMusic.utils.decorators import asyncify
from YukkiMusic.utils.formatters import seconds_to_min, time_to_seconds

# Add these to your config.py
YOUTUBE_API_KEY = config.YOUTUBE_API_KEY  # Get from Google Cloud Console
YOUTUBE_API_BASE_URL = "https://www.googleapis.com/youtube/v3"

NOTHING = {"cookies_dead": None}


async def shell_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, errorz = await proc.communicate()
    if errorz:
        if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower():
            return out.decode("utf-8")
        else:
            return errorz.decode("utf-8")
    return out.decode("utf-8")


class YouTubeAPI:
    def __init__(self):
        self.api_key = YOUTUBE_API_KEY
        self.base_url = YOUTUBE_API_BASE_URL
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    async def _make_api_request(self, endpoint: str, params: Dict) -> Optional[Dict]:
        """Make a request to YouTube API v3"""
        params["key"] = self.api_key
        url = f"{self.base_url}/{endpoint}"
        
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        return await response.json()
                    else:
                        print(f"API Error: {response.status}")
                        return None
            except Exception as e:
                print(f"Request failed: {e}")
                return None

    def extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from YouTube URL"""
        patterns = [
            r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
            r'(?:embed\/)([0-9A-Za-z_-]{11})',
            r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def extract_playlist_id(self, url: str) -> Optional[str]:
        """Extract playlist ID from YouTube URL"""
        match = re.search(r'list=([a-zA-Z0-9_-]+)', url)
        return match.group(1) if match else None

    async def exists(self, link: str, videoid: bool | str = None):
        if videoid:
            video_id = link
        else:
            video_id = self.extract_video_id(link)
            
        if not video_id:
            return False
            
        # Check if video exists using API
        params = {
            "part": "id",
            "id": video_id
        }
        
        result = await self._make_api_request("videos", params)
        return result and len(result.get("items", [])) > 0

    @property
    def use_fallback(self):
        return NOTHING["cookies_dead"] is True

    @use_fallback.setter
    def use_fallback(self, value):
        if NOTHING["cookies_dead"] is None:
            NOTHING["cookies_dead"] = value

    @asyncify
    def url(self, message_1: Message) -> str | None:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        text = ""
        offset = None
        length = None
        for message in messages:
            if offset:
                break
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        offset, length = entity.offset, entity.length
                        break
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        if offset in (None,):
            return None
        return text[offset : offset + length]

    @alru_cache(maxsize=None)
    async def details(self, link: str, videoid: bool | str = None):
        if videoid:
            video_id = link
        else:
            video_id = self.extract_video_id(link)
            
        if not video_id:
            return None
            
        params = {
            "part": "snippet,contentDetails",
            "id": video_id
        }
        
        result = await self._make_api_request("videos", params)
        
        if not result or not result.get("items"):
            return None
            
        video = result["items"][0]
        snippet = video["snippet"]
        content_details = video["contentDetails"]
        
        title = snippet["title"]
        duration_iso = content_details["duration"]
        # Convert ISO 8601 duration to seconds
        duration_sec = self._parse_duration(duration_iso)
        duration_min = seconds_to_min(duration_sec) if duration_sec else "0:00"
        
        # Get highest quality thumbnail
        thumbnails = snippet["thumbnails"]
        thumbnail = (thumbnails.get("maxres") or 
                    thumbnails.get("high") or 
                    thumbnails.get("medium") or 
                    thumbnails.get("default", {})).get("url", "")
        
        return title, duration_min, duration_sec, thumbnail, video_id

    def _parse_duration(self, duration: str) -> int:
        """Parse ISO 8601 duration to seconds"""
        import re
        
        pattern = r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?'
        match = re.match(pattern, duration)
        
        if not match:
            return 0
            
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2) or 0)
        seconds = int(match.group(3) or 0)
        
        return hours * 3600 + minutes * 60 + seconds

    @alru_cache(maxsize=None)
    async def title(self, link: str, videoid: bool | str = None):
        details = await self.details(link, videoid)
        return details[0] if details else None

    @alru_cache(maxsize=None)
    async def duration(self, link: str, videoid: bool | str = None):
        details = await self.details(link, videoid)
        return details[1] if details else None

    @alru_cache(maxsize=None)
    async def thumbnail(self, link: str, videoid: bool | str = None):
        details = await self.details(link, videoid)
        return details[3] if details else None

    async def search_videos(self, query: str, max_results: int = 10) -> List[Dict]:
        """Search for videos using YouTube API"""
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "order": "relevance"
        }
        
        result = await self._make_api_request("search", params)
        
        if not result or not result.get("items"):
            return []
            
        videos = []
        for item in result["items"]:
            video_id = item["id"]["videoId"]
            snippet = item["snippet"]
            
            # Get video details for duration
            video_details = await self.details(video_id, videoid=True)
            
            videos.append({
                "id": video_id,
                "title": snippet["title"],
                "link": f"https://www.youtube.com/watch?v={video_id}",
                "thumbnail": snippet["thumbnails"]["high"]["url"],
                "duration": video_details[1] if video_details else "0:00",
                "channel": snippet["channelTitle"]
            })
            
        return videos

    # Keep the existing video download methods as they still use yt-dlp
    async def video(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        cmd = [
            "yt-dlp",
            f"--cookies",
            cookies(),
            "-g",
            "-f",
            "best[height<=?720][width<=?1280]",
            f"{link}",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        else:
            return 0, stderr.decode()

    @alru_cache(maxsize=None)
    async def playlist(self, link, limit, videoid: bool | str = None):
        if videoid:
            playlist_id = link
        else:
            playlist_id = self.extract_playlist_id(link)
            
        if not playlist_id:
            return []
            
        params = {
            "part": "snippet",
            "playlistId": playlist_id,
            "maxResults": min(limit, 50)  # API limit is 50
        }
        
        result = await self._make_api_request("playlistItems", params)
        
        if not result or not result.get("items"):
            return []
            
        video_ids = []
        for item in result["items"]:
            video_id = item["snippet"]["resourceId"]["videoId"]
            video_ids.append(video_id)
            
        return video_ids

    @alru_cache(maxsize=None)
    async def track(self, link: str, videoid: bool | str = None):
        if videoid:
            video_id = link
            link = self.base + link
        else:
            video_id = self.extract_video_id(link)
            
        if not video_id:
            # If no video ID found, search for the query
            search_results = await self.search_videos(link, 1)
            if search_results:
                video = search_results[0]
                track_details = {
                    "title": video["title"],
                    "link": video["link"],
                    "vidid": video["id"],
                    "duration_min": video["duration"],
                    "thumb": video["thumbnail"],
                }
                return track_details, video["id"]
            else:
                return await self._track(link)
        
        # Get video details using API
        details = await self.details(video_id, videoid=True)
        if details:
            title, duration_min, duration_sec, thumbnail, vidid = details
            track_details = {
                "title": title,
                "link": link,
                "vidid": vidid,
                "duration_min": duration_min,
                "thumb": thumbnail,
            }
            return track_details, vidid
        else:
            return await self._track(link)

    @asyncify
    def _track(self, q):
        # Fallback to yt-dlp for search
        options = {
            "format": "best",
            "noplaylist": True,
            "quiet": True,
            "extract_flat": "in_playlist",
            "cookiefile": f"{cookies()}",
        }
        with YoutubeDL(options) as ydl:
            info_dict = ydl.extract_info(f"ytsearch: {q}", download=False)
            details = info_dict.get("entries")[0]
            info = {
                "title": details["title"],
                "link": details["url"],
                "vidid": details["id"],
                "duration_min": (
                    seconds_to_min(details["duration"])
                    if details["duration"] != 0
                    else None
                ),
                "thumb": details["thumbnails"][0]["url"],
            }
            return info, details["id"]

    # Keep all the existing download methods as they use yt-dlp
    @alru_cache(maxsize=None)
    @asyncify
    def formats(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]

        ytdl_opts = {
            "quiet": True,
            "cookiefile": f"{cookies()}",
        }

        ydl = YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
                try:
                    str(format["format"])
                except Exception:
                    continue
                if "dash" not in str(format["format"]).lower():
                    try:
                        format["format"]
                        format["filesize"]
                        format["format_id"]
                        format["ext"]
                        format["format_note"]
                    except KeyError:
                        continue
                    formats_available.append(
                        {
                            "format": format["format"],
                            "filesize": format["filesize"],
                            "format_id": format["format_id"],
                            "ext": format["ext"],
                            "format_note": format["format_note"],
                            "yturl": link,
                        }
                    )
        return formats_available, link

    @alru_cache(maxsize=None)
    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: bool | str = None,
    ):
        # Use API search instead of VideosSearch
        search_results = await self.search_videos(link, 10)
        
        if query_type < len(search_results):
            video = search_results[query_type]
            return video["title"], video["duration"], video["thumbnail"], video["id"]
        
        return None, None, None, None

    async def download(
        self,
        link: str,
        mystic,
        video: bool | str = None,
        videoid: bool | str = None,
        songaudio: bool | str = None,
        songvideo: bool | str = None,
        format_id: bool | str = None,
        title: bool | str = None,
    ) -> str:
        # Keep the existing download implementation as it uses yt-dlp
        if videoid:
            link = self.base + link

        @asyncify
        def audio_dl():
            ydl_optssx = {
                "format": "bestaudio[ext=m4a]/bestaudio/best",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile": f"{cookies()}",
                "prefer_ffmpeg": True,
            }

            with YoutubeDL(ydl_optssx) as x:
                info = x.extract_info(link, False)
                xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
                if os.path.exists(xyz):
                    return xyz
                x.download([link])
                return xyz

        @asyncify
        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "cookiefile": f"{cookies()}",
            }

            with YoutubeDL(ydl_optssx) as x:
                info = x.extract_info(link, False)
                xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
                if os.path.exists(xyz):
                    return xyz
                x.download([link])
                return xyz

        @asyncify
        def song_video_dl():
            ydl_optssx = {
                "format": f"{format_id}+140",
                "outtmpl": os.path.join("downloads", f"%(id)s_{format_id}.%(ext)s"),
                "geo_bypass": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
                "cookiefile": f"{cookies()}",
            }

            with YoutubeDL(ydl_optssx) as x:
                info = x.extract_info(link)
                filename = f"{info['id']}_{format_id}.mp4"
                file_path = os.path.join("downloads", filename)
                return file_path

        @asyncify
        def song_audio_dl():
            ydl_optssx = {
                "format": format_id,
                "outtmpl": os.path.join("downloads", f"%(id)s_{format_id}.%(ext)s"),
                "geo_bypass": True,
                "noplaylist": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
                "cookiefile": f"{cookies()}",
            }

            with YoutubeDL(ydl_optssx) as x:
                info = x.extract_info(link)
                filename = f"{info['id']}_{format_id}.mp3"
                file_path = os.path.join("downloads", filename)
                return file_path

        if songvideo:
            return await song_video_dl()

        elif songaudio:
            return await song_audio_dl()

        elif video:
            if await is_on_off(config.YTDOWNLOADER):
                direct = True
                downloaded_file = await video_dl()
            else:
                command = [
                    "yt-dlp",
                    f"--cookies",
                    cookies(),
                    "-g",
                    "-f",
                    "best",
                    link,
                ]

                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()

                if stdout:
                    downloaded_file = stdout.decode().split("\n")[0]
                    direct = None
                else:
                    downloaded_file = await video_dl()
                    direct = True
        else:
            direct = True
            downloaded_file = await audio_dl()

        return downloaded_file, direct


# For backward compatibility, alias the new class
YouTube = YouTubeAPI
