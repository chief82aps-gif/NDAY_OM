const apiBaseInput = document.getElementById("apiBase");
const extensionKeyInput = document.getElementById("extensionKey");
const status = document.getElementById("status");

chrome.storage.sync.get(["apiBase", "extensionKey"], (result) => {
  apiBaseInput.value = result.apiBase || "https://nday-om.onrender.com";
  extensionKeyInput.value = result.extensionKey || "";
});

document.getElementById("save").addEventListener("click", () => {
  const apiBase = apiBaseInput.value.trim().replace(/\/$/, "");
  const extensionKey = extensionKeyInput.value.trim();
  chrome.storage.sync.set({ apiBase, extensionKey }, () => {
    status.textContent = "Saved.";
    setTimeout(() => (status.textContent = ""), 2000);
  });
});
