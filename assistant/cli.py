"""คุยกับ agent ผ่านเทอร์มินัล — เริ่มจากตรงนี้เพื่อทดสอบก่อนต่อ Telegram

รัน:  python -m assistant.cli
"""
from __future__ import annotations

import asyncio

from .core import Agent


async def confirm_in_terminal(description: str) -> bool:
    print(f"\n⚠️  agent ขออนุญาต: {description}")
    answer = await asyncio.to_thread(input, "อนุญาตไหม? [y/N] ")
    return answer.strip().lower() in ("y", "yes", "ใช่")


async def main() -> None:
    agent = Agent(confirm=confirm_in_terminal)
    await agent.start()
    print("พร้อมแล้ว พิมพ์คุยได้เลย (exit เพื่อออก)\n")
    try:
        while True:
            text = (await asyncio.to_thread(input, "คุณ: ")).strip()
            if text.lower() in ("exit", "quit", "ออก"):
                break
            if text:
                print(f"\nAI: {await agent.ask(text)}\n")
    finally:
        await agent.stop()


if __name__ == "__main__":
    asyncio.run(main())
