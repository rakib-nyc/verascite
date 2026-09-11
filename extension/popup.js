/* The popup counts locally, then checks only when told to.
 *
 * The count costs nothing -- extraction is a regex and a bundled reporter list --
 * so a user can see how many citations are on a page without any request being
 * made. That separation is the whole privacy story: looking is free, checking is
 * deliberate.
 */
const countEl = document.getElementById("count");
const go = document.getElementById("go");

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function inject(tab) {
  await chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ["content.css"] });
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["reporters.js", "names.js", "content.js"]
  });
}

document.getElementById("opts").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});

(async function () {
  try {
    const tab = await activeTab();
    if (!tab || !/^https?:/.test(tab.url || "")) {
      countEl.textContent = "This page cannot be checked.";
      return;
    }
    await inject(tab);
    const res = await chrome.tabs.sendMessage(tab.id, { type: "count" });
    const n = (res && res.count) || 0;
    countEl.textContent = n
      ? n + " citation" + (n === 1 ? "" : "s") + " found on this page."
      : "No reporter citations found on this page.";
    go.disabled = !n;
    go.addEventListener("click", async () => {
      await chrome.tabs.sendMessage(tab.id, { type: "run" });
      window.close();
    });
  } catch (err) {
    countEl.textContent = "Could not read this page (" + err.message + ").";
  }
})();
