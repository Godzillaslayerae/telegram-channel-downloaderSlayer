# Telegram Channel Video Downloader Bot

This bot is designed specifically for a Telegram **channel**.

## Workflow

1. Add the bot to your channel as an administrator.
2. Give it permission to **Post Messages** and **Delete Messages**.
3. Post a supported video URL in the channel.
4. The bot downloads the video using `yt-dlp`.
5. The bot posts **only the video**, without a caption.
6. After the upload succeeds, the original URL message is deleted.
7. Temporary downloaded files are deleted.

The bot does not intentionally send ads, promotional messages, or error messages to the channel.

## Setup

### 1. Create the bot

Open `@BotFather` in Telegram and create a bot with `/newbot`.

Copy the token.

### 2. Install dependencies

Python 3.12+ is recommended.

```bash
pip install -r requirements.txt
```

You also need FFmpeg installed on the machine. On Ubuntu/Debian:

```bash
sudo apt update
sudo apt install ffmpeg
```

### 3. Set the token

Linux/macOS:

```bash
export BOT_TOKEN="YOUR_BOT_TOKEN"
python bot.py
```

Windows PowerShell:

```powershell
$env:BOT_TOKEN="YOUR_BOT_TOKEN"
python bot.py
```

### 4. Add the bot to your channel

Add it as an administrator and enable:

- Post Messages
- Delete Messages

The bot does not need permission to edit other people's messages.

### 5. Test

Post a URL by itself in the channel, for example:

```text
https://example.com/video
```

If `yt-dlp` supports that URL and the resulting file is within the configured size limit, the channel should end up with only the video.

## Important limitations

- `yt-dlp` does not guarantee that every website works.
- Some sites require login, DRM, age verification, or block automated downloading.
- Do not use this to download content you do not have permission to download.
- Telegram bot upload limits and hosting limits can restrict large videos.
- A free server can run out of CPU, RAM, bandwidth, or disk space.
- The original URL is deleted only after a successful video upload. If downloading/uploading fails, the original URL remains so it is not silently lost.

## Files

- `bot.py` — main bot
- `requirements.txt` — Python packages
- `.env.example` — token configuration example
- `Dockerfile` — optional container deployment
