"""แกนกลางของ agent — ใช้ร่วมกันทุกช่องทาง (CLI, Telegram, งานตั้งเวลา)"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
)

from .tools import TOOL_NAMES, build_server

SYSTEM_PROMPT = """\
คุณคือผู้ช่วยส่วนตัวของเจ้าของ ทำงานได้หลากหลาย: คุยทั่วไป ค้นข้อมูล จัดการไฟล์ จดบันทึก และแจ้งเตือน
- ตอบเป็นภาษาไทยเว้นแต่ผู้ใช้ใช้ภาษาอื่น ตอบกระชับ เหมือนคุยแชท
- ถ้าเป็นคำสั่งที่ทำได้ ให้ลงมือทำเลย ไม่ต้องถามซ้ำ ยกเว้นข้อมูลที่ขาดจริงๆ
- ก่อนลบ/แก้ไฟล์ รันคำสั่ง หรือส่งอะไรออกไปข้างนอก ระบบจะขอให้เจ้าของกดยืนยัน ถ้าถูกปฏิเสธให้หยุดและบอกว่าไม่ได้ทำ
- เรื่องที่ต้องจำข้ามวัน ให้ใช้ save_note ส่วนเรื่องด่วนจริงๆ ใช้ notify_owner
- ถ้าไม่รู้หรือไม่แน่ใจ ให้บอกตรงๆ อย่าเดา
"""

# เครื่องมือที่ปลอดภัย (อ่านอย่างเดียว) — อนุมัติอัตโนมัติ ไม่ต้องถาม
SAFE_TOOLS = ["Read", "Glob", "Grep", "WebSearch", "WebFetch", *TOOL_NAMES]

# ฟังก์ชันถามเจ้าของ: รับคำอธิบายสิ่งที่จะทำ คืน True ถ้าอนุญาต
ConfirmFn = Callable[[str], Awaitable[bool]]


def describe_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return f"รันคำสั่ง:\n{tool_input.get('command', '')}"
    if tool_name in ("Write", "Edit", "NotebookEdit"):
        return f"{tool_name} ไฟล์: {tool_input.get('file_path') or tool_input.get('notebook_path')}"
    return f"{tool_name}: {str(tool_input)[:300]}"


class Agent:
    """ห่อ ClaudeSDKClient ให้คุยต่อเนื่องได้ (จำบทสนทนา) พร้อมระบบขออนุญาต"""

    def __init__(self, confirm: ConfirmFn | None = None, cwd: str | None = None) -> None:
        self.confirm = confirm  # ถ้าเป็น None = ปฏิเสธทุกอย่างที่ไม่ใช่ SAFE_TOOLS
        self._lock = asyncio.Lock()  # ให้ประมวลผลทีละคำสั่ง
        self._client = ClaudeSDKClient(
            ClaudeAgentOptions(
                model=os.getenv("MODEL", "claude-opus-5-5"),
                system_prompt=SYSTEM_PROMPT,
                mcp_servers={"assistant": build_server()},
                allowed_tools=SAFE_TOOLS,
                can_use_tool=self._can_use_tool,
                cwd=cwd or os.getenv("AGENT_WORKDIR", os.getcwd()),
                max_turns=int(os.getenv("MAX_TURNS", "25")),
                max_budget_usd=float(os.getenv("MAX_BUDGET_USD", "2.0")),  # เพดานต่อเซสชัน กันบิลบาน
            )
        )

    async def _can_use_tool(
        self, tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        if tool_name in SAFE_TOOLS:
            return PermissionResultAllow()
        if self.confirm is None:
            return PermissionResultDeny(message="ไม่มีช่องทางขออนุญาตเจ้าของ จึงทำไม่ได้")
        if await self.confirm(describe_call(tool_name, tool_input)):
            return PermissionResultAllow()
        return PermissionResultDeny(message="เจ้าของไม่อนุญาต")

    async def start(self) -> None:
        await self._client.connect()

    async def stop(self) -> None:
        await self._client.disconnect()

    async def ask(self, text: str) -> str:
        """ส่งข้อความหา agent แล้วรอคำตอบสุดท้าย (จำบริบทจากครั้งก่อนๆ)"""
        async with self._lock:
            await self._client.query(text)
            parts: list[str] = []
            final: str | None = None
            async for msg in self._client.receive_response():
                if isinstance(msg, AssistantMessage):
                    parts = [b.text for b in msg.content if isinstance(b, TextBlock)] or parts
                elif isinstance(msg, ResultMessage):
                    if msg.is_error:
                        return f"⚠️ เกิดข้อผิดพลาด: {msg.subtype} {msg.errors or ''}".strip()
                    final = msg.result
            return (final or "\n".join(parts) or "(ไม่มีคำตอบ)").strip()
