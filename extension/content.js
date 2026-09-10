/* Find the legal citations on a page and say what is actually reported at each.
 *
 * Two rules shape everything here, and both come from measurement rather than
 * taste.
 *
 * **Nothing happens until asked.** No scanning on load, no requests on load, no
 * ambient reading of pages. Extraction runs locally when the user opens the
 * popup, so the badge can say how many citations are present without a single
 * byte leaving the machine; only an explicit click checks them. Legal research
 * is often privileged, and an extension that quietly reads every page a lawyer
 * opens is not a verification tool, it is a liability.
 *
 * **Absence is rendered as absence.** About one citation in ten in a real brief
 * is missing from the free archives -- not fabricated, not defective, simply not
 * held. Published false-flag rates for that case run from 25% to 66%. A browser
 * extension's natural affordances push hard the wrong way here: a red underline
 * is read as "wrong" and a warning triangle as "fake". So exactly one result
 * gets a warning colour -- a reporter series that has never been published,
 * which reporters-db *contradicts* rather than merely fails to find. Every other
 * outcome is neutral, and says in words why it is not an accusation.
 */
(function () {
  "use strict";

  if (window.__verasciteLoaded) { return; }
  window.__verasciteLoaded = true;

  var GAP_MS = 900;   // CourtListener is a non-profit. Do not hammer it.
  var MAX = 40;

  var CITE = /\b(\d{1,4})\s+([A-Z][A-Za-z.'’]*(?:\s*[0-9]?[A-Za-z.'’]+){0,4}?)\s+(\d{1,7})(?=\b)/g;
  var NOT_A_REPORTER = /^(?:U\.?S\.?C|C\.?F\.?R|Stat|Pub|No|Fed\.?\s*R|Cir|Supp\.?\s*$)/i;
  var VENDOR_ONLY = /^(?:WL|LEXIS|U\.?S\.?\s*App\.?\s*LEXIS|U\.?S\.?\s*Dist\.?\s*LEXIS)$/i;

  var SKIP = { SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, TEXTAREA: 1, INPUT: 1,
               SELECT: 1, CODE: 1, PRE: 1 };

  function reporterExists(reporter) {
    var set = window.VERASCITE_REPORTERS;
    if (!set) { return true; }                 // data missing: never accuse
    if (set.has(reporter)) { return true; }
    var squashed = reporter.replace(/\s+/g, "");
    var loose = reporter.replace(/\./g, "").replace(/\s+/g, " ").trim();
    var it = set.values(), n;
    while (!(n = it.next()).done) {
      var v = n.value;
      if (v.replace(/\s+/g, "") === squashed) { return true; }
      if (v.replace(/\./g, "").replace(/\s+/g, " ").trim() === loose) { return true; }
    }
    return false;
  }

  /* ---- extraction, entirely local ---- */

  function textNodes(root) {
    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var parent = node.parentElement;
        if (!parent) { return NodeFilter.FILTER_REJECT; }
        if (SKIP[parent.tagName]) { return NodeFilter.FILTER_REJECT; }
        if (parent.closest("[data-verascite]")) { return NodeFilter.FILTER_REJECT; }
        if (parent.isContentEditable) { return NodeFilter.FILTER_REJECT; }
        if (!node.nodeValue || node.nodeValue.length < 8) { return NodeFilter.FILTER_REJECT; }
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var out = [], n;
    while ((n = walker.nextNode())) { out.push(n); }
    return out;
  }

  function scan() {
    var found = [], seen = {};
    textNodes(document.body).forEach(function (node) {
      var text = node.nodeValue, m;
      CITE.lastIndex = 0;
      while ((m = CITE.exec(text)) !== null) {
        var reporter = m[2].replace(/\s+/g, " ").trim();
        if (NOT_A_REPORTER.test(reporter) || !/[A-Za-z]/.test(reporter)) { continue; }
        var cite = m[1] + " " + reporter + " " + m[3];
        var key = cite.toLowerCase();
        if (seen[key]) { continue; }
        seen[key] = 1;
        var before = text.slice(Math.max(0, m.index - 90), m.index);
        var nameMatch = before.match(
          /([A-Z][A-Za-z0-9'’&.,\- ]{1,70}?\sv\.?\s[A-Za-z0-9'’&.,\- ]{2,60}?)[,\s]*$/);
        found.push({
          cite: cite,
          reporter: reporter,
          claimed: nameMatch ? nameMatch[1].trim() : "",
          claimedYear: claimedYear(text, m.index + m[0].length),
          vendorOnly: VENDOR_ONLY.test(reporter),
          noSuchReporter: !VENDOR_ONLY.test(reporter) && !reporterExists(reporter)
        });
        if (found.length >= MAX) { return; }
      }
    });
    return found;
  }

  /* ---- marking citations in the page ---- */

  function mark(cite, state) {
    var wanted = cite.toLowerCase();
    textNodes(document.body).forEach(function (node) {
      var idx = node.nodeValue.toLowerCase().indexOf(wanted);
      if (idx < 0) { return; }
      var range = document.createRange();
      range.setStart(node, idx);
      range.setEnd(node, idx + cite.length);
      var span = document.createElement("span");
      span.setAttribute("data-verascite", state);
      span.className = "vs-mark vs-" + state;
      try { range.surroundContents(span); } catch (e) { /* spans elements; skip */ }
    });
  }

  /* ---- the panel ---- */

  var LABEL = {
    mismatch: "Different case at this citation",
    noreporter: "No such reporter series",
    yearoff: "Year does not match",
    found: "Reported",
    absent: "Not in this archive",
    vendor: "No free source carries this",
    error: "Could not check"
  };

  var EXPLAIN = {
    noreporter: function (i) {
      return "<b>" + esc(i.reporter) + "</b> is not among the 1,342 reporter series " +
        "recognised in American law, or any known variant spelling. Every published " +
        "American case appears in one of them. <b>This is a contradiction, not an " +
        "absence</b> — it is the one thing this can tell you affirmatively.";
    },
    found: function (i) {
      var s = "Reported as <b>" + esc(i.actual) + "</b>.";
      if (i.claimed) {
        s += " Consistent with the name on the page. That confirms the name only — " +
             "not the quotation, the page, or what the case holds.";
      }
      return s;
    },
    mismatch: function (i) {
      return "The page cites this as <b>" + esc(i.claimed) + "</b>, but the archive " +
        "reports the case at this citation as <b>" + esc(i.actual) + "</b>. Those are " +
        "different cases. <b>A source was retrieved and it contradicts the page</b> — " +
        "this is a finding, not a prompt." +
        (i.yearOff ? " The year is wrong too: the page says " + esc(i.claimedYear) +
          ", the record was filed " + esc(i.filedYear) + "." : "");
    },
    yearoff: function (i) {
      return "Reported as <b>" + esc(i.actual) + "</b>, which matches the name on the " +
        "page — but the page says <b>" + esc(i.claimedYear) + "</b> and the record was " +
        "filed in <b>" + esc(i.filedYear) + "</b>. Check the year, and check you have " +
        "the right decision: some cases have a later history at a different citation.";
    },
    absent: function () {
      return "<b>This does not mean the citation is fake.</b> This archive does not " +
        "contain it. Recent decisions, unpublished dispositions, state trial courts " +
        "and cases carried only by paid services are routinely absent — about one " +
        "citation in ten. Check it elsewhere before relying on it, and before " +
        "removing it.";
    },
    vendor: function () {
      return "A <b>vendor database identifier</b>. It names a real decision that no " +
        "free archive carries, so nothing free can confirm or deny it. That is a gap " +
        "in public access, not a mark against the citation.";
    },
    error: function (i) { return esc(i.reason || "The archive could not be reached."); }
  };

      // A retrieved record that names a different case is a contradiction, not a
      // hint. This is the second-largest defect class and it is the whole reason
      // the page reports the real case name rather than a bare found/not-found.
      function classify(item, res) {
        if (res.state !== "found") { return res; }
        var merged = Object.assign({}, res);
        var verdict = nameVerdict(item.claimed, res.actual);
        var filedYear = (res.dateFiled || "").slice(0, 4);
        var yearOff = !!(item.claimedYear && filedYear && item.claimedYear !== filedYear);
        merged.filedYear = filedYear;
        merged.claimedYear = item.claimedYear || "";
        merged.yearOff = yearOff;
        if (verdict === "mismatch") { merged.state = "mismatch"; }
        else if (yearOff) { merged.state = "yearoff"; }
        return merged;
      }

  function esc(s) {
    var d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  // Name comparison is ported from the command-line tool rather than
  // reimplemented, so the browser and the terminal reach the same verdict.
  // "mismatch" is a finding: a source was retrieved and it names a different
  // case. It is reached only when both names identify a specific party and
  // they share none of them, exact or misspelled.
  function nameVerdict(claimed, actual) {
    var N = window.VERASCITE_NAMES;
    if (!N || !claimed || !actual) { return "unclear"; }
    return N.compare(claimed, actual);
  }

  // The year the document asserts, taken only from a parenthetical directly
  // after the citation, so a stray four-digit number cannot become a finding.
  function claimedYear(text, endIndex) {
    var after = text.slice(endIndex, endIndex + 40);
    var m = after.match(/^[^()]{0,24}\((?:[^)]*?\b)?((?:19|20)\d{2})\)/);
    return m ? m[1] : "";
  }

  function panel() {
    var existing = document.getElementById("verascite-panel");
    if (existing) { existing.remove(); }
    var p = document.createElement("div");
    p.id = "verascite-panel";
    p.innerHTML =
      '<div class="vs-head">' +
        '<span class="vs-title">VeraScite</span>' +
        '<button class="vs-close" aria-label="Close">×</button>' +
      '</div>' +
      '<div class="vs-status">Checking…</div>' +
      '<div class="vs-list"></div>' +
      '<div class="vs-foot">Only citation strings were sent, to the free ' +
        'CourtListener archive. Never the page, never your text. ' +
        'This is not legal advice.</div>';
    document.documentElement.appendChild(p);
    p.querySelector(".vs-close").addEventListener("click", function () { p.remove(); });
    return p;
  }

  function row(item) {
    var d = document.createElement("div");
    d.className = "vs-row vs-" + item.state;
    d.innerHTML =
      '<div class="vs-row-top"><span class="vs-cite"></span>' +
      '<span class="vs-chip vs-chip-' + item.state + '">' + LABEL[item.state] + "</span></div>" +
      '<div class="vs-why">' + EXPLAIN[item.state](item) + "</div>";
    d.querySelector(".vs-cite").textContent = item.cite;
    if (item.state === "found" && item.url) {
      var a = document.createElement("a");
      a.className = "vs-open";
      a.href = item.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      a.textContent = "Read it on CourtListener →";
      d.appendChild(a);
    }
    return d;
  }

  function run() {
    var items = scan();
    var p = panel();
    var list = p.querySelector(".vs-list");
    var status = p.querySelector(".vs-status");

    if (!items.length) {
      status.textContent = "No reporter citations found on this page.";
      return;
    }

    var done = [];
    var i = 0;
    (function step() {
      if (i >= items.length) {
        var counts = {};
        done.forEach(function (r) { counts[r.state] = (counts[r.state] || 0) + 1; });
        var bits = Object.keys(counts).map(function (k) {
          return counts[k] + " " + LABEL[k].toLowerCase();
        });
        status.textContent = "Checked " + done.length + ": " + bits.join(" · ");
        return;
      }
      var item = items[i];
      status.textContent = "Checking " + (i + 1) + " of " + items.length + "…";

      var settle = function (res) {
        var merged = Object.assign({}, item, classify(item, res));
        done.push(merged);
        list.appendChild(row(merged));
        mark(merged.cite, merged.state);
        i += 1;
        setTimeout(step, res.state === "found" || res.state === "absent" ? GAP_MS : 0);
      };

      // Two states are decided locally and cost no request at all.
      if (item.noSuchReporter) { settle({ state: "noreporter" }); return; }
      if (item.vendorOnly) { settle({ state: "vendor" }); return; }

      chrome.runtime.sendMessage({ type: "lookup", cite: item.cite }, function (res) {
        settle(res || { state: "error", reason: "The extension could not reach its own background worker." });
      });
    })();
  }

  chrome.runtime.onMessage.addListener(function (msg, sender, respond) {
    if (msg.type === "count") { respond({ count: scan().length }); return true; }
    if (msg.type === "run") { run(); respond({ started: true }); return true; }
    return false;
  });
})();
