# main.py — the Spark 上で動く推論ゲートウェイ。
# STT: transformers + PyTorch で Whisper を CUDA 実行。
# MT:  OpenAI 互換 chat endpoint (vLLM / Ollama) に投げて和訳。
#
# Audio path: WSS で受けた webm/opus を ffmpeg のストリーミング subprocess に流し続け、
# stdout から PCM(s16le, 16kHz, mono) を連続で読んでリングバッファに積む。
# その PCM を Silero VAD で発話単位に切り、発話 1 つにつき Whisper を 1 回かける。
# 固定時間窓だと文の途中で切れて訳し漏れが出やすかったため、HANDOVER §9 のとおり
# VAD ベースに移行した。

import asyncio
import os
import re
import httpx
import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from silero_vad import get_speech_timestamps, load_silero_vad
from transformers import pipeline

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_DTYPE = os.getenv("WHISPER_DTYPE", "float16")
PCM_BUFFER_SEC = float(os.getenv("PCM_BUFFER_SEC", "30.0"))  # リングバッファの長さ
SAMPLE_RATE = 16000  # Whisper 入力レート固定。
MAX_PCM_SAMPLES = int(SAMPLE_RATE * PCM_BUFFER_SEC)

# --- VAD パラメータ ---
VAD_TICK_SEC = float(os.getenv("VAD_TICK_SEC", "0.5"))
# 発話終了から「これで一区切り」と判定するまでの待ち時間 (s)
VAD_SETTLE_SEC = float(os.getenv("VAD_SETTLE_SEC", "0.5"))
# Silero の発話確率しきい値 (0.0 〜 1.0)
VAD_THRESHOLD = float(os.getenv("VAD_THRESHOLD", "0.5"))
# 発話を切る最小無音長 (ms)
VAD_MIN_SILENCE_MS = int(os.getenv("VAD_MIN_SILENCE_MS", "400"))
# 発話の最小長 (ms)
VAD_MIN_SPEECH_MS = int(os.getenv("VAD_MIN_SPEECH_MS", "250"))
# 1 発話の最大長 (s)。これより長い発話は完結を待たずチャンクで emit していく。
VAD_MAX_UTTERANCE_SEC = float(os.getenv("VAD_MAX_UTTERANCE_SEC", "8.0"))

# 翻訳段 (vLLM / Ollama の OpenAI 互換エンドポイント)。未設定なら原語のまま返す。
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "")
VLLM_MODEL = os.getenv("VLLM_MODEL", "qwen2.5:7b")
VLLM_TIMEOUT_SEC = float(os.getenv("VLLM_TIMEOUT_SEC", "20.0"))
TRANSLATE_SYSTEM_PROMPT = (
    "あなたは多言語→日本語のライブ字幕翻訳者です。\n"
    "出力ルール（厳守）:\n"
    "- 入力テキストを自然な日本語に訳し、訳文のみを返す。\n"
    "- 出力は日本語のみ。英語・中国語・韓国語などの文字を絶対に混ぜない。\n"
    "- 前置き、説明、注釈、メタコメント、独り言は一切出さない。\n"
    "- 「翻訳できません」「訳文が途中で切れているため」のような断り書きや解説は禁止。\n"
    "- 入力が文の途中で切れていても、その断片を断片のまま自然な日本語で訳す。理由は書かない。\n"
    "- 出力は引用符・括弧の注釈・記号で囲まない。\n"
    "- 入力が既に日本語ならそのまま返す。"
)

TRANSLATE_FEWSHOT = [
    (
        "And the more successful ones spent the majority of their money on management productivity.",
        "そしてより成功した人たちは、大部分のお金を経営の生産性に費やしました。",
    ),
    (
        "He said the lesson from the Gulf War was that the best software will win the war.",
        "彼は、湾岸戦争から得た教訓は、最良のソフトウェアが戦争に勝つということだと述べた。",
    ),
    (
        "I think the best way to predict the future is to invent it.",
        "未来を予測する最良の方法は、それを発明することだと思います。",
    ),
]

# Whisper が短い発話で吐きがちな英語幻聴。VAD で大半防げるが念のため残す。
WHISPER_HALLUCINATIONS = {
    "you", "thank you.", "thanks for watching.", "thanks for watching!",
    ".", "...", "Thank you.", "ご視聴ありがとうございました。",
}

# STT 出力に出てきても訳すに値しない filler。表記揺れを吸えるよう正規化して比較。
STT_FILLERS = {
    "uh", "um", "umm", "uhh", "ah", "ahh", "hmm", "mmm", "mm",
    "oh", "ooh", "huh", "you know", "i mean", "like",
    "ええと", "あの", "うん", "ああ", "うー", "んー", "んーと",
}

# LLM が漏らしがちなメタコメントの目印。これが訳文に含まれていたら原文 fallback。
TRANSLATION_META_MARKERS = (
    "（訳文",
    "（翻訳",
    "（注",
    "(訳文",
    "(翻訳",
    "(注",
    "翻訳できません",
    "訳すことが",
    "原文が",
    "原文は",
    "意味が不明",
    "意味が取れない",
)

app = FastAPI(title="video-overlay-translator gateway")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_DTYPES = {
    "float16": torch.float16,
    "bfloat16": torch.bfloat16,
    "float32": torch.float32,
}

_asr = None
_vad = None


def get_asr():
    global _asr
    if _asr is None:
        _asr = pipeline(
            "automatic-speech-recognition",
            model=WHISPER_MODEL,
            device=WHISPER_DEVICE,
            dtype=_DTYPES.get(WHISPER_DTYPE, torch.float16),
        )
    return _asr


def get_vad():
    """Silero VAD を CPU 上に持つ。GPU は Whisper / LLM のために空けておく。"""
    global _vad
    if _vad is None:
        _vad = load_silero_vad(onnx=False)
        print("[vot] Silero VAD loaded", flush=True)
    return _vad


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "stt_model": WHISPER_MODEL,
        "device": WHISPER_DEVICE,
        "mt_base_url": VLLM_BASE_URL or "(disabled)",
        "mt_model": VLLM_MODEL,
        "segmentation": "vad",
    }


@app.get("/models")
async def list_models():
    if not VLLM_BASE_URL:
        return {"object": "list", "data": []}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{VLLM_BASE_URL.rstrip('/')}/models")
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"object": "list", "data": [], "error": f"{type(e).__name__}: {e}"}


@app.websocket("/translate")
async def translate(ws: WebSocket):
    await ws.accept()
    requested_model = ws.query_params.get("model") or VLLM_MODEL
    print(f"[vot] WS connect: model={requested_model}", flush=True)

    ff = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-loglevel", "error",
        "-fflags", "+discardcorrupt+nobuffer",
        "-f", "matroska", "-i", "pipe:0",
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )

    # PCM リングバッファ + セッション開始からの絶対サンプル数。
    state = {
        "pcm": np.zeros(0, dtype=np.int16),
        "total_samples": 0,   # 受信した全 PCM の長さ（リングで捨てた分も含む）
        "last_emitted_abs": 0,  # この絶対位置より後の発話だけ emit する
    }
    pcm_lock = asyncio.Lock()
    stop = asyncio.Event()

    async def feed_ffmpeg() -> None:
        try:
            while not stop.is_set():
                chunk = await ws.receive_bytes()
                if ff.stdin.is_closing():
                    break
                ff.stdin.write(chunk)
                await ff.stdin.drain()
        except WebSocketDisconnect:
            pass
        except Exception as e:
            print(f"[vot] feed_ffmpeg: {type(e).__name__}: {e}", flush=True)
        finally:
            stop.set()
            try:
                ff.stdin.close()
            except Exception:
                pass

    async def read_pcm() -> None:
        try:
            while not stop.is_set():
                raw = await ff.stdout.read(8192)
                if not raw:
                    break
                arr = np.frombuffer(raw, dtype=np.int16)
                async with pcm_lock:
                    state["pcm"] = np.concatenate([state["pcm"], arr])
                    state["total_samples"] += len(arr)
                    if state["pcm"].size > MAX_PCM_SAMPLES:
                        state["pcm"] = state["pcm"][-MAX_PCM_SAMPLES:]
        except Exception as e:
            print(f"[vot] read_pcm: {type(e).__name__}: {e}", flush=True)
        finally:
            stop.set()

    async def emit_loop() -> None:
        # VAD を初回利用前にロード（最初の発話までのレイテンシを減らす）。
        await asyncio.to_thread(get_vad)
        settle_samples = int(VAD_SETTLE_SEC * SAMPLE_RATE)
        max_utt_samples = int(VAD_MAX_UTTERANCE_SEC * SAMPLE_RATE)
        try:
            while not stop.is_set():
                await asyncio.sleep(VAD_TICK_SEC)
                async with pcm_lock:
                    if state["pcm"].size < SAMPLE_RATE:
                        continue
                    buf = state["pcm"].copy()
                    buf_abs_start = state["total_samples"] - len(buf)
                    last_emitted_abs = state["last_emitted_abs"]

                audio = buf.astype(np.float32) / 32768.0
                segments = await asyncio.to_thread(_vad_segments, audio)

                for seg in segments:
                    abs_seg_start = buf_abs_start + seg["start"]
                    abs_seg_end = buf_abs_start + seg["end"]
                    if abs_seg_end <= last_emitted_abs:
                        continue

                    # この発話のうち、まだ emit していない範囲。
                    slice_abs_start = max(abs_seg_start, last_emitted_abs)
                    available = abs_seg_end - slice_abs_start
                    # 発話末尾に settle 分の余白があれば「完結」と判定。
                    trailing_silence = len(buf) - seg["end"]
                    is_complete = trailing_silence >= settle_samples

                    if available >= max_utt_samples:
                        # 長い発話は完結を待たず、max 単位で順次 emit。
                        slice_abs_end = slice_abs_start + max_utt_samples
                    elif is_complete:
                        # 短〜中尺かつ完結したら残り全部 emit。
                        slice_abs_end = abs_seg_end
                    else:
                        # まだ伸びる可能性があり、max にも届かない → 待つ。
                        continue

                    s_start = max(0, slice_abs_start - buf_abs_start)
                    s_end = min(len(buf), slice_abs_end - buf_abs_start)
                    if s_end - s_start < int(SAMPLE_RATE * 0.3):
                        last_emitted_abs = slice_abs_end
                        async with pcm_lock:
                            state["last_emitted_abs"] = last_emitted_abs
                        continue

                    audio_slice = audio[s_start:s_end]
                    text = await asyncio.to_thread(transcribe_pcm, audio_slice)
                    last_emitted_abs = slice_abs_end
                    async with pcm_lock:
                        state["last_emitted_abs"] = last_emitted_abs
                    if not text:
                        continue
                    ja = await translate_to_ja(text, requested_model)
                    if not ja:
                        continue
                    # 訳文は文単位に分割して順次送り、クライアント側の読書時間を確保する。
                    for sentence in _split_sentences(ja):
                        try:
                            await ws.send_json({"status": "final", "text": sentence})
                        except Exception:
                            stop.set()
                            break
                    if stop.is_set():
                        break
        except Exception as e:
            print(f"[vot] emit_loop: {type(e).__name__}: {e}", flush=True)
        finally:
            stop.set()

    tasks = [
        asyncio.create_task(feed_ffmpeg()),
        asyncio.create_task(read_pcm()),
        asyncio.create_task(emit_loop()),
    ]
    try:
        await stop.wait()
    finally:
        for t in tasks:
            t.cancel()
        try:
            ff.terminate()
            await asyncio.wait_for(ff.wait(), timeout=2.0)
        except Exception:
            try:
                ff.kill()
            except Exception:
                pass


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。！？])")


def _split_sentences(text: str) -> list[str]:
    """日本語訳文を句点単位で分割。1 字幕が長くなりすぎないように。"""
    parts = [p.strip() for p in _SENTENCE_BOUNDARY.split(text)]
    return [p for p in parts if p]


def _vad_segments(audio: np.ndarray) -> list[dict]:
    """音声から発話区間を取り出して [{"start": int, "end": int}, ...] で返す。"""
    model = get_vad()
    audio_t = torch.from_numpy(audio)
    return get_speech_timestamps(
        audio_t,
        model,
        sampling_rate=SAMPLE_RATE,
        threshold=VAD_THRESHOLD,
        min_silence_duration_ms=VAD_MIN_SILENCE_MS,
        min_speech_duration_ms=VAD_MIN_SPEECH_MS,
        return_seconds=False,
    )


def transcribe_pcm(audio: np.ndarray) -> str:
    """VAD で切り出した 1 発話分の PCM を Whisper で転写。"""
    if audio.size < int(SAMPLE_RATE * 0.2):
        return ""
    asr = get_asr()
    result = asr({"array": audio, "sampling_rate": SAMPLE_RATE})
    text = (result.get("text") or "").strip()
    # 句読点を剥がして単語ベースで filler 判定。
    bare = text.lower().rstrip(".,!?！？。、").strip()
    if bare in {h.lower() for h in WHISPER_HALLUCINATIONS}:
        return ""
    if bare in STT_FILLERS:
        return ""
    # 短すぎる出力は filler の可能性が高く、字幕表示の邪魔になるだけ。
    if len(bare) < 3:
        return ""
    return text


async def translate_to_ja(text: str, model: str) -> str:
    """OpenAI 互換 chat endpoint に投げて和訳。失敗時は原文をそのまま返す。"""
    if not VLLM_BASE_URL:
        return text
    messages = [{"role": "system", "content": TRANSLATE_SYSTEM_PROMPT}]
    for src, tgt in TRANSLATE_FEWSHOT:
        messages.append({"role": "user", "content": src})
        messages.append({"role": "assistant", "content": tgt})
    messages.append({"role": "user", "content": text})
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": 512,
    }
    if model.startswith("qwen3"):
        payload["think"] = False
    try:
        async with httpx.AsyncClient(timeout=VLLM_TIMEOUT_SEC) as client:
            resp = await client.post(
                f"{VLLM_BASE_URL.rstrip('/')}/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            ja = (data["choices"][0]["message"].get("content") or "").strip()
            if not ja:
                print(
                    f"[vot] empty content from {model} (finish_reason={data['choices'][0].get('finish_reason')})",
                    flush=True,
                )
                return text
            # メタコメント・断り書きが混入した場合は字幕に出さない。
            if any(marker in ja for marker in TRANSLATION_META_MARKERS):
                print(f"[vot] meta leak: {ja[:80]}", flush=True)
                return ""
            return ja
    except Exception as e:
        print(f"[vot] translate_to_ja failed: {type(e).__name__}: {e}", flush=True)
        return text


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
