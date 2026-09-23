import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import (
    Application,
    ContextTypes,
    MessageHandler,
    filters,
)
import yt_dlp

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")

# Telegram Bot API upload limits can vary by API/hosting setup.
# Keep this configurable rather than silently processing huge files.
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "49"))
MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024

URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)

# Sites that yt-dlp can handle are intentionally not hard-coded.
# Only process messages that actually contain an HTTP(S) URL.
def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0).rstrip(".,!?)]}>'\"")

def download_video(url: str, workdir: str) -> Path:
    output_template = os.path.join(workdir, "%(title).80s-%(id)s.%(ext)s")

    opts = {
        "outtmpl": output_template,
        "format": "best[ext=mp4][height<=1080]/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "overwrites": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    candidates = [
        p for p in Path(workdir).iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".mkv", ".webm", ".mov", ".avi"}
    ]

    if not candidates:
        raise RuntimeError("No video file was produced.")

    return max(candidates, key=lambda p: p.stat().st_mtime)


async def handle_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.channel_post
    if not message:
        return

    # Ignore bot-generated/non-text posts. The intended workflow is:
    # post a URL as a channel message.
    text = message.text or message.caption or ""
    url = extract_url(text)

    if not url:
        return

    workdir = tempfile.mkdtemp(prefix="tg_video_")

    try:
        log.info("Downloading %s", url)

        # yt-dlp is blocking, so run it outside the asyncio event loop.
        video_path = await asyncio.to_thread(download_video, url, workdir)

        size = video_path.stat().st_size
        if size > MAX_FILE_BYTES:
            log.warning("File too large: %.2f MB", size / 1024 / 1024)
            return

        # Send with NO caption so the channel receives only the video.
        with video_path.open("rb") as video_file:
            await context.bot.send_video(
                chat_id=message.chat_id,
                video=video_file,
                caption=None,
                supports_streaming=True,
                disable_notification=True,
            )

        # Delete the original URL message only after successful upload.
        try:
            await context.bot.delete_message(
                chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except Exception:
            log.exception("Could not delete original URL message.")

    except Exception:
        log.exception("Failed to process URL: %s", url)

        # Deliberately do not post an error message to the channel,
        # keeping the channel clean. The failure is visible in server logs.

    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")

    app = Application.builder().token(BOT_TOKEN).build()

    # Channel posts are delivered through CHANNEL_POST updates.
    app.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POST,
            handle_channel_post,
        )
    )

    log.info("Bot is running...")
    app.run_polling(
        allowed_updates=["channel_post"],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
