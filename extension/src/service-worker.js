// service-worker.js
// 役割: ツールバー操作の受付、offscreen document のライフサイクル管理、
//       tabCapture の streamId 取得、content script との橋渡し。
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

chrome.action.onClicked.addListener(async (tab) => {
  const active = await getActiveTabId();

  if (active === tab.id) {
    // 同じタブで再クリック → OFF
    await chrome.runtime.sendMessage({ type: "STOP" }).catch(() => {});
    if (await hasOffscreen()) await chrome.offscreen.closeDocument();
    await setBadge(tab.id, false);
    await setActiveTabId(null);
    return;
  }

  if (active != null) {
    // 別タブで起動中なら先に止める
    await chrome.runtime.sendMessage({ type: "STOP" }).catch(() => {});
    await setBadge(active, false);
  }

  // URL は SW 側で読む（offscreen から chrome.storage に届かないケースを避ける）。
  const { sparkWssUrl } = await chrome.storage.local.get("sparkWssUrl");
  if (!sparkWssUrl) {
    await chrome.runtime.openOptionsPage();
    return;
  }

  await ensureOffscreen();
  // getMediaStreamId はトップレベル user gesture が必要（action click は満たす）。
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  await chrome.runtime.sendMessage({
    type: "START_CAPTURE",
    streamId,
    tabId: tab.id,
    url: sparkWssUrl,
  });
  await setActiveTabId(tab.id);
  await setBadge(tab.id, true);
});

// offscreen から届いた和訳テキストを、対象タブの content script へ転送するだけ。
chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "TRANSLATION") {
    // msg: { tabId, status: "interim"|"final", text }
    chrome.tabs.sendMessage(msg.tabId, msg).catch(() => {});
  }
});

// 対象タブが閉じたら自動 OFF。
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const active = await getActiveTabId();
  if (tabId !== active) return;
  await chrome.runtime.sendMessage({ type: "STOP" }).catch(() => {});
  if (await hasOffscreen()) await chrome.offscreen.closeDocument();
  await setActiveTabId(null);
});
