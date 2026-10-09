// "About this company and role" (plan 34): the dashboard's answer, turned
// into plain lines for the panel. Pure functions; content.js sets them as
// text, never as markup.

(function (root) {
  "use strict";

  // The one-line summary shown while the section is collapsed.
  function summary(data) {
    if (!data || data.state === "unsupported") return "";
    const parts = [];
    if (data.posting) parts.push("Asks for: " + data.posting.label);
    if (data.role) parts.push("Role nationwide: " + bachelorsOrMore(data.role.line));
    if (!parts.length && data.company) parts.push("Company profile available");
    return parts.join(" · ") || "Nothing to show for this page yet";
  }

  // "…: 3% high school or less · 11% … · 52% bachelor's · 34% graduate degree."
  // -> "86% hold a bachelor's or more"
  function bachelorsOrMore(line) {
    const b = /(\d+)% bachelor's/.exec(line || "");
    const g = /(\d+)% graduate degree/.exec(line || "");
    if (!b || !g) return "see below";
    return (Number(b[1]) + Number(g[1])) + "% hold a bachelor's or more";
  }

  // [{heading, text, note}] in the order the panel shows them.
  function sections(data) {
    if (!data || data.state === "unsupported") return [];
    const caveats = data.caveats || {};
    const out = [];
    if (data.posting) {
      let text = data.posting.label;
      if (data.posting.certs && data.posting.certs.length) {
        text += "; names " + data.posting.certs.join(", ");
      }
      out.push({ heading: "This posting", text,
                 note: data.posting.evidence ? "“" + data.posting.evidence + "”" : "" });
    }
    if (data.company) {
      out.push({ heading: "This company", text: data.company.line,
                 note: caveats.posting || "" });
    } else if (data.state === "missing" || data.state === "stale") {
      out.push({ heading: "This company", text: "Not read recently.", note: "" });
    }
    if (data.role) {
      out.push({ heading: "This role, nationwide", text: data.role.line,
                 note: (caveats.role || "") + " Sources: BLS table 5.3; titles by O*NET " +
                       "(USDOL/ETA, CC BY 4.0), modified." });
    }
    return out;
  }

  // Ask the dashboard to read the board only when it said it should.
  function shouldRefresh(data) {
    return Boolean(data && (data.state === "missing" || data.state === "stale") &&
                   data.can_refresh);
  }

  const api = { summary, sections, shouldRefresh, bachelorsOrMore };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.JSAResearch = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
