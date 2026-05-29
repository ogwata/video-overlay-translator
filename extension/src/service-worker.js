// service-worker.js
// 役割: popup からの開始/停止指示を受けて offscreen のライフサイクルを回し、
//       offscreen からの和訳を対象タブの content script へ転送する。
// ネットワーク(WSS)処理そのものは offscreen 側に集約する（CLAUDE.md ガード3）。

const OFFSCREEN_PATH = "src/offscreen.html";

// MV3 の SW は休眠する可能性があるため、状態は storage.session に保持。
async function getActiveTabId() {
  const { activeTabId } = await chrome.storage.session.get("activeTabId");
  return activeTabId ?? null;
}
async function setActiveTabId(tabId) {
  if (tabId == null) {
    await chrome.storage.session.remove("activeTabId");
  } else {
    await chrome.storage.session.set({ activeTabId: tabId });
  }
}

async function hasOffscreen() {
  return chrome.offscreen.hasDocument();
}

async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_PATH,
    reasons: ["USER_MEDIA"],
    justification: "tab audio capture for translation",
  });
}

async function setBadge(tabId, on) {
  await chrome.action.setBadgeText({ tabId, text: on ? "ON" : "" });
  if (on) await chrome.action.setBadgeBackgroundColor({ color: "#16a34a" });
}

async function startCaptureWithStreamId(tabId, streamId) {
  const { sparkWssUrl, sparkModel } = await chrome.storage.local.get([
    "sparkWssUrl",
    "sparkModel",
  ]);
  if (!sparkWssUrl) {
    await chrome.runtime.openOptionsPage();
    return;
  }

  let url = sparkWssUrl;
  if (sparkModel) {
    const u = new URL(sparkWssUrl);
    u.searchParams.set("model", sparkModel);
    url = u.toString();
  }

  await ensureOffscreen();
  await chrome.runtime.sendMessage({
    type: "START_CAPTURE",
    streamId,
    tabId,
    url,
  });
  await setActiveTabId(tabId);
  await setBadge(tabId, true);
  // content script に「このタブはキャプチャ中」を伝える（Space で pause/play 用）。
  chrome.tabs.sendMessage(tabId, { type: "CAPTURE_STATE", active: true }).catch(() => {});
}

async function stopCapture() {
  const active = await getActiveTabId();
  await chrome.runtime.sendMessage({ type: "STOP" }).catch(() => {});
  if (await hasOffscreen()) await chrome.offscreen.closeDocument();
  if (active != null) {
    await setBadge(active, false);
    chrome.tabs.sendMessage(active, { type: "CAPTURE_STATE", active: false }).catch(() => {});
  }
  await setActiveTabId(null);
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "TRANSLATION") {
    // 対象タブの content script へ転送 + popup ライブ表示用に session に最新を残す。
    chrome.tabs.sendMessage(msg.tabId, msg).catch(() => {});
    chrome.storage.session.set({
      lastTranslation: { text: msg.text, status: msg.status, at: Date.now() },
    });
    return;
  }
  if (msg?.type === "GET_STATE") {
    (async () => {
      const activeTabId = await getActiveTabId();
      const { lastTranslation } = await chrome.storage.session.get("lastTranslation");
      sendResponse({ activeTabId, lastTranslation: lastTranslation ?? null });
    })();
    return true; // async sendResponse
  }
  if (msg?.type === "START_FROM_POPUP") {
    (async () => {
      const active = await getActiveTabId();
      if (active != null && active !== msg.tabId) {
        await stopCapture(); // 別タブで動いていたら止めて切替
      }
      await startCaptureWithStreamId(msg.tabId, msg.streamId);
    })();
    return;
  }
  if (msg?.type === "STOP_FROM_POPUP") {
    stopCapture();
    return;
  }
});

// 対象タブが閉じたら自動 OFF。
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const active = await getActiveTabId();
  if (tabId === active) await stopCapture();
});
