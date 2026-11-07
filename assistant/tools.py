"""เครื่องมือ (tools) ที่เราเขียนเองให้ agent เรียกใช้

เพิ่มความสามารถใหม่ = เขียนฟังก์ชันด้วย @tool แล้วใส่ลงใน TOOLS ด้านล่าง
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from claude_agent_sdk import create_sdk_mcp_server, tool

SERVER_NAME = "assistant"
TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Bangkok"))
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
NOTES_FILE = DATA_DIR / "notes.json"

# ฟังก์ชันที่ใช้ส่งข้อความหาเจ้าของ (CLI = print, Telegram = ส่งเข้าแชท)
Notifier = Callable[[str], Awaitable[None]]


async def _print_notifier(text: str) -> None:
    print(f"\n🔔 {text}\n")


_notifier: Notifier = _print_notifier


def set_notifier(fn: Notifier) -> None:
    global _notifier
    _notifier = fn


def _text(s: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": s}]}


def _load_notes() -> list[dict[str, str]]:
    try:
        return json.loads(NOTES_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []


@tool("get_current_time", "ดูวันที่และเวลาปัจจุบัน (เวลาไทย)", {})
async def get_current_time(args: dict[str, Any]) -> dict[str, Any]:
    return _text(datetime.now(TZ).strftime("%A %d %B %Y %H:%M:%S (%Z)"))


@tool(
    "save_note",
    "จดบันทึกถาวรไว้ใช้ภายหลัง เช่น ข้อมูลที่ผู้ใช้บอกให้จำ งานที่ต้องตามต่อ",
    {"text": str},
)
async def save_note(args: dict[str, Any]) -> dict[str, Any]:
    notes = _load_notes()
    notes.append({"time": datetime.now(TZ).isoformat(timespec="seconds"), "text": args["text"]})
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    NOTES_FILE.write_text(json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    return _text(f"จดแล้ว (ตอนนี้มี {len(notes)} บันทึก)")


@tool("list_notes", "อ่านบันทึกทั้งหมดที่เคยจดไว้", {})
async def list_notes(args: dict[str, Any]) -> dict[str, Any]:
    notes = _load_notes()
    if not notes:
        return _text("ยังไม่มีบันทึก")
    return _text("\n".join(f"- [{n['time']}] {n['text']}" for n in notes))


@tool(
    "notify_owner",
    "ส่งข้อความแจ้งเตือนหาเจ้าของทันที ใช้เมื่อมีเรื่องด่วนที่เจ้าของควรรู้",
    {"message": str},
)
async def notify_owner(args: dict[str, Any]) -> dict[str, Any]:
    await _notifier(args["message"])
    return _text("ส่งแจ้งเตือนแล้ว")


TOOLS = [get_current_time, save_note, list_notes, notify_owner]

# ชื่อเต็มที่ SDK ใช้ภายใน = mcp__<server>__<tool>
TOOL_NAMES = [f"mcp__{SERVER_NAME}__{t.name}" for t in TOOLS]


def build_server():
    return create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0", tools=TOOLS)
