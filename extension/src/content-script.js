// content-script.js
// 役割: 和訳テキストを受け取って透明オーバーレイに描画する「だけ」。
// ネットワーク処理は持たない（CLAUDE.md ガード3）。
//
// VAD ベースで翻訳が来るようになり「自然な文単位」だが立て続けに到着するため、
// 読み終わる前に上書きされてしまう。文字数ベースの最小表示時間でキュー消化する。

const STALE_AFTER_MS = 15000;        // 新着が来ないとオーバーレイを薄字に
const MIN_DISPLAY_MS = 2000;         // 1 字幕の最低表示時間
const MAX_DISPLAY_MS = 8000;         // 1 字幕の最大表示時間
const PER_CHAR_MS = 70;              // 1 文字あたりの追加表示時間
const MAX_QUEUE = 4;                 // キュー長上限。溢れたら古い方から捨てる

const overlay = document.createElement("div");
overlay.id = "vot-overlay";
overlay.setAttribute("aria-live", "polite");
document.documentElement.appendChild(overlay);

let queue = [];
let displayTimer = null;
let staleTimer = null;
let showing = false;
let currentText = "";

function readingTimeMs(text) {
  return Math.min(MAX_DISPLAY_MS, Math.max(MIN_DISPLAY_MS, text.length * PER_CHAR_MS));
}

function showNext() {
  if (queue.length === 0) {
    showing = false;
    return;
  }
  const text = queue.shift();
  currentText = text;
  overlay.dataset.status = "final";
  overlay.textContent = text;
  if (staleTimer) clearTimeout(staleTimer);
  staleTimer = setTimeout(() => {
    overlay.dataset.status = "stale";
  }, STALE_AFTER_MS);
  showing = true;
  if (displayTimer) clearTimeout(displayTimer);
  displayTimer = setTimeout(showNext, readingTimeMs(text));
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type !== "TRANSLATION") return;
  const text = msg.text;
  if (!text) return;
  // 直前と同じ訳文は無視（VAD でも稀に発生しうる）。
  if (text === currentText || text === queue[queue.length - 1]) return;
  queue.push(text);
  while (queue.length > MAX_QUEUE) queue.shift();
  if (!showing) showNext();
});
