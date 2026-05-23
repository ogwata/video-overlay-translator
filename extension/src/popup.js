const $ = (id) => document.getElementById(id);

let currentTabId = null;
let activeTabId = null;

function setStatus(html) { $("status").innerHTML = html; }

function relativeTime(at) {
  const ms = Date.now() - at;
  if (ms < 1500) return "just now";
  if (ms < 60000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3600000) return `${Math.floor(ms / 60000)}m ago`;
  return `${Math.floor(ms / 3600000)}h ago`;
}

function paintToggle() {
  const onThisTab = activeTabId != null && activeTabId === currentTabId;
  const onOtherTab = activeTabId != null && activeTabId !== currentTabId;
  if (onThisTab) {
    setStatus('<span class="status-on">● Translating</span>');
    $("toggle").textContent = "Stop";
    $("toggle").className = "danger";
  } else if (onOtherTab) {
    setStatus(`<span class="status-warn">● Active on tab ${activeTabId}</span>`);
    $("toggle").textContent = "Switch to this tab";
    $("toggle").className = "primary";
  } else {
    setStatus('<span class="status-off">○ Idle</span>');
    $("toggle").textContent = "Start";
    $("toggle").className = "primary";
  }
}

function paintLast(last) {
  if (!last) return;
  $("lastText").textContent = last.text;
  $("lastUpdate").textContent = relativeTime(last.at);
}

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  currentTabId = tab?.id ?? null;

  const state = await chrome.runtime.sendMessage({ type: "GET_STATE" }).catch(() => null);
  activeTabId = state?.activeTabId ?? null;
  paintToggle();
  paintLast(state?.lastTranslation);

  const { sparkWssUrl, sparkModel } = await chrome.storage.local.get([
    "sparkWssUrl",
    "sparkModel",
  ]);
  if (sparkWssUrl) {
    try { $("host").textContent = new URL(sparkWssUrl).host; }
    catch { $("host").textContent = "(invalid URL)"; }
  } else {
    $("host").textContent = "(not configured)";
  }
  $("model").textContent = sparkModel || "(server default)";
}

// 翻訳が来るたびに popup の表示を更新（popup を開きっぱなしの間ライブ更新）。
chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "session") return;
  if (changes.lastTranslation?.newValue) paintLast(changes.lastTranslation.newValue);
  if (changes.activeTabId) {
    activeTabId = changes.activeTabId.newValue ?? null;
    paintToggle();
  }
});

$("toggle").addEventListener("click", async () => {
  if (activeTabId === currentTabId) {
    await chrome.runtime.sendMessage({ type: "STOP_FROM_POPUP" }).catch(() => {});
    window.close();
    return;
  }

  // 設定が無ければ options を開く（gesture を浪費しない）。
  const { sparkWssUrl } = await chrome.storage.local.get("sparkWssUrl");
  if (!sparkWssUrl) {
    chrome.runtime.openOptionsPage();
    window.close();
    return;
  }

  // gesture が popup ボタンクリックで生きてるうちに streamId を取る。
  // SW 経由で間接的に取るより popup から直接の方が安定。
  try {
    const streamId = await chrome.tabCapture.getMediaStreamId({
      targetTabId: currentTabId,
    });
    await chrome.runtime.sendMessage({
      type: "START_FROM_POPUP",
      tabId: currentTabId,
      streamId,
    });
  } catch (e) {
    console.error("[vot/popup] failed to start:", e);
    setStatus(`<span class="status-warn">⚠ ${e.message || e}</span>`);
    return;
  }
  window.close();
});

$("settings").addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
  window.close();
});

init();
