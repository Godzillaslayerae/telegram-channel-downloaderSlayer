import asyncio
import logging
import mimetypes
import os
import re
import secrets
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import yt_dlp
import uvicorn
from fastapi import FastAPI, HTTPException, Request


# =========================
# SETTINGS
# =========================

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "49"))

# Render automatically provides this for Web Services.
PUBLIC_URL = (
    os.getenv("RENDER_EXTERNAL_URL")
    or os.getenv("PUBLIC_URL")
)

WEBHOOK_PATH = "/telegram/webhook"

# Generate a secret automatically.
WEBHOOK_SECRET = secrets.token_urlsafe(32)

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

URL_REGEX = re.compile(
    r"https?://[^\s<>\"]+",
    re.IGNORECASE
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

log = logging.getLogger("telegram-bot")

http_client = None

# Only process one download at a time.
download_lock = asyncio.Semaphore(1)


# =========================
# TELEGRAM API
# =========================

async def telegram_call(method, data=None, files=None):
    global http_client

    response = await http_client.post(
        f"{TELEGRAM_API}/{method}",
        data=data,
        files=files
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {result}"
        )

    return result.get("result")


async def setup_webhook():
    if not PUBLIC_URL:
        log.error(
            "PUBLIC_URL / RENDER_EXTERNAL_URL is missing. "
            "Webhook cannot be configured."
        )
        return

    webhook_url = PUBLIC_URL.rstrip("/") + WEBHOOK_PATH

    log.info("Setting Telegram webhook: %s", webhook_url)

    for attempt in range(1, 6):
        try:
            result = await telegram_call(
                "setWebhook",
                data={
                    "url": webhook_url,
                    "allowed_updates": '["channel_post"]',
                    "drop_pending_updates": "true",
                    "secret_token": WEBHOOK_SECRET,
                },
            )

            log.info(
                "Webhook configured successfully: %s",
                result
            )

            return

        except Exception as e:
            log.error(
                "Webhook setup attempt %s failed: %s",
                attempt,
                e
            )

            if attempt < 5:
                await asyncio.sleep(5)

    log.error("Could not configure Telegram webhook.")


# =========================
# URL EXTRACTION
# =========================

def extract_url(message):
    text = message.get("text") or message.get("caption") or ""

    match = URL_REGEX.search(text)

    if not match:
        return None

    return match.group(0).rstrip(".,!?)]}")


# =========================
# VIDEO DOWNLOAD
# =========================

def download_video(url, folder):
    output_template = str(
        Path(folder) / "%(title).80s-%(id)s.%(ext)s"
    )

    ydl_options = {
        # Prefer MP4 because Telegram can send it as a video.
        "format": "best[ext=mp4]/best",

        "outtmpl": output_template,

        # Don't download playlists.
        "noplaylist": True,

        # Quiet but still show useful errors.
        "quiet": True,
        "no_warnings": True,

        # Avoid unnecessary metadata files.
        "writethumbnail": False,
        "writeinfojson": False,

        # Reasonable network retries.
        "retries": 3,
        "fragment_retries": 3,

        # Don't download huge 4K files unnecessarily.
        "max_filesize": MAX_FILE_MB * 1024 * 1024,
    }

    with yt_dlp.YoutubeDL(ydl_options) as ydl:
        ydl.download([url])

    # Find downloaded video.
    possible_files = []

    for file in Path(folder).rglob("*"):
        if not file.is_file():
            continue

        if file.name.endswith(
            (".part", ".ytdl", ".json", ".jpg", ".jpeg", ".png", ".webp")
        ):
            continue

        possible_files.append(file)

    if not possible_files:
        raise RuntimeError("No video file was downloaded.")

    # Pick the largest video file.
    video = max(
        possible_files,
        key=lambda x: x.stat().st_size
    )

    return video


# =========================
# SEND VIDEO
# =========================

async def send_video(chat_id, video_path):
    file_size = video_path.stat().st_size
    max_size = MAX_FILE_MB * 1024 * 1024

    if file_size > max_size:
        raise RuntimeError(
            f"Video is too large: "
            f"{file_size / 1024 / 1024:.1f} MB"
        )

    mime_type = (
        mimetypes.guess_type(video_path.name)[0]
        or "video/mp4"
    )

    with open(video_path, "rb") as video_file:

        files = {
            "video": (
                video_path.name,
                video_file,
                mime_type
            )
        }

        data = {
            "chat_id": str(chat_id),

            # No caption.
            # No bot name.
            # No promotional text.

            "supports_streaming": "true",
            "disable_notification": "true",
        }

        return await telegram_call(
            "sendVideo",
            data=data,
            files=files
        )


# =========================
# DELETE ORIGINAL MESSAGE
# =========================

async def delete_message(chat_id, message_id):
    return await telegram_call(
        "deleteMessage",
        data={
            "chat_id": str(chat_id),
            "message_id": str(message_id),
        }
    )


# =========================
# PROCESS CHANNEL POST
# =========================

async def process_channel_post(message):

    url = extract_url(message)

    if not url:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    message_id = message.get("message_id")

    if not chat_id or not message_id:
        return

    log.info("URL detected: %s", url)

    async with download_lock:

        temp_dir = tempfile.mkdtemp(
            prefix="telegram_video_"
        )

        try:

            log.info("Downloading video...")

            video_path = await asyncio.to_thread(
                download_video,
                url,
                temp_dir
            )

            log.info(
                "Downloaded: %s (%.2f MB)",
                video_path.name,
                video_path.stat().st_size / 1024 / 1024
            )

            # Send video first.
            await send_video(
                chat_id,
                video_path
            )

            log.info("Video sent successfully.")

            # Delete original link ONLY after video was sent.
            await delete_message(
                chat_id,
                message_id
            )

            log.info(
                "Original link message deleted."
            )

        except Exception as e:

            log.exception(
                "Failed to process URL: %s",
                e
            )

            # Original message is NOT deleted if something fails.

        finally:

            shutil.rmtree(
                temp_dir,
                ignore_errors=True
            )


# =========================
# TELEGRAM WEBHOOK
# =========================

async def handle_update(update):

    message = update.get("channel_post")

    if not message:
        return

    await process_channel_post(message)


# =========================
# FASTAPI
# =========================

@asynccontextmanager
async def lifespan(app):

    global http_client

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(
            180.0,
            connect=30.0
        )
    )

    await setup_webhook()

    log.info("Telegram bot is running.")

    yield

    await http_client.aclose()

    log.info("Bot stopped.")


app = FastAPI(
    lifespan=lifespan
)


@app.get("/")
async def home():

    return {
        "status": "online",
        "bot": "Telegram Video Downloader"
    }


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):

    # Check Telegram secret token.
    incoming_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if incoming_secret != WEBHOOK_SECRET:
        raise HTTPException(
            status_code=403,
            detail="Forbidden"
        )

    try:
        update = await request.json()

    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid JSON"
        )

    # Return immediately to Telegram.
    # Processing happens in the background.
    asyncio.create_task(
        handle_update(update)
    )

    return {
        "ok": True
    }


# =========================
# START SERVER
# =========================

if __name__ == "__main__":

    port = int(
        os.getenv("PORT", "10000")
    )

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port
    )        raise RuntimeError("No video file was produced.")

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
