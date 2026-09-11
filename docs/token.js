/* Your CourtListener token, kept on your machine.
 *
 * **A token is optional.** Every browser surface works without one, because the
 * CourtListener search endpoint answers unauthenticated requests. What a token
 * buys is a higher rate limit and priority: anonymous traffic is throttled
 * first and hardest, and a long document can crawl without one.
 *
 * **Where it is kept, precisely.** In this browser's `localStorage`, under the
 * origin serving this page. It is never transmitted anywhere except to
 * courtlistener.com in the `Authorization` header of your own lookups. There is
 * no account, no server of ours, and nothing to opt out of -- we could not
 * receive it if we wanted to, because there is nowhere for it to go.
 *
 * **What that means honestly.** `localStorage` is readable by script running on
 * this same origin, so it is the right place for a free, read-only, revocable
 * research token and the wrong place for anything else. A CourtListener token
 * is exactly that: it grants read access to public case law and can be rotated
 * from your profile page in seconds. Do not paste any other kind of credential
 * into it.
 */
window.VERASCITE_TOKEN = (function () {
  "use strict";

  var KEY = "verascite.courtlistener.token";
  var PROFILE = "https://www.courtlistener.com/profile/api-token/";

  function read() {
    try { return (window.localStorage.getItem(KEY) || "").trim(); }
    catch (e) { return ""; }          // private mode, or storage disabled
  }

  function write(value) {
    var token = (value || "").trim();
    try {
      if (token) { window.localStorage.setItem(KEY, token); }
      else { window.localStorage.removeItem(KEY); }
      return true;
    } catch (e) { return false; }
  }

  /* Headers for a lookup. Anonymous unless a token is stored. */
  function headers() {
    var h = { Accept: "application/json" };
    var token = read();
    if (token) { h.Authorization = "Token " + token; }
    return h;
  }

  /* Never print the whole thing back at the user -- a shoulder-surfer in a
     library or a screen-share should not be able to read it off the page. */
  function masked() {
    var token = read();
    if (!token) { return ""; }
    if (token.length <= 8) { return "••••"; }
    return token.slice(0, 4) + "…" + token.slice(-4);
  }

  /* Confirms a token works before it is relied on, so a typo surfaces here
     rather than as a wall of failed lookups. */
  function verify(token) {
    var url = "https://www.courtlistener.com/api/rest/v4/search/?type=o&q=" +
      encodeURIComponent('citation:("550 U.S. 544")');
    return fetch(url, { headers: { Accept: "application/json",
                                   Authorization: "Token " + (token || "").trim() } })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) {
          return { ok: false, reason: "CourtListener rejected that token." };
        }
        if (!r.ok) {
          return { ok: false, reason: "CourtListener answered HTTP " + r.status +
                                      ". That is a service problem, not a bad token." };
        }
        return { ok: true };
      })
      .catch(function (e) {
        return { ok: false, reason: "Could not reach CourtListener (" + e.message + ")." };
      });
  }

  return { read: read, write: write, headers: headers, masked: masked,
           verify: verify, KEY: KEY, PROFILE: PROFILE };
})();
