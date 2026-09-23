import os
import re
import asyncio
import tempfile
import shutil
from pathlib import Path

import httpx
import yt_dlp
import uvicorn
from fastapi import FastAPI, Request, HTTPException

TOKEN = os.getenv("BOT_TOKEN")
PORT = int(os.getenv("PORT", "10000"))
PUBLIC_URL = os.getenv("RENDER_EXTERNAL_URL")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not PUBLIC_URL:
    raise RuntimeError("RENDER_EXTERNAL_URL is missing")

API = f"https://api.telegram.org/bot{TOKEN}"
WEBHOOK_PATH = "/telegram"
WEBHOOK_SECRET = "telegram-secret-123"

app = FastAPI()
client = httpx.AsyncClient(timeout=180.0)
download_lock = asyncio.Lock()

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


async def telegram(method, data=None, files=None):
    response = await client.post(
        f"{API}/{method}",
        data=data,
        files=files,
    )
    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(result)

    return result.get("result")


def find_url(message):
    text = message.get("text") or message.get("caption") or ""
    match = URL_PATTERN.search(text)

    if match:
        return match.group(0).rstrip(".,!?)]}")

    return None


def download_video(url, folder):
    output = str(Path(folder) / "%(title).70s-%(id)s.%(ext)s")

    options = {
        "format": "best[ext=mp4]/best",
        "outtmpl": output,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    }

    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    ignored = (".part", ".json", ".jpg", ".jpeg", ".png", ".webp")
    videos = [
        file
        for file in Path(folder).iterdir()
        if file.is_file() and not file.name.endswith(ignored)
    ]

    if not videos:
        raise RuntimeError("No video was downloaded")

    return max(videos, key=lambda file: file.stat().st_size)


async def send_video(chat_id, video):
    max_bytes = 49 * 1024 * 1024

    if video.stat().st_size > max_bytes:
        raise RuntimeError("Video is larger than 49 MB")

    with open(video, "rb") as file:
        await telegram(
            "sendVideo",
            data={
                "chat_id": str(chat_id),
                "supports_streaming": "true",
            },
            files={
                "video": (video.name, file, "video/mp4"),
            },
        )


async def delete_message(chat_id, message_id):
    await telegram(
        "deleteMessage",
        data={
            "chat_id": str(chat_id),
            "message_id": str(message_id),
        },
    )


async def process_message(message):
    url = find_url(message)

    if not url:
        return

    chat_id = message["chat"]["id"]
    message_id = message["message_id"]
    folder = tempfile.mkdtemp(prefix="tg_video_")

    async with download_lock:
        try:
            print("Downloading:", url)

            video = await asyncio.to_thread(
                download_video,
                url,
                folder,
            )

            print("Uploading:", video.name)

            await send_video(chat_id, video)
            await delete_message(chat_id, message_id)

            print("Done")

        except Exception as error:
            print("ERROR:", error)

        finally:
            shutil.rmtree(folder, ignore_errors=True)


async def setup_webhook():
    webhook_url = PUBLIC_URL.rstrip("/") + WEBHOOK_PATH

    await telegram(
        "setWebhook",
        data={
            "url": webhook_url,
            "allowed_updates": '["channel_post"]',
            "drop_pending_updates": "true",
            "secret_token": WEBHOOK_SECRET,
        },
    )

    print("Webhook connected:", webhook_url)


@app.on_event("startup")
async def startup():
    await setup_webhook()
    print("Bot is online")


@app.on_event("shutdown")
async def shutdown():
    await client.aclose()


@app.get("/")
async def home():
    return {"status": "online"}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Forbidden")

    update = await request.json()

    message = update.get("channel_post")

    if message:
        asyncio.create_task(process_message(message))

    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
    )
