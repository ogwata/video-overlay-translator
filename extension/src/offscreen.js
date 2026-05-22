// offscreen.js
// 役割: tabCapture の音声を取得 → Opus 圧縮 → WSS で the Spark に送信 →
//       返ってきた和訳テキストを service worker に渡す。
// ここがネットワーク処理の集約点（ページの mixed-content ポリシー外）。

let ws = null;
let recorder = null;
let currentTabId = null;

// Spark の WSS URL は storage 経由で設定（実値は .env / CLAUDE.local.md と一致）。
async function getSparkUrl() {
  const { sparkWssUrl } = await chrome.storage.local.get("sparkWssUrl");
  // TODO: 未設定時の扱い。例: options ページで設定させる。
  return sparkWssUrl; // 例: wss://spark-host.<tailnet>.ts.net:8000/translate
}

chrome.runtime.onMessage.addListener(async (msg) => {
  if (msg?.type === "START_CAPTURE") {
    currentTabId = msg.tabId;
    await startCapture(msg.streamId);
  } else if (msg?.type === "STOP") {
    stopCapture();
  }
});

async function startCapture(streamId) {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    video: false,
  });

  const url = await getSparkUrl();
  ws = new WebSocket(url);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) => {
    // the Spark からの和訳。{ status, text } を想定。
    const data = JSON.parse(ev.data);
    chrome.runtime.sendMessage({ type: "TRANSLATION", tabId: currentTabId, ...data });
  };

  // TODO: MediaRecorder(opus) でチャンク化し ws.send。
  //       VAD 区切り or 時間窓は未決定（HANDOVER 9）。最初のマイルストーンでは
  //       生チャンクをそのまま送ってエコーが返ることだけ確認すれば良い。
  recorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
  recorder.ondataavailable = async (e) => {
    if (ws?.readyState === WebSocket.OPEN && e.data.size > 0) {
      ws.send(await e.data.arrayBuffer());
    }
  };
  recorder.start(1000); // 1秒ごと（暫定）
}

function stopCapture() {
  recorder?.stop();
  recorder = null;
  ws?.close();
  ws = null;
}
