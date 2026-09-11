/* The token lives in chrome.storage.local: this machine, this browser profile,
 * never synced to an account and never transmitted anywhere but CourtListener.
 * `storage.sync` was deliberately not used -- it would push the token through
 * a Google account, which is exactly the kind of quiet egress this tool exists
 * to avoid.
 */
const KEY = "courtlistenerToken";
const box = document.getElementById("tok");
const out = document.getElementById("status");

function masked(t) {
  if (!t) { return ""; }
  return t.length <= 8 ? "••••" : t.slice(0, 4) + "…" + t.slice(-4);
}

chrome.storage.local.get(KEY).then(({ [KEY]: token }) => {
  if (token) { box.placeholder = "saved: " + masked(token); }
});

// A typo should surface here, not as a wall of failed lookups later.
async function verify(token) {
  const url = "https://www.courtlistener.com/api/rest/v4/search/?type=o&q=" +
    encodeURIComponent('citation:("550 U.S. 544")');
  try {
    const r = await fetch(url, { headers: { Accept: "application/json",
                                            Authorization: "Token " + token } });
    if (r.status === 401 || r.status === 403) {
      return { ok: false, reason: "CourtListener rejected that token." };
    }
    if (!r.ok) {
      return { ok: false, reason: "CourtListener answered HTTP " + r.status +
                                  " — a service problem, not a bad token." };
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, reason: "Could not reach CourtListener (" + e.message + ")." };
  }
}

document.getElementById("save").addEventListener("click", async () => {
  const value = box.value.trim();
  if (!value) { out.textContent = "Nothing to save."; return; }
  out.textContent = "Checking…";
  const res = await verify(value);
  if (!res.ok) { out.textContent = res.reason; return; }
  await chrome.storage.local.set({ [KEY]: value });
  box.value = "";
  box.placeholder = "saved: " + masked(value);
  out.textContent = "Saved on this machine.";
});

document.getElementById("clear").addEventListener("click", async () => {
  await chrome.storage.local.remove(KEY);
  box.value = "";
  box.placeholder = "paste your token";
  out.textContent = "Removed.";
});
