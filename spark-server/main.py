# main.py — the Spark 上で動く推論ゲートウェイ。
# STT: transformers + PyTorch で Whisper を CUDA 実行。
# MT:  OpenAI 互換 chat endpoint (vLLM / Ollama) に投げて和訳。
#
# Audio path: WSS で受けた webm/opus を ffmpeg のストリーミング subprocess に流し続け、
# stdout から PCM(s16le, 16kHz, mono) を連続で読んでリングバッファに積む。
# 一定周期で最新の数秒だけを取り出して Whisper にかけることで、セッションが長くなっても
# 計算量が線形に伸びない。

import asyncio
import os
import httpx
import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from transformers import pipeline

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_DTYPE = os.getenv("WHISPER_DTYPE", "float16")
STT_WINDOW_SEC = float(os.getenv("STT_WINDOW_SEC", "5.0"))     # 何秒ごとに転写を発火するか
STT_CONTEXT_SEC = float(os.getenv("STT_CONTEXT_SEC", "8.0"))   # 1回の転写で渡す PCM の長さ
PCM_BUFFER_SEC = float(os.getenv("PCM_BUFFER_SEC", "30.0"))    # リングバッファの長さ
SAMPLE_RATE = 16000  # Whisper 入力レート固定。
MAX_PCM_SAMPLES = int(SAMPLE_RATE * PCM_BUFFER_SEC)
CONTEXT_SAMPLES = int(SAMPLE_RATE * STT_CONTEXT_SEC)

# 翻訳段 (vLLM / Ollama の OpenAI 互換エンドポイント)。未設定なら原語のまま返す。
VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "")  # 例: http://127.0.0.1:11434/v1
VLLM_MODEL = os.getenv("VLLM_MODEL", "qwen2.5:7b")
VLLM_TIMEOUT_SEC = float(os.getenv("VLLM_TIMEOUT_SEC", "20.0"))
TRANSLATE_SYSTEM_PROMPT = (
    "あなたは多言語→日本語のライブ字幕翻訳者です。\n"
    "出力ルール（厳守）:\n"
    "- 入力テキストを自然な日本語に訳し、訳文のみを返す。\n"
    "- 出力は日本語のみ。英語・中国語・韓国語などの文字を絶対に混ぜない。\n"
    "- 前置き、説明、注釈、引用符、メタコメント、独り言は一切付けない。\n"
    "- 入力が文の途中で切れていても、原文以外の文字を生成しない。\n"
    "- 入力が既に日本語ならそのまま返す。"
)

# Few-shot 例。小型モデル (Qwen2.5-7B 等) は出力フォーマットの一貫性に弱いので、
# in-context で「英語のまま漏らさず・中国語に滑らず・メタコメント無し」を体得させる。
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

app = FastAPI(title="video-overlay-translator gateway")

# 拡張 (chrome-extension://) から /models を読みに来るので CORS を開けておく。
# Tailscale 内向きの secure context なので allow_origins=* で実害なし。
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

# Lazy load: import が GPU 初期化で固まらないように初回リクエスト時に持つ。
_asr = None


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


@app.get("/healthz")
def healthz():
    return {
        "ok": True,
        "stt_model": WHISPER_MODEL,
        "device": WHISPER_DEVICE,
        "mt_base_url": VLLM_BASE_URL or "(disabled)",
        "mt_model": VLLM_MODEL,
    }


@app.get("/models")
async def list_models():
    """Ollama / vLLM の /v1/models をそのままプロキシ。拡張のオプションでドロップダウン化する。"""
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
    # 接続ごとのモデル切替を許す。?model=qwen2.5:14b 等。未指定は env デフォルト。
    requested_model = ws.query_params.get("model") or VLLM_MODEL

    # ffmpeg を streaming で起動。stdin に webm を流し続け、stdout から PCM が出続ける。
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

    pcm = np.zeros(0, dtype=np.int16)
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
            print(f"[vot] feed_ffmpeg: {type(e).__name__}: {e}")
        finally:
            stop.set()
            try:
                ff.stdin.close()
            except Exception:
                pass

    async def read_pcm() -> None:
        nonlocal pcm
        try:
            while not stop.is_set():
                raw = await ff.stdout.read(8192)
                if not raw:
                    break
                arr = np.frombuffer(raw, dtype=np.int16)
                async with pcm_lock:
                    pcm = np.concatenate([pcm, arr])
                    if pcm.size > MAX_PCM_SAMPLES:
                        pcm = pcm[-MAX_PCM_SAMPLES:]
        except Exception as e:
            print(f"[vot] read_pcm: {type(e).__name__}: {e}")
        finally:
            stop.set()

    async def emit_loop() -> None:
        try:
            while not stop.is_set():
                await asyncio.sleep(STT_WINDOW_SEC)
                async with pcm_lock:
                    if pcm.size < SAMPLE_RATE:  # <1秒分しか無ければ待つ
                        continue
                    buf = pcm[-CONTEXT_SAMPLES:].copy()
                audio = buf.astype(np.float32) / 32768.0
                text = await asyncio.to_thread(transcribe_pcm, audio)
                if not text:
                    continue
                ja = await translate_to_ja(text, requested_model)
                try:
                    await ws.send_json({"status": "final", "text": ja})
                except Exception:
                    break
        except Exception as e:
            print(f"[vot] emit_loop: {type(e).__name__}: {e}")
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


SILENCE_RMS_THRESHOLD = float(os.getenv("SILENCE_RMS_THRESHOLD", "0.005"))
# Whisper が無音/雑音から吐きがちな幻聴。これだけの出力は捨てる。
WHISPER_HALLUCINATIONS = {
    "you", "thank you.", "thanks for watching.", "thanks for watching!",
    ".", "...", "Thank you.", "ご視聴ありがとうございました。",
}


def transcribe_pcm(audio: np.ndarray) -> str:
    """短い PCM (<= STT_CONTEXT_SEC 秒) を Whisper で転写。

    無音/雑音窓は Whisper が ``you`` などの幻聴を吐くため、RMS で早期に切る。
    出力テキストも known-hallucination リストと突き合わせて捨てる。
    """
    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    if rms < SILENCE_RMS_THRESHOLD:
        return ""
    asr = get_asr()
    result = asr({"array": audio, "sampling_rate": SAMPLE_RATE})
    text = (result.get("text") or "").strip()
    if text.lower() in {h.lower() for h in WHISPER_HALLUCINATIONS}:
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
    try:
        async with httpx.AsyncClient(timeout=VLLM_TIMEOUT_SEC) as client:
            resp = await client.post(
                f"{VLLM_BASE_URL.rstrip('/')}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 256,
                },
            )
            resp.raise_for_status()
            ja = resp.json()["choices"][0]["message"]["content"].strip()
            return ja or text
    except Exception as e:
        print(f"[vot] translate_to_ja failed: {type(e).__name__}: {e}")
        return text


if __name__ == "__main__":
    import uvicorn
    # tailscale serve が 443 で WSS 終端し、ここへは 127.0.0.1 でフォワードする前提。
    # 他NIC・Tailscale 直への平文露出を塞ぐためループバックに絞る。
    uvicorn.run(app, host="127.0.0.1", port=8000)
