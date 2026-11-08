"""โหมดเสียง: พูดคุยกับออกัสต่อเนื่อง ไม่ต้องกด Enter

รัน:  python -m assistant.voice            (ดูรายการไมค์: python -m assistant.voice --devices)
      ตรวจระบบเสียงทีละส่วน:  python -m assistant.voice --check

โหมดคำปลุก (ตั้งด้วยตัวแปร WAKE_MODE):
  always     ฟังตลอด ทุกประโยคที่พูดส่งให้ออกัส            (ค่าเริ่มต้น — ใช้ทดสอบไมค์/ลำโพง)
  name       ตอบเมื่อได้ยินชื่อ "ออกัส"/"August" ในประโยค (ใช้ Whisper ตรวจ ไม่ต้องมีคีย์)
  porcupine  ใช้ Porcupine จับ "Hey August" (ต้องมี PICOVOICE_ACCESS_KEY และ WAKE_PPN)

ลำโพง+ไมค์ในเครื่องเดียวกันจะได้ยินเสียงตัวเอง จึงทำแบบ "ผลัดกันพูด":
ระหว่างออกัสคิด/พูด ระบบจะปิดไมค์ แล้วเปิดใหม่เมื่อพูดจบ
กดปุ่มใดก็ได้บนคีย์บอร์ด (Windows) เพื่อให้ออกัสหยุดพูด
"""
from __future__ import annotations

import asyncio
import collections
import faulthandler
import io
import os
import queue
import re
import sys
import threading
import time
from typing import Any, Callable

import numpy as np

from .core import Agent

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 480 ตัวอย่างต่อเฟรม
TTS_RATE = 24000

WAKE_MODE = os.getenv("WAKE_MODE", "always").lower()
CONV_WINDOW = float(os.getenv("CONV_WINDOW", "15"))  # วินาที: คุยต่อได้โดยไม่ต้องเรียกชื่อซ้ำ
END_SILENCE_MS = int(os.getenv("END_SILENCE_MS", "900"))  # เงียบนานเท่านี้ถือว่าพูดจบ
MAX_UTTERANCE_S = 25

VOICE_PROMPT = """\
ตอนนี้ผู้ใช้คุยกับคุณด้วยเสียง คำตอบของคุณจะถูกอ่านออกเสียง จึงต้อง:
- ตอบสั้น 1-3 ประโยค เป็นภาษาพูดธรรมชาติ
- ห้ามใช้ markdown ตาราง รายการหัวข้อ โค้ด หรืออีโมจิ
- ถ้างานยาว ให้สรุปสั้นๆ แล้วถามว่าต้องการรายละเอียดไหม
- ข้อความของผู้ใช้มาจากการถอดเสียง อาจมีคำผิด ให้เดาความหมายที่น่าจะเป็น
"""

# คำที่ Whisper มักแต่งขึ้นเองเมื่อเจอเสียงเงียบ/เสียงรบกวน
HALLUCINATIONS = re.compile(
    r"ขอบคุณที่(รับชม|ติดตาม)|กดติดตาม|กดไลค์|subscribe|thanks? for watching|ซับไตเติ้ล|subtitles? by",
    re.IGNORECASE,
)
WAKE_VARIANTS = ["ออกัส", "ออกัสต์", "ออกัท", "ออกัต", "ออกัด", "august", "ออกุส", "ออกอส"]


def has_wake_name(text: str) -> bool:
    t = text.lower().replace(" ", "")
    return any(w in t for w in WAKE_VARIANTS)


def split_for_tts(text: str, first_max: int = 80, max_len: int = 180) -> list[str]:
    """ตัดข้อความเป็นท่อนสั้นๆ เพื่อให้เริ่มพูดท่อนแรกได้เร็ว"""
    text = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", text)  # อีโมจิ
    text = re.sub(r"[*_`#>|]+", "", text)  # เครื่องหมาย markdown
    text = re.sub(r"(?m)^\s*[-•]\s+", "", text)  # จุดนำหน้ารายการ
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    chunks: list[str] = []
    cur = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        limit = first_max if not chunks else max_len
        if len(cur) + len(part) + 1 <= limit:
            cur = f"{cur} {part}".strip()
            continue
        if cur:
            chunks.append(cur)
        while len(part) > (first_max if not chunks else max_len):
            lim = first_max if not chunks else max_len
            cut = part.rfind(" ", 0, lim)
            cut = cut if cut > lim // 3 else lim  # ภาษาไทยไม่มีจุด ตัดที่เว้นวรรคใกล้สุด
            chunks.append(part[:cut].strip())
            part = part[cut:].strip()
        cur = part
    if cur:
        chunks.append(cur)
    return chunks


def decode_mp3(data: bytes) -> np.ndarray:
    """mp3 -> float32 mono 24 kHz (ใช้ PyAV ที่มากับ faster-whisper)"""
    import av

    out: list[np.ndarray] = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TTS_RATE)
        for frame in container.decode(audio=0):
            out += [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
        out += [f.to_ndarray().reshape(-1) for f in (resampler.resample(None) or [])]
    if not out:
        return np.zeros(0, np.float32)
    return np.concatenate(out).astype(np.float32) / 32768


# ---------------------------------------------------------------- ไมค์ + จับช่วงที่พูด


class Mic:
    def __init__(self, device: int | str | None = None) -> None:
        import sounddevice as sd

        self._q: queue.Queue[bytes] = queue.Queue()
        self._open = threading.Event()
        self._open.set()
        self._stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=FRAME_SAMPLES, device=device, callback=self._callback,
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        if self._open.is_set():
            self._q.put(bytes(indata))

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()

    def flush(self) -> None:
        while not self._q.empty():
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    def mute(self) -> None:
        self._open.clear()
        self.flush()

    def unmute(self) -> None:
        self.flush()
        self._open.set()

    def read(self, timeout: float = 1.0) -> bytes | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


def listen_utterance(mic: Any, vad: Any, idle_timeout: float | None = None) -> np.ndarray | None:
    """รอจนมีคนพูดแล้วพูดจบ คืนเสียงที่พูด (float32, 16 kHz) หรือ None ถ้าไม่มีใครพูดภายใน idle_timeout"""
    pre: collections.deque = collections.deque(maxlen=10)  # ~300ms ก่อนเริ่มพูด
    post: collections.deque = collections.deque(maxlen=max(1, END_SILENCE_MS // FRAME_MS))
    collected: list[bytes] = []
    triggered = False
    started = time.monotonic()
    while True:
        frame = mic.read(timeout=0.5)
        if frame is None:
            if not triggered and idle_timeout is not None and time.monotonic() - started > idle_timeout:
                return None
            continue
        speech = vad.is_speech(frame, SAMPLE_RATE)
        if not triggered:
            pre.append((frame, speech))
            if len(pre) == pre.maxlen and sum(s for _, s in pre) > 0.8 * pre.maxlen:
                triggered = True
                collected = [f for f, _ in pre]
                pre.clear()
            elif idle_timeout is not None and time.monotonic() - started > idle_timeout:
                return None
        else:
            collected.append(frame)
            post.append(speech)
            if len(post) == post.maxlen and sum(not s for s in post) > 0.9 * post.maxlen:
                break
            if len(collected) * FRAME_MS / 1000 > MAX_UTTERANCE_S:
                break
    return np.frombuffer(b"".join(collected), np.int16).astype(np.float32) / 32768


# ---------------------------------------------------------------- เสียงเป็นข้อความ


class STT:
    def __init__(self) -> None:
        from faster_whisper import WhisperModel

        name = os.getenv("WHISPER_MODEL", "small")
        print(f"โหลดโมเดลฟังเสียง '{name}' (ครั้งแรกจะดาวน์โหลด อาจใช้เวลา)...")
        self.model = WhisperModel(name, device="auto", compute_type="int8")
        self.lang = os.getenv("STT_LANG", "th")
        self.prompt = os.getenv(
            "STT_PROMPT",
            "บทสนทนาภาษาไทย ผู้ช่วยชื่อ ออกัส (August) ปนคำอังกฤษเช่น email, meeting, stock",
        )

    def transcribe(self, audio: np.ndarray) -> str:
        segments, _ = self.model.transcribe(
            audio, language=self.lang, beam_size=1, condition_on_previous_text=False,
            initial_prompt=self.prompt, vad_filter=False,
        )
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.6).strip()
        return "" if HALLUCINATIONS.search(text) else text


# ---------------------------------------------------------------- ข้อความเป็นเสียง


def _key_pressed() -> bool:
    if sys.platform == "win32":
        import msvcrt

        if msvcrt.kbhit():
            msvcrt.getwch()
            return True
    return False


def _play(audio: np.ndarray) -> bool:
    """เล่นเสียง คืน True ถ้าผู้ใช้กดปุ่มหยุด"""
    import sounddevice as sd

    sd.play(audio, TTS_RATE)
    end = time.monotonic() + len(audio) / TTS_RATE + 0.2
    while time.monotonic() < end:
        if _key_pressed():
            sd.stop()
            return True
        time.sleep(0.03)
    sd.stop()
    return False


def beep() -> None:
    import sounddevice as sd

    t = np.linspace(0, 0.12, int(TTS_RATE * 0.12), False)
    sd.play((0.25 * np.sin(2 * np.pi * 880 * t)).astype(np.float32), TTS_RATE)
    sd.wait()


class TTS:
    def __init__(self) -> None:
        self.voice = os.getenv("TTS_VOICE", "th-TH-PremwadeeNeural")  # ชาย: th-TH-NiwatNeural

    async def synth(self, text: str) -> np.ndarray:
        import edge_tts

        mp3 = b""
        async for chunk in edge_tts.Communicate(text, self.voice).stream():
            if chunk["type"] == "audio":
                mp3 += chunk["data"]
        return await asyncio.to_thread(decode_mp3, mp3)

    async def speak(self, text: str, on_start: Callable[[], None] | None = None) -> None:
        chunks = split_for_tts(text)
        if not chunks:
            return
        nxt = asyncio.create_task(self.synth(chunks[0]))
        for i in range(len(chunks)):
            try:
                audio = await nxt
            except Exception as e:  # เน็ตหลุด ฯลฯ — ไม่ให้โปรแกรมล้ม
                print(f"[พูดไม่ได้: {e}]")
                return
            if i == 0 and on_start:
                on_start()
            if i + 1 < len(chunks):
                nxt = asyncio.create_task(self.synth(chunks[i + 1]))  # สร้างท่อนถัดไประหว่างที่กำลังพูด
            if await asyncio.to_thread(_play, audio):
                if i + 1 < len(chunks):
                    nxt.cancel()
                return


# ---------------------------------------------------------------- คำปลุก Porcupine


class PorcupineWake:
    def __init__(self, mic: Mic) -> None:
        import pvporcupine

        self.mic = mic
        self.p = pvporcupine.create(
            access_key=os.environ["PICOVOICE_ACCESS_KEY"],
            keyword_paths=[os.environ["WAKE_PPN"]],
        )

    def wait(self) -> None:
        buf = np.empty(0, np.int16)
        n = self.p.frame_length
        while True:
            frame = self.mic.read(timeout=1.0)
            if frame is None:
                continue
            buf = np.concatenate([buf, np.frombuffer(frame, np.int16)])
            while len(buf) >= n:
                chunk, buf = buf[:n], buf[n:]
                if self.p.process(chunk.tolist()) >= 0:
                    return


# ---------------------------------------------------------------- ตรวจระบบ


async def self_check(mic_dev: int | str | None) -> None:
    import sounddevice as sd

    rec = None
    steps = ["ลำโพง", "เสียงพูด", "ไมค์", "ฟังเสียง"]
    for i, name in enumerate(steps, 1):
        print(f"\n[{i}/4] {name}")
        try:
            if name == "ลำโพง":
                print("  ควรได้ยินเสียงบี๊บ")
                beep()
            elif name == "เสียงพูด":
                t0 = time.monotonic()
                audio = await TTS().synth("สวัสดีครับ ผมออกัส ทดสอบเสียง")
                print(f"  สร้างเสียงใช้เวลา {time.monotonic() - t0:.1f} วินาที กำลังเล่น...")
                _play(audio)
            elif name == "ไมค์":
                print("  พูดอะไรก็ได้ 4 วินาที... เริ่ม!")
                rec = sd.rec(4 * SAMPLE_RATE, samplerate=SAMPLE_RATE, channels=1, dtype="float32", device=mic_dev)
                sd.wait()
                level = float(np.sqrt(np.mean(rec**2)))
                print(f"  ระดับเสียง {level:.4f}", "(เบามาก ไมค์อาจไม่ได้ยิน/เลือกผิดตัว)" if level < 0.003 else "(ได้ยิน)")
            elif name == "ฟังเสียง" and rec is not None:
                text = STT().transcribe(rec[:, 0])
                print(f"  ถอดได้ว่า: {text or '(ไม่ได้ยินคำพูด)'}")
            print("  ✓ ผ่าน")
        except Exception as e:
            print(f"  ✗ ไม่ผ่าน: {type(e).__name__}: {e}")


# ---------------------------------------------------------------- ลูปหลัก


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # ให้ Windows แสดงภาษาไทยได้

    faulthandler.enable()  # ถ้าโปรแกรมล่มแบบไม่มี error จะพิมพ์จุดที่ล่มให้เห็น
    import webrtcvad

    mic_dev = os.getenv("MIC_DEVICE")
    mic_dev = int(mic_dev) if mic_dev and mic_dev.isdigit() else mic_dev
    if "--check" in sys.argv:
        await self_check(mic_dev)
        return
    if "--devices" in sys.argv:
        import sounddevice as sd

        print(sd.query_devices())
        return

    mic = Mic(mic_dev)
    vad = webrtcvad.Vad(int(os.getenv("VAD_LEVEL", "2")))  # 0 ไวน้อย - 3 ไวมาก
    stt, tts = STT(), TTS()
    wake = PorcupineWake(mic) if WAKE_MODE == "porcupine" else None

    async def confirm(description: str) -> bool:
        print(f"\n⚠️  ออกัสขออนุญาต: {description}")
        await tts.speak("ขออนุญาตทำงานบางอย่างครับ ดูที่หน้าจอแล้วพิมพ์ y หรือ n")
        answer = await asyncio.to_thread(input, "อนุญาตไหม? [y/N] ")
        return answer.strip().lower() in ("y", "yes", "ใช่")

    # โหมดเสียงต้องตอบไว: ใช้ Haiku + คิดน้อย (เปลี่ยนได้ด้วย VOICE_MODEL / VOICE_EFFORT)
    agent = Agent(
        confirm=confirm,
        extra_prompt=VOICE_PROMPT,
        model=os.getenv("VOICE_MODEL", "claude-haiku-5-5"),
        effort=os.getenv("VOICE_EFFORT", "low"),
    )
    print("กำลังเชื่อมต่อ Claude...")
    await agent.start()
    mic.start()
    print(f"โหมดคำปลุก: {WAKE_MODE} | กด Ctrl+C เพื่อออก | กดปุ่มใดๆ ระหว่างที่ออกัสพูด = หยุดพูด")

    active_until = 0.0
    try:
        mic.mute()
        print("กำลังพูดทักทาย...")
        await tts.speak("ออกัสพร้อมแล้วครับ")
        mic.unmute()
        while True:
            now = time.monotonic()
            if wake and now >= active_until:
                print("…รอคำว่า Hey August")
                await asyncio.to_thread(wake.wait)
                await asyncio.to_thread(beep)
                mic.flush()
                active_until = time.monotonic() + CONV_WINDOW
            idle = max(0.5, active_until - time.monotonic()) if wake else None
            print("🎤 กำลังฟัง... พูดได้เลย")
            audio = await asyncio.to_thread(listen_utterance, mic, vad, idle)
            if audio is None:
                continue
            # เวลาที่เริ่มพูด (ไม่ใช่ตอนถอดเสียงเสร็จ) ใช้ตัดสินว่ายังอยู่ในช่วงคุยต่อไหม
            spoke_at = time.monotonic() - len(audio) / SAMPLE_RATE
            print("…กำลังถอดเสียง")
            t0 = time.monotonic()
            text = await asyncio.to_thread(stt.transcribe, audio)
            t_stt = time.monotonic() - t0
            if not text:
                print("(ไม่ได้ยินเป็นคำพูด ข้ามไป)")
                continue
            print(f"คุณ: {text}")
            if WAKE_MODE == "name" and spoke_at > active_until and not has_wake_name(text):
                print("(ไม่ได้เรียกชื่อออกัส ข้ามไป)")
                continue
            mic.mute()
            print("…ออกัสกำลังคิด")
            t0 = time.monotonic()
            reply = await agent.ask(text)
            t_llm = time.monotonic() - t0
            print(f"ออกัส: {reply}")
            t0 = time.monotonic()
            await tts.speak(reply, on_start=lambda: print(
                f"⏱ ถอดเสียง {t_stt:.1f}s | Claude {t_llm:.1f}s | สร้างเสียง {time.monotonic() - t0:.1f}s\n"))
            mic.unmute()
            active_until = time.monotonic() + CONV_WINDOW
    except Exception as e:
        print(f"\n✗ เกิดข้อผิดพลาด: {type(e).__name__}: {e}")
        raise
    finally:
        print("กำลังปิด...")
        mic.stop()
        await agent.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nปิดออกัสแล้ว (Ctrl+C)")
