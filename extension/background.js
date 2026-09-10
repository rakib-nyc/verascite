/* Lookups run here, not in the page.
 *
 * A content script fetches under the page's own origin, which means a site could
 * observe the requests and CourtListener would see traffic attributed to whatever
 * page happened to be open. Routing through the service worker keeps the lookup
 * between the extension and the archive, and keeps the only thing transmitted --
 * a citation string -- out of the page's network log.
 */
const API = "https://www.courtlistener.com/api/rest/v4/search/";

async function lookup(cite) {
  const url = API + "?type=o&q=" + encodeURIComponent('citation:("' + cite + '")');
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) { throw new Error("HTTP " + response.status); }
  const data = await response.json();
  const hit = (data.results || [])[0];
  if (!data.count || !hit) { return { state: "absent" }; }
  return {
    state: "found",
    actual: hit.caseName || "(unnamed)",
    parallel: (hit.citation || [])
      .filter((c) => c.toLowerCase() !== cite.toLowerCase()).slice(0, 3),
    dateFiled: hit.dateFiled || "",
    url: hit.absolute_url ? "https://www.courtlistener.com" + hit.absolute_url : ""
  };
}

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (msg.type !== "lookup") { return false; }
  lookup(msg.cite)
    .then(respond)
    .catch((err) => respond({
      state: "error",
      // A network failure is a network failure. It is never evidence about a
      // citation, and the wording has to make that impossible to misread.
      reason: "Could not reach the archive (" + err.message +
              "). That is a connection problem, not a finding about this citation."
    }));
  return true;   // keep the channel open for the async reply
});
