# main.py — the Spark 上で動く推論ゲートウェイ。
# 最初のマイルストーン: 音声を受けて固定の和訳文字列を返す WSS エコー。
# 疎通が通ったら stt.py(faster-whisper) → translate.py(vLLM) を差し込む。

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="video-overlay-translator gateway")


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.websocket("/translate")
async def translate(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            # 受信は音声チャンク(bytes)。エコー段では中身は使わず受領のみ。
            await ws.receive_bytes()
            # TODO(STT): faster-whisper で原語テキストへ
            # TODO(MT):  vLLM(Qwen) で日本語へ
            # 暫定→確定の2段で返す（HANDOVER 1, 9）。エコー段は固定文字列。
            await ws.send_json({"status": "final", "text": "（疎通OK: ここに和訳が入る）"})
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    import uvicorn
    # tailscale serve で WSS 終端する前提なので、ここは平文 HTTP/WS で listen。
    uvicorn.run(app, host="0.0.0.0", port=8000)
