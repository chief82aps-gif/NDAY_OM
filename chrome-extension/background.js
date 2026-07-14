/**
 * Background service worker — does the actual cross-origin fetch to the
 * NDAY_OM backend. Requests made here (rather than from the content script)
 * reliably bypass page-level CORS restrictions via the host_permissions
 * declared in manifest.json.
 */
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message.type !== "SYNC_CANDIDATE") return false;

  (async () => {
    const { apiBase, extensionKey, payload } = message;
    try {
      const response = await fetch(`${apiBase}/api/candidates/sync`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Extension-Key": extensionKey,
        },
        body: JSON.stringify(payload),
      });
      const text = await response.text();
      if (!response.ok) {
        sendResponse({ error: `${response.status}: ${text}` });
        return;
      }
      sendResponse({ data: JSON.parse(text) });
    } catch (e) {
      sendResponse({ error: String(e) });
    }
  })();

  return true; // keep the message channel open for the async response
});
