// content-script.js
// 役割: 和訳テキストを受け取って透明オーバーレイに描画する「だけ」。
// ネットワーク処理は持たない（CLAUDE.md ガード3）。

const STALE_AFTER_MS = 15000; // この時間新しい翻訳が来なかったら stale 扱い

const overlay = document.createElement("div");
overlay.id = "vot-overlay";
overlay.setAttribute("aria-live", "polite");
document.documentElement.appendChild(overlay);

let staleTimer = null;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type !== "TRANSLATION") return;
  // status: "interim"(暫定=薄字) | "final"(確定=濃字)
  overlay.dataset.status = msg.status;
  overlay.textContent = msg.text;
  // 同じ字幕が長時間張り付くのを防ぐため、一定時間で stale 状態へ落とす。
  if (staleTimer) clearTimeout(staleTimer);
  staleTimer = setTimeout(() => {
    overlay.dataset.status = "stale";
  }, STALE_AFTER_MS);
});
