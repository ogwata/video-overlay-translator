const $ = (id) => document.getElementById(id);

function modelsHttpsUrl(wssUrl) {
  // wss://host[:port]/translate → https://host[:port]/models
  const u = new URL(wssUrl);
  return `https://${u.host}/models`;
}

function setModelOptions(ids, selected) {
  $("model").innerHTML = "";
  if (!ids.length) {
    $("model").appendChild(new Option("(no models)", ""));
    return;
  }
  for (const id of ids) {
    const opt = new Option(id, id);
    if (id === selected) opt.selected = true;
    $("model").appendChild(opt);
  }
}

async function refreshModels(wssUrl, selected) {
  setModelOptions([], selected);
  $("model").appendChild(new Option("(loading…)", ""));
  try {
    const resp = await fetch(modelsHttpsUrl(wssUrl), { cache: "no-store" });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const ids = (data.data || []).map((m) => m.id).filter(Boolean);
    setModelOptions(ids, selected);
  } catch (e) {
    setModelOptions([], "");
    $("model").appendChild(new Option(`(fetch failed: ${e.message})`, ""));
  }
}

(async () => {
  const { sparkWssUrl, sparkModel } = await chrome.storage.local.get([
    "sparkWssUrl",
    "sparkModel",
  ]);
  if (sparkWssUrl) {
    $("url").value = sparkWssUrl;
    await refreshModels(sparkWssUrl, sparkModel || "");
  } else {
    setModelOptions([], "");
  }
})();

$("refreshModels").addEventListener("click", () => {
  const url = $("url").value.trim();
  if (!url) return;
  refreshModels(url, $("model").value);
});

$("f").addEventListener("submit", async (e) => {
  e.preventDefault();
  const url = $("url").value.trim();
  const model = $("model").value;
  if (url && !/^wss:\/\//i.test(url)) {
    $("status").style.color = "#dc2626";
    $("status").textContent = "wss:// で始まる URL を入れてください。";
    return;
  }
  await chrome.storage.local.set({ sparkWssUrl: url, sparkModel: model });
  $("status").style.color = "#16a34a";
  $("status").textContent = "Saved.";
  setTimeout(() => ($("status").textContent = ""), 1500);
});
