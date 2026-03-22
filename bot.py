"""
AXIS AGENT — Telegram Bot
Анализ видео + Text → раскадровка/промпты через Gemini API
"""

import os
import asyncio
import logging
import tempfile
import base64
import json
import re
from pathlib import Path

import cv2
import numpy as np
import httpx

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "YOUR_GEMINI_API_KEY")
GEMINI_MODEL   = "gemini-2.0-flash"

# ─── Conversation states ──────────────────────────────────────────────────────
(
    STATE_MAIN_MENU,
    STATE_VIDEO_WAIT,
    STATE_VIDEO_CONTEXT,
    STATE_TEXT_WAIT,
    STATE_TEXT_DURATION,
) = range(5)

# ─── Gemini helpers ───────────────────────────────────────────────────────────

GEMINI_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)


async def gemini_request(parts: list) -> str:
    """Send a multimodal request to Gemini and return text."""
    payload = {"contents": [{"parts": parts}]}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(GEMINI_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected Gemini response: {data}") from e


# ─── Frame extraction ─────────────────────────────────────────────────────────

def extract_frames(video_path: str, max_frames: int = 30) -> tuple[list[str], float]:
    """
    Extract up to max_frames evenly-spaced frames from a video file.
    Returns list of base64-encoded JPEG strings and video duration in seconds.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Не удалось открыть видео файл.")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps          = cap.get(cv2.CAP_PROP_FPS) or 25
    duration     = total_frames / fps

    step     = max(1, total_frames // max_frames)
    indices  = list(range(0, total_frames, step))[:max_frames]
    b64frames: list[str] = []

    for idx in indices:
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ret, frame = cap.read()
        if not ret:
            continue
        # Resize to max 512px wide to keep payload small
        h, w = frame.shape[:2]
        if w > 512:
            frame = cv2.resize(frame, (512, int(h * 512 / w)))
        _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        b64frames.append(base64.b64encode(buf).decode())

    cap.release()
    return b64frames, duration


# ─── Gemini prompts ───────────────────────────────────────────────────────────

VIDEO_SYSTEM_PROMPT = """
Ты — нейросетевой аналитик визуального контента AXIS AGENT.
Тебе дан набор кадров из видео (длительность указана отдельно).
Твоя задача: проанализировать видео и вернуть СТРОГО JSON без лишнего текста:

{
  "summary": "Краткое описание видео (2-3 предложения)",
  "script": "Подробный тайм-кодированный сценарий по кадрам",
  "prompts": [
    {
      "timestamp": "00:00-00:05",
      "scene": "Описание сцены",
      "prompt": "Детальный промпт для нейросети (англ.)"
    }
  ]
}

Промпты пиши на английском языке в стиле Midjourney/SDXL.
""".strip()

TEXT_SYSTEM_PROMPT = """
Ты — нейросетевой генератор раскадровок AXIS AGENT.
На основе текстового описания создай раскадровку и промпты.
Вернуть СТРОГО JSON без лишнего текста:

{
  "summary": "Общее описание концепции",
  "storyboard": [
    {
      "frame": 1,
      "timecode": "00:00-00:05",
      "description": "Описание кадра",
      "camera": "Тип съёмки (напр. wide shot, close-up)",
      "prompt": "Детальный промпт для нейросети (англ.)"
    }
  ]
}

Промпты пиши на английском языке в стиле Midjourney/SDXL.
""".strip()


async def analyze_video(frames: list[str], duration: float, context: str) -> dict:
    parts = [
        {"text": VIDEO_SYSTEM_PROMPT},
        {"text": f"Длительность видео: {duration:.1f} секунд."},
    ]
    if context:
        parts.append({"text": f"Контекст от пользователя: {context}"})
    for b64 in frames:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})

    raw = await gemini_request(parts)
    # Strip markdown fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    return json.loads(raw)


async def text_to_storyboard(text: str, duration: int) -> dict:
    parts = [
        {"text": TEXT_SYSTEM_PROMPT},
        {"text": f"Длительность ролика: {duration} секунд.\nОписание: {text}"},
    ]
    raw = await gemini_request(parts)
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    return json.loads(raw)


# ─── Formatters ───────────────────────────────────────────────────────────────

def format_video_result(data: dict) -> list[str]:
    """Return list of message chunks (Telegram 4096 char limit)."""
    lines = []
    lines.append("🎬 *АНАЛИЗ ВИДЕО — AXIS AGENT*\n")

    if data.get("summary"):
        lines.append(f"📋 *РЕЗЮМЕ*\n{data['summary']}\n")

    if data.get("script"):
        lines.append(f"📝 *СЦЕНАРИЙ*\n{data['script']}\n")

    prompts = data.get("prompts", [])
    if prompts:
        lines.append("✨ *ПРОМПТЫ*")
        for p in prompts:
            lines.append(
                f"\n⏱ `{p.get('timestamp','')}`\n"
                f"🎞 {p.get('scene','')}\n"
                f"💡 `{p.get('prompt','')}`"
            )

    full = "\n".join(lines)
    # Split into ≤4000-char chunks on newline boundary
    chunks, buf = [], ""
    for line in full.split("\n"):
        if len(buf) + len(line) + 1 > 4000:
            chunks.append(buf)
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf:
        chunks.append(buf)
    return chunks


def format_storyboard_result(data: dict) -> list[str]:
    lines = []
    lines.append("🎬 *РАСКАДРОВКА — AXIS AGENT*\n")

    if data.get("summary"):
        lines.append(f"📋 *КОНЦЕПЦИЯ*\n{data['summary']}\n")

    for frame in data.get("storyboard", []):
        lines.append(
            f"\n🖼 *Кадр {frame.get('frame','')}* — `{frame.get('timecode','')}`\n"
            f"📽 {frame.get('description','')}\n"
            f"🎥 Съёмка: _{frame.get('camera','')}_\n"
            f"💡 `{frame.get('prompt','')}`"
        )

    full = "\n".join(lines)
    chunks, buf = [], ""
    for line in full.split("\n"):
        if len(buf) + len(line) + 1 > 4000:
            chunks.append(buf)
            buf = line + "\n"
        else:
            buf += line + "\n"
    if buf:
        chunks.append(buf)
    return chunks


# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎬 Анализ видео", callback_data="mode_video"),
            InlineKeyboardButton("📝 Text → Раскадровка", callback_data="mode_text"),
        ],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data="help")],
    ])


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "⚡ *AXIS AGENT — Neural Video Synthesis*\n\n"
        "Выберите режим работы:",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return STATE_MAIN_MENU


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Главное меню:",
        reply_markup=main_menu_keyboard(),
    )
    return STATE_MAIN_MENU


async def button_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "mode_video":
        await query.edit_message_text(
            "🎬 *Режим: Анализ видео*\n\n"
            "Отправь мне видеофайл (до 50 МБ).\n"
            "Я извлеку кадры и сгенерирую сценарий + промпты.",
            parse_mode="Markdown",
        )
        return STATE_VIDEO_WAIT

    elif query.data == "mode_text":
        await query.edit_message_text(
            "📝 *Режим: Text → Раскадровка*\n\n"
            "Опиши концепцию своего ролика или идею.\n"
            "Я создам раскадровку с промптами для нейросетей.",
            parse_mode="Markdown",
        )
        return STATE_TEXT_WAIT

    elif query.data == "help":
        await query.edit_message_text(
            "ℹ️ *AXIS AGENT — Помощь*\n\n"
            "🎬 *Анализ видео* — отправь видеофайл, получи:\n"
            "  • Резюме сюжета\n  • Тайм-кодированный сценарий\n  • Промпты по сценам\n\n"
            "📝 *Text → Раскадровка* — опиши идею, получи:\n"
            "  • Концепцию\n  • Покадровую раскадровку\n  • Промпты для каждого кадра\n\n"
            "Команды:\n/start — главное меню\n/cancel — отменить",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("← Назад", callback_data="back_main")
            ]]),
        )
        return STATE_MAIN_MENU

    elif query.data == "back_main":
        await query.edit_message_text(
            "Главное меню:",
            reply_markup=main_menu_keyboard(),
        )
        return STATE_MAIN_MENU

    return STATE_MAIN_MENU


async def receive_video(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message

    # Accept both video and document (some clients send as document)
    file_obj = msg.video or msg.document
    if not file_obj:
        await msg.reply_text("❌ Пожалуйста, отправь видеофайл.")
        return STATE_VIDEO_WAIT

    # Check size (50 MB)
    if file_obj.file_size and file_obj.file_size > 50 * 1024 * 1024:
        await msg.reply_text("❌ Файл слишком большой. Максимум 50 МБ.")
        return STATE_VIDEO_WAIT

    ctx.user_data["video_file_id"] = file_obj.file_id
    await msg.reply_text(
        "✅ Видео получено!\n\n"
        "Добавь контекст для анализа (необязательно).\n"
        "Например: *«рекламный ролик кроссовок»* или *«свадебное видео»*\n\n"
        "Или напиши `/skip` чтобы пропустить.",
        parse_mode="Markdown",
    )
    return STATE_VIDEO_CONTEXT


async def receive_video_context(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    context = "" if text.lower() in ("/skip", "skip") else text
    ctx.user_data["video_context"] = context

    status_msg = await update.message.reply_text("⏳ Скачиваю видео...")

    try:
        file_id = ctx.user_data["video_file_id"]
        tg_file = await ctx.bot.get_file(file_id)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        await tg_file.download_to_drive(tmp_path)
        await status_msg.edit_text("⚙️ Извлекаю кадры...")

        frames, duration = extract_frames(tmp_path, max_frames=30)
        os.unlink(tmp_path)

        if not frames:
            await status_msg.edit_text("❌ Не удалось извлечь кадры из видео.")
            return STATE_MAIN_MENU

        await status_msg.edit_text(
            f"🧠 Анализирую {len(frames)} кадров ({duration:.1f} сек)...\nЭто займёт ~30 секунд."
        )

        result = await analyze_video(frames, duration, context)
        chunks = format_video_result(result)

        await status_msg.delete()
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="Markdown")

        await update.message.reply_text(
            "✅ Анализ завершён!",
            reply_markup=main_menu_keyboard(),
        )

    except json.JSONDecodeError:
        await status_msg.edit_text(
            "❌ Gemini вернул нечитаемый ответ. Попробуй ещё раз."
        )
    except Exception as e:
        logger.exception("Video analysis error")
        await status_msg.edit_text(f"❌ Ошибка: {e}")

    return STATE_MAIN_MENU


async def receive_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data["story_text"] = update.message.text
    await update.message.reply_text(
        "⏱ Укажи длительность ролика в секундах.\n"
        "Например: `30`, `60`, `120`",
        parse_mode="Markdown",
    )
    return STATE_TEXT_DURATION


async def receive_duration(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    try:
        duration = int(raw)
        if duration <= 0 or duration > 600:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Введи число от 1 до 600 (секунды)."
        )
        return STATE_TEXT_DURATION

    story_text = ctx.user_data.get("story_text", "")
    status_msg = await update.message.reply_text(
        "🧠 Генерирую раскадровку... (~20 секунд)"
    )

    try:
        result = await text_to_storyboard(story_text, duration)
        chunks = format_storyboard_result(result)

        await status_msg.delete()
        for chunk in chunks:
            await update.message.reply_text(chunk, parse_mode="Markdown")

        await update.message.reply_text(
            "✅ Раскадровка готова!",
            reply_markup=main_menu_keyboard(),
        )
    except json.JSONDecodeError:
        await status_msg.edit_text(
            "❌ Gemini вернул нечитаемый ответ. Попробуй ещё раз."
        )
    except Exception as e:
        logger.exception("Text to storyboard error")
        await status_msg.edit_text(f"❌ Ошибка: {e}")

    return STATE_MAIN_MENU


async def cmd_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    ctx.user_data.clear()
    await update.message.reply_text(
        "❌ Отменено.",
        reply_markup=main_menu_keyboard(),
    )
    return STATE_MAIN_MENU


async def fallback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Используй кнопки меню или /start",
        reply_markup=main_menu_keyboard(),
    )
    return STATE_MAIN_MENU


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            STATE_MAIN_MENU: [
                CallbackQueryHandler(button_handler),
                CommandHandler("menu", cmd_menu),
            ],
            STATE_VIDEO_WAIT: [
                MessageHandler(filters.VIDEO | filters.Document.VIDEO, receive_video),
                CallbackQueryHandler(button_handler),
            ],
            STATE_VIDEO_CONTEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_video_context),
                CommandHandler("skip", receive_video_context),
            ],
            STATE_TEXT_WAIT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text),
                CallbackQueryHandler(button_handler),
            ],
            STATE_TEXT_DURATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_duration),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cmd_cancel),
            CommandHandler("start", cmd_start),
            MessageHandler(filters.ALL, fallback_handler),
        ],
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("menu", cmd_menu))

    logger.info("AXIS AGENT BOT started ⚡")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
