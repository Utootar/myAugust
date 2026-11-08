# myAugust — AI agent ส่วนตัว

ผู้ช่วยที่คุยได้ทุกเรื่อง สั่งงานได้ (ค้นเว็บ อ่าน/แก้ไฟล์ รันคำสั่ง จดบันทึก แจ้งเตือน)
สร้างด้วย **Claude Agent SDK** (Python) รันบนเซิร์ฟเวอร์ที่เปิดตลอด แล้วคุยผ่านมือถือทาง Telegram

```
assistant/
  core.py          สมองของ agent: จำบทสนทนา + ระบบขออนุญาต + system prompt
  tools.py         เครื่องมือที่เราเขียนเอง (เวลา, จดบันทึก, แจ้งเตือน) — เพิ่มได้ที่นี่
  cli.py           คุยผ่านเทอร์มินัล (ใช้ทดสอบ)
  telegram_bot.py  คุยผ่าน Telegram (ใช้งานจริง)
  voice.py         พูดคุยด้วยเสียงต่อเนื่อง ชื่อ "ออกัส" (ดูหัวข้อโหมดเสียง)
```

## เริ่มใช้

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# ติดตั้ง Claude Code (ต้องมี Node.js) แล้วล็อกอินด้วยบัญชี Pro ครั้งเดียว
npm install -g @anthropic-ai/claude-code
claude                      # พิมพ์ /login แล้วทำตามหน้าจอ จากนั้นออกด้วย /exit

python -m assistant.cli     # ทดสอบด้วยการพิมพ์คุย (ยังไม่ต้องต่อ Telegram)
```

ถ้าใช้ API key แทน Pro: `cp .env.example .env` ใส่ `ANTHROPIC_API_KEY` แล้ว `export` ก่อนรัน
agent ทำงานในโฟลเดอร์ `./workspace` เท่านั้นโดยค่าเริ่มต้น

## โหมดเสียง (ออกัส)

```
pip install -r requirements-voice.txt     # ครั้งแรกจะโหลดโมเดลฟังเสียงเพิ่ม (small ≈ 500 MB)
python -m assistant.voice --devices       # ดูรายชื่อไมค์ (ถ้าต้องเลือก: set MIC_DEVICE=เลขอุปกรณ์)
python -m assistant.voice
```

ทดสอบเป็นขั้นๆ ด้วยตัวแปร `WAKE_MODE`:

| ค่า | ทำงานอย่างไร |
|---|---|
| `always` (ค่าเริ่มต้น) | ฟังตลอด ทุกประโยคส่งให้ออกัส — ใช้ทดสอบไมค์/ลำโพง/การฟังไทย |
| `name` | ตอบเมื่อได้ยินคำว่า "ออกัส"/"August" (ตรวจจากข้อความที่ถอดได้ ไม่ต้องมีคีย์) |
| `porcupine` | จับ "Hey August" ด้วย Porcupine — ตั้ง `PICOVOICE_ACCESS_KEY` และ `WAKE_PPN=path\to\file.ppn` |

Windows (PowerShell): `$env:WAKE_MODE="name"; python -m assistant.voice`
Windows (cmd): `set WAKE_MODE=name` แล้วรัน

- ผลัดกันพูด: ระหว่างออกัสคิด/พูด ไมค์จะปิด (กันเสียงตัวเองย้อนเข้าไมค์) กดปุ่มใดก็ได้เพื่อให้หยุดพูด
- ปรับได้: `WHISPER_MODEL` (tiny/base/small/medium), `TTS_VOICE` (`th-TH-NiwatNeural` = เสียงชาย), `VAD_LEVEL` 0-3, `END_SILENCE_MS`

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
