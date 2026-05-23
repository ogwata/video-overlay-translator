# main.py — the Spark 上で動く推論ゲートウェイ。
# STT 段: transformers + PyTorch で Whisper を CUDA 実行。
# 翻訳段(vLLM/Qwen) は次ステージで差し込む。
#
# faster-whisper(CTranslate2) の aarch64 wheel に CUDA が同梱されておらず、
# DGX Spark (Grace ARM64) では GPU を引けないため、transformers パイプラインに移行。

import asyncio
import os
import subprocess
import numpy as np
import torch
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from transformers import pipeline

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "openai/whisper-large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_DTYPE = os.getenv("WHISPER_DTYPE", "float16")
STT_WINDOW_SEC = float(os.getenv("STT_WINDOW_SEC", "5.0"))
SAMPLE_RATE = 16000  # Whisper 入力レート固定。

app = FastAPI(title="video-overlay-translator gateway")

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
            torch_dtype=_DTYPES.get(WHISPER_DTYPE, torch.float16),
        )
    return _asr


@app.get("/healthz")
def healthz():
    return {"ok": True, "model": WHISPER_MODEL, "device": WHISPER_DEVICE}


@app.websocket("/translate")
async def translate(ws: WebSocket):
    await ws.accept()
    chunks = bytearray()
    loop = asyncio.get_running_loop()
    last_emit_at = loop.time()
    last_seg_end = 0.0  # これまで返したセグメントの最大 end (秒)

    try:
        while True:
            chunk = await ws.receive_bytes()
            chunks.extend(chunk)
            now = loop.time()
            if now - last_emit_at < STT_WINDOW_SEC:
                continue
            new_text, last_seg_end = await asyncio.to_thread(
                transcribe_new, bytes(chunks), last_seg_end
            )
            if new_text:
                # TODO(MT): vLLM(Qwen) で和訳。今は原語のまま返す。
                # TODO: interim/final 2段化 (HANDOVER 9)。今は final 一本。
                await ws.send_json({"status": "final", "text": new_text})
            last_emit_at = now
    except WebSocketDisconnect:
        pass


def transcribe_new(webm_bytes: bytes, since_end: float) -> tuple[str, float]:
    """蓄積した webm 全体を PCM へデコード→転写し、since_end より後だけ返す。

    再デコードは無駄が大きいが、まずは結線確認のための素直な実装。
    長時間で遅くなったら ffmpeg ストリーム + PCM リングバッファに移行する。
    """
    if not webm_bytes:
        return "", since_end
    audio = webm_to_pcm(webm_bytes)
    if audio.size < SAMPLE_RATE:  # 1秒未満ならスキップ
        return "", since_end
    asr = get_asr()
    result = asr(
        {"array": audio, "sampling_rate": SAMPLE_RATE},
        chunk_length_s=30,    # 内部チャンキング。長音声でも OOM しない。
        batch_size=8,
        return_timestamps=True,
    )
    new_parts: list[str] = []
    new_end = since_end
    for ch in result.get("chunks", []):
        start, end = ch.get("timestamp", (None, None))
        if start is None:
            continue
        if start >= since_end:
            t = (ch.get("text") or "").strip()
            if t:
                new_parts.append(t)
            if end is not None:
                new_end = max(new_end, end)
    return " ".join(new_parts), new_end


def webm_to_pcm(webm_bytes: bytes) -> np.ndarray:
    """webm/opus を mono 16kHz float32 PCM に ffmpeg で変換。"""
    proc = subprocess.run(
        [
            "ffmpeg", "-loglevel", "error",
            "-f", "webm", "-i", "pipe:0",
            "-ac", "1", "-ar", str(SAMPLE_RATE),
            "-f", "s16le", "pipe:1",
        ],
        input=webm_bytes, capture_output=True, check=False,
    )
    if proc.returncode != 0:
        print(f"[vot] ffmpeg failed: {proc.stderr[:500].decode('utf-8', errors='replace')}")
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


if __name__ == "__main__":
    import uvicorn
    # tailscale serve が 443 で WSS 終端し、ここへは 127.0.0.1 でフォワードする前提。
    # 他NIC・Tailscale 直への平文露出を塞ぐためループバックに絞る。
    uvicorn.run(app, host="127.0.0.1", port=8000)
