// content-script.js
// 役割: 和訳テキストを受け取って透明オーバーレイに描画する「だけ」。
// ネットワーク処理は持たない（CLAUDE.md ガード3）。

const overlay = document.createElement("div");
overlay.id = "vot-overlay";
overlay.setAttribute("aria-live", "polite");
document.documentElement.appendChild(overlay);

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type !== "TRANSLATION") return;
  // status: "interim"(暫定=薄字) | "final"(確定=濃字)
  overlay.dataset.status = msg.status;
  overlay.textContent = msg.text;
});
