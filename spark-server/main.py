# main.py — the Spark 上で動く推論ゲートウェイ。
# STT 段: faster-whisper(CUDA) で原語テキストを返す。
# 翻訳段(vLLM/Qwen) は次ステージで差し込む。

import asyncio
import os
import tempfile
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from faster_whisper import WhisperModel

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
STT_WINDOW_SEC = float(os.getenv("STT_WINDOW_SEC", "5.0"))

app = FastAPI(title="video-overlay-translator gateway")

# Lazy load: import が GPU 初期化で固まらないように初回リクエスト時に持つ。
_model: WhisperModel | None = None


def get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(
            WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE
        )
    return _model


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
    """蓄積した webm 全体を再デコード→転写し、since_end より後のセグメントだけ返す。

    再デコードは無駄が大きいが、まずは結線確認のための素直な実装。
    長時間で遅くなったら ffmpeg のストリーム + PCM リングバッファに移行する。
    """
    if not webm_bytes:
        return "", since_end
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(webm_bytes)
        path = f.name
    try:
        model = get_model()
        segments, _info = model.transcribe(
            path,
            language=None,        # 自動判定。動画の言語が混在しても対応。
            vad_filter=False,     # 無音カットは後で
            beam_size=1,          # 体感レイテンシ重視。品質が要れば 5 へ。
        )
        new_parts: list[str] = []
        new_end = since_end
        for seg in segments:
            if seg.start >= since_end:
                t = seg.text.strip()
                if t:
                    new_parts.append(t)
                new_end = max(new_end, seg.end)
        return " ".join(new_parts), new_end
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    import uvicorn
    # tailscale serve が 443 で WSS 終端し、ここへは 127.0.0.1 でフォワードする前提。
    # 他NIC・Tailscale 直への平文露出を塞ぐためループバックに絞る。
    uvicorn.run(app, host="127.0.0.1", port=8000)
