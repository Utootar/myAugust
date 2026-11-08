"""คุยกับ agent ผ่าน Telegram — รันบนเซิร์ฟเวอร์ที่เปิดตลอด

รัน:  python -m assistant.telegram_bot

ความปลอดภัย: ตอบเฉพาะ TELEGRAM_OWNER_ID คนเดียว คนอื่นทักมาจะถูกเมิน
งานเสี่ยง (รันคำสั่ง, แก้ไฟล์) จะส่งปุ่ม อนุญาต/ปฏิเสธ มาให้กดก่อน
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .core import Agent
from .tools import set_notifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("assistant")

OWNER_ID = int(os.environ["TELEGRAM_OWNER_ID"])
CONFIRM_TIMEOUT = 120  # วินาที — ไม่กดภายในเวลานี้ถือว่าปฏิเสธ

agent = Agent()
_pending: dict[str, asyncio.Future[bool]] = {}
_ids = itertools.count(1)
_app: Application | None = None


async def notify(text: str) -> None:
    """ส่งข้อความหาเจ้าของ (ใช้โดย tool notify_owner และงานตั้งเวลา)"""
    assert _app is not None
    await _app.bot.send_message(OWNER_ID, text)


async def confirm(description: str) -> bool:
    assert _app is not None
    key = str(next(_ids))
    fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
    _pending[key] = fut
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("✅ อนุญาต", callback_data=f"ok:{key}"),
            InlineKeyboardButton("❌ ปฏิเสธ", callback_data=f"no:{key}"),
        ]]
    )
    await _app.bot.send_message(OWNER_ID, f"⚠️ ขออนุญาต\n\n{description}", reply_markup=keyboard)
    try:
        return await asyncio.wait_for(fut, CONFIRM_TIMEOUT)
    except asyncio.TimeoutError:
        return False
    finally:
        _pending.pop(key, None)


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.from_user.id != OWNER_ID:
        return
    verdict, _, key = (q.data or "").partition(":")
    fut = _pending.get(key)
    if fut and not fut.done():
        fut.set_result(verdict == "ok")
    await q.answer()
    await q.edit_message_text(f"{q.message.text}\n\n→ {'อนุญาตแล้ว' if verdict == 'ok' else 'ปฏิเสธแล้ว'}")


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None or update.effective_user is None or update.effective_user.id != OWNER_ID:
        return  # เมินคนอื่นเงียบๆ
    await context.bot.send_chat_action(msg.chat_id, "typing")
    reply = await agent.ask(msg.text or "")
    for i in range(0, len(reply), 4000):  # Telegram จำกัด 4096 ตัวอักษรต่อข้อความ
        await msg.reply_text(reply[i : i + 4000])


async def on_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user and update.effective_user.id == OWNER_ID:
        await update.effective_message.reply_text("ตอนนี้รองรับเฉพาะข้อความตัวอักษร (เสียง/รูปจะเพิ่มทีหลัง)")


async def post_init(app: Application) -> None:
    global _app
    _app = app
    agent.confirm = confirm
    set_notifier(notify)
    await agent.start()
    log.info("agent พร้อมแล้ว")


async def post_shutdown(app: Application) -> None:
    await agent.stop()


def main() -> None:
    app = (
        Application.builder()
        .token(os.environ["TELEGRAM_BOT_TOKEN"])
        .concurrent_updates(True)  # จำเป็น: ไม่งั้นปุ่มอนุญาตจะถูกบล็อกระหว่าง agent รอคำตอบ
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(~filters.TEXT & ~filters.COMMAND, on_unsupported))
    app.run_polling()


if __name__ == "__main__":
    main()
