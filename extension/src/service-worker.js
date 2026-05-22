// service-worker.js
// 役割: ツールバー操作の受付、offscreen document のライフサイクル管理、
//       tabCapture の streamId 取得、content script との橋渡し。
// ネットワーク(WSS)処理そのものは offscreen 側に集約する（CLAUDE.md ガード3）。

const OFFSCREEN_PATH = "src/offscreen.html";

async function ensureOffscreen() {
  // TODO: chrome.offscreen.hasDocument() を確認し、なければ createDocument。
  //       reasons: ["USER_MEDIA"], justification: "tab audio capture for translation".
}

chrome.action.onClicked.addListener(async (tab) => {
  // TODO: ON/OFF をトグル。ON 時:
  //   1) await ensureOffscreen()
  //   2) const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id })
  //   3) offscreen に { type: "START_CAPTURE", streamId, tabId: tab.id } を送る
  //   OFF 時: offscreen に STOP を送る。
});

// offscreen から届いた和訳テキストを、対象タブの content script へ転送するだけ。
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg?.type === "TRANSLATION") {
    // msg: { tabId, status: "interim"|"final", text }
    chrome.tabs.sendMessage(msg.tabId, msg);
  }
});
