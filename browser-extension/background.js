// Sends browser downloads to the Download Manager app (127.0.0.1:9614).
// If the app isn't running, the browser's own download is left alone.
const API = "http://127.0.0.1:9614";

async function isEnabled() {
  const { enabled = true } = await chrome.storage.local.get("enabled");
  return enabled;
}

async function refreshBadge() {
  chrome.action.setBadgeText({ text: (await isEnabled()) ? "" : "OFF" });
}
refreshBadge();
chrome.runtime.onStartup.addListener(refreshBadge);

// Click the toolbar icon to turn interception on/off.
chrome.action.onClicked.addListener(async () => {
  await chrome.storage.local.set({ enabled: !(await isEnabled()) });
  refreshBadge();
});

chrome.downloads.onCreated.addListener(async (item) => {
  const url = item.finalUrl || item.url;
  if (item.incognito || !/^https?:/i.test(url)) return;   // blob:, data:, file: stay in the browser
  if (!(await isEnabled())) return;

  try {   // is the app running?
    const ping = await fetch(`${API}/ping`, { signal: AbortSignal.timeout(1500) });
    if (!ping.ok) return;
  } catch { return; }

  const cookies = await chrome.cookies.getAll({ url });
  const cookie = cookies.map((c) => `${c.name}=${c.value}`).join("; ");

  try {
    const res = await fetch(`${API}/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, referrer: item.referrer || "", cookie, userAgent: navigator.userAgent }),
    });
    if (!res.ok) return;
  } catch { return; }

  // The app has it: drop the browser's own copy.
  chrome.downloads.cancel(item.id).then(() => chrome.downloads.erase({ id: item.id })).catch(() => {});
});
