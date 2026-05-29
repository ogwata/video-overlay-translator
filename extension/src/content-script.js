// content-script.js
// 役割: 和訳テキストを受け取って透明オーバーレイに描画する「だけ」。
// ネットワーク処理は持たない（CLAUDE.md ガード3）。
//
// VAD ベースで翻訳が来るようになり「自然な文単位」だが立て続けに到着するため、
// 読み終わる前に上書きされてしまう。文字数ベースの最小表示時間でキュー消化する。

const STALE_AFTER_MS = 10000;        // 新着が来ないとオーバーレイを薄字に
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
let captureActive = false;  // このタブで翻訳が動いているか

function readingTimeMs(text) {
  // 短い字幕は最低保証を緩める。filler を強引に 2 秒見せる必要は無い。
  if (text.length <= 4) return 900;
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
  if (msg?.type === "CAPTURE_STATE") {
    captureActive = !!msg.active;
    return;
  }
  if (msg?.type !== "TRANSLATION") return;
  captureActive = true;  // 翻訳が来ている = このタブはキャプチャ中
  const text = msg.text;
  if (!text) return;
  // 直前と同じ訳文は無視（VAD でも稀に発生しうる）。
  if (text === currentText || text === queue[queue.length - 1]) return;
  queue.push(text);
  while (queue.length > MAX_QUEUE) queue.shift();
  if (!showing) showNext();
});

// ページ内で「いま見ている動画」を推定する。
// 再生中のものを最優先、無ければ画面占有面積が最大のものを返す。
function findActiveVideo() {
  const videos = Array.from(document.querySelectorAll("video"));
  if (videos.length === 0) return null;
  const playing = videos.find((v) => !v.paused && !v.ended && v.readyState > 2);
  if (playing) return playing;
  let best = null;
  let bestArea = 0;
  for (const v of videos) {
    const r = v.getBoundingClientRect();
    const area = Math.max(0, r.width) * Math.max(0, r.height);
    if (area > bestArea) {
      bestArea = area;
      best = v;
    }
  }
  return best;
}

// 翻訳中のみ Space を奪って動画を pause/play。
// X のように操作バーが過敏に隠れる / Space 非対応のプレイヤーの回避策。
document.addEventListener(
  "keydown",
  (e) => {
    if (e.code !== "Space" || !captureActive) return;
    const t = e.target;
    const tag = (t && t.tagName ? t.tagName : "").toLowerCase();
    if (tag === "input" || tag === "textarea" || (t && t.isContentEditable)) return;
    const video = findActiveVideo();
    if (!video) return;
    e.preventDefault();
    e.stopPropagation();
    if (video.paused) video.play();
    else video.pause();
  },
  true,  // capture phase: サイト側ハンドラより先に処理する
);
