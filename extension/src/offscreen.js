// offscreen.js
// 役割: tabCapture の音声を取得 → Opus 圧縮 → WSS で the Spark に送信 →
//       返ってきた和訳テキストを service worker に渡す。
// ここがネットワーク処理の集約点（ページの mixed-content ポリシー外）。

let ws = null;
let recorder = null;
let stream = null;
let audioCtx = null;
let currentTabId = null;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "START_CAPTURE") {
    currentTabId = msg.tabId;
    startCapture(msg.streamId, msg.url).catch((err) => {
      console.error("[vot/offscreen] startCapture failed:", err);
    });
  } else if (msg?.type === "STOP") {
    stopCapture();
  }
});

async function startCapture(streamId, url) {
  if (!url) {
    console.error("[vot/offscreen] Spark WSS URL が渡ってきていません。");
    return;
  }

  stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });

  // tabCapture の音声はキャプチャ側に流れる代わりにタブのスピーカー出力が止まる。
  // そのままだとユーザに動画音声が聞こえなくなるので、WebAudio でスピーカーに戻す。
  audioCtx = new AudioContext();
  audioCtx.createMediaStreamSource(stream).connect(audioCtx.destination);

  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  ws.onopen = () => console.info("[vot/offscreen] WSS open:", url);
  ws.onerror = (e) => console.error("[vot/offscreen] WSS error:", e);
  ws.onclose = (e) => console.info("[vot/offscreen] WSS close:", e.code, e.reason);
  ws.onmessage = (ev) => {
    // the Spark からの和訳。{ status, text } を想定。
    const data = JSON.parse(ev.data);
    chrome.runtime.sendMessage({ type: "TRANSLATION", tabId: currentTabId, ...data });
  };

  // MediaRecorder(opus) で時間窓チャンク。VAD 区切りは将来対応（HANDOVER 9）。
  recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
  recorder.ondataavailable = async (e) => {
    if (ws?.readyState === WebSocket.OPEN && e.data.size > 0) {
      ws.send(await e.data.arrayBuffer());
    }
  };
  recorder.start(1000); // 1秒ごと（暫定）
}

function stopCapture() {
  try { recorder?.stop(); } catch {}
  recorder = null;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  audioCtx?.close().catch(() => {});
  audioCtx = null;
  try { ws?.close(); } catch {}
  ws = null;
  currentTabId = null;
}
