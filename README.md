# myAugust — AI agent ส่วนตัว

ผู้ช่วยที่คุยได้ทุกเรื่อง สั่งงานได้ (ค้นเว็บ อ่าน/แก้ไฟล์ รันคำสั่ง จดบันทึก แจ้งเตือน)
สร้างด้วย **Claude Agent SDK** (Python) รันบนเซิร์ฟเวอร์ที่เปิดตลอด แล้วคุยผ่านมือถือทาง Telegram

```
assistant/
  core.py          สมองของ agent: จำบทสนทนา + ระบบขออนุญาต + system prompt
  tools.py         เครื่องมือที่เราเขียนเอง (เวลา, จดบันทึก, แจ้งเตือน) — เพิ่มได้ที่นี่
  cli.py           คุยผ่านเทอร์มินัล (ใช้ทดสอบ)
  telegram_bot.py  คุยผ่าน Telegram (ใช้งานจริง)
```

## เริ่มใช้

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # แล้วกรอก ANTHROPIC_API_KEY
set -a; source .env; set +a

python -m assistant.cli     # ทดสอบก่อนด้วยการพิมพ์คุย
```

## ต่อ Telegram

1. คุยกับ **@BotFather** → `/newbot` → ได้ token ใส่ `TELEGRAM_BOT_TOKEN`
2. ถาม **@userinfobot** → ได้เลข id ของคุณ ใส่ `TELEGRAM_OWNER_ID`
3. `python -m assistant.telegram_bot` แล้วทักบอทได้เลย

ให้รันตลอดบนเซิร์ฟเวอร์ด้วย systemd หรือ `docker run --restart=always` (ถ้าต้องการ บอกผมแล้วจะเขียนไฟล์ให้)

## ความปลอดภัย (สำคัญ เพราะ agent "สั่งอะไรก็ทำ")

- **ตอบเฉพาะเจ้าของ** — ข้อความจากคนอื่นถูกเมิน
- **อ่านอย่างเดียว** (Read, Glob, Grep, ค้นเว็บ, เครื่องมือใน `tools.py`) → ทำเลย
- **อย่างอื่น** (Bash, Write, Edit) → ส่งปุ่ม อนุญาต/ปฏิเสธ ไปที่มือถือก่อน ไม่กดภายใน 2 นาทีถือว่าปฏิเสธ
- มีเพดานค่าใช้จ่าย `MAX_BUDGET_USD` และจำนวนรอบ `MAX_TURNS` กันบิลบาน
- ตั้ง `AGENT_WORKDIR` ให้ชี้โฟลเดอร์เฉพาะ อย่ารันด้วยสิทธิ์ root บนเครื่องที่มีข้อมูลสำคัญ

## สถานะ

| ส่วน | สถานะ |
|---|---|
| เครื่องมือ + ระบบขออนุญาต | ทดสอบแบบออฟไลน์แล้ว |
| คุยกับ Claude จริง / Telegram | **ยังไม่ได้ทดสอบ** (ต้องใช้ API key และ bot token ของคุณ) |
| ฟังเสียง / คำปลุก / โทรออก | ยังไม่ทำ — ต่อยอดได้เพราะแกนกลางแยกจากช่องทางแล้ว |

## ขั้นต่อไป

1. ส่งข้อความเสียงใน Telegram → แปลงเป็นข้อความด้วย Whisper → ส่งให้ `agent.ask()`
2. งานเชิงรุก: ตั้งเวลาให้ agent ตรวจข้อมูล (Google Sheet / ฐานข้อมูลโกดัง) แล้วเรียก `notify_owner`
3. โทรออกเรื่องด่วนผ่าน Twilio
