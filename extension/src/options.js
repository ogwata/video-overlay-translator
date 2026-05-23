const $ = (id) => document.getElementById(id);

(async () => {
  const { sparkWssUrl } = await chrome.storage.local.get("sparkWssUrl");
  if (sparkWssUrl) $("url").value = sparkWssUrl;
})();

$("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("url").value.trim();
  if (url && !/^wss:\/\//i.test(url)) {
    $("status").textContent = "wss:// で始まる URL を入れてください。";
    $("status").style.color = "#dc2626";
    return;
  }
  await chrome.storage.local.set({ sparkWssUrl: url });
  $("status").style.color = "#16a34a";
  $("status").textContent = "Saved.";
  setTimeout(() => ($("status").textContent = ""), 1500);
});
