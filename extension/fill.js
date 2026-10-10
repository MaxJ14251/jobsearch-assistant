// What goes in which field: pure functions, no DOM, no network (plan 30).
//
// Loaded two ways: as a content script (it sets globalThis.JSAFill) and by
// Node's test runner (module.exports). It decides; content.js does.
//
// The rule it keeps: a field is filled only from something you wrote down.
// A yes/no question is answered only from a yes/no in your profile, never
// guessed from free text, and salary is never filled (ADR 0001).

(function (root) {
  "use strict";

  function normalize(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/[‘’]/g, "'")
      .replace(/[_\-\[\]().,:;?!*"/]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  // Label text -> key. First match wins, so the order matters: salary first
  // (a "compensation" field must never be mistaken for anything fillable),
  // the specific names before "name", "why this role" before "why us".
  const RULES = [
    ["salary", /\b(salary|compensation|pay expectations?|desired pay|expected pay)\b/],
    ["cover_letter", /\bcover letter\b/],
    ["resume", /\b(resume|résumé|cv)\b/],
    ["preferred_name", /\bpreferred (first )?name\b/],
    ["first_name", /\b(first|given) name\b/],
    ["last_name", /\b(last|family) name\b|\bsurname\b/],
    ["full_name", /^(full )?name$|\bfull name\b|\byour name\b|^legal name$/],
    ["email", /\be ?mail\b/],
    ["phone", /\b(phone|mobile|telephone)\b/],
    ["linkedin", /\blinked ?in\b/],
    ["github", /\bgit ?hub\b/],
    ["portfolio", /\bportfolio\b/],
    ["website", /\b(website|personal site|blog)\b/],
    ["sponsorship", /\bsponsor(ship)?\b/],
    ["relocation", /\brelocat(e|ion|ing)\b/],
    ["authorized", /\b(authori[sz]ed|eligible|legally able|legally permitted) to work\b|\bright to work\b|\bwork authori[sz]ation\b/],
    ["why_role", /\bwhy\b.*\b(role|position|this job|opportunity)\b|\binterest(ed)? in (this|the) (role|position)\b/],
    ["why_company", /\bwhy\b.*\b(work|join|company|us|here)\b|\bwhat (draws|attracts) you\b/],
    ["relevant_project", /\b(project|accomplishment)s?\b.*\b(proud|relevant|tell|describe)\b|\b(tell|describe)\b.*\b(project|accomplishment)\b/],
    ["location", /\b(current )?location\b|\bwhere are you (located|based)\b|^city$|\bcity( and|,) state\b/],
  ];

  // Two of these in one label ("Are you authorized to work here, and will
  // you need sponsorship?") is a question no single yes/no answers.
  const EXCLUSIVE = ["authorized", "sponsorship", "relocation"];

  // The boards' own system fields, by id or name: these need no label.
  const BOARD_FIELDS = {
    greenhouse: {
      first_name: "first_name", last_name: "last_name", email: "email", phone: "phone",
      preferred_name: "preferred_name", resume: "resume", cover_letter: "cover_letter",
      "job_application[first_name]": "first_name", "job_application[last_name]": "last_name",
      "job_application[email]": "email", "job_application[phone]": "phone",
      "job_application[location]": "location",
    },
    lever: {
      name: "full_name", email: "email", phone: "phone", location: "location",
      resume: "resume", "urls[LinkedIn]": "linkedin", "urls[GitHub]": "github",
      "urls[Portfolio]": "portfolio", "urls[Other]": "website",
    },
    ashby: {
      _systemfield_name: "full_name", _systemfield_email: "email",
      _systemfield_phone: "phone", _systemfield_resume: "resume",
      _systemfield_location: "location",
    },
  };

  function keyForLabel(label) {
    const text = normalize(label);
    if (!text) return null;
    const exclusive = EXCLUSIVE.filter((k) => RULES.find((r) => r[0] === k)[1].test(text));
    if (exclusive.length > 1) return null;
    for (const [key, pattern] of RULES) {
      if (pattern.test(text)) return key;
    }
    return null;
  }

  // field: {board, id, name, label, type}. Board fields first, then the label.
  function keyFor(field) {
    const known = BOARD_FIELDS[field.board] || {};
    for (const handle of [field.id, field.name]) {
      if (handle && Object.prototype.hasOwnProperty.call(known, handle)) return known[handle];
    }
    const fromLabel = keyForLabel(field.label);
    if (fromLabel) return fromLabel;
    return null;
  }

  const LINK_KEYS = ["linkedin", "github", "portfolio", "website"];
  const YES_NO_KEYS = { sponsorship: "sponsorship", relocation: "relocation",
                        authorized: "authorized_to_work_us" };
  const WRITTEN_KEYS = ["why_role", "why_company", "relevant_project"];

  // What to put in a field for `key`, from the dashboard's /ext/fill answer:
  // {value, source, kind} or null. kind is "text", "yesno" or "file".
  function valueFor(key, data, fieldType) {
    if (!key || !data || data.state !== "ready" || key === "salary") return null;
    const id = data.identity || {};
    const facts = data.facts || {};
    const docs = data.documents || {};
    if (key === "resume" || key === "cover_letter") {
      const doc = docs[key];
      if (fieldType === "file") {
        if (!doc) return null;
        const label = key === "resume" ? "resume" : "cover letter";
        return { kind: "file", document: doc,
                 source: (doc.approved ? "approved " : "unapproved draft: ") +
                         label + " v" + doc.version };
      }
      return null;                 // a cover-letter text box: the file is the letter
    }
    const plain = {
      first_name: id.first_name, last_name: id.last_name, full_name: id.full_name,
      preferred_name: id.preferred_name, email: id.email, phone: id.phone,
    };
    if (key in plain) {
      return plain[key] ? { kind: "text", value: plain[key], source: "profile" } : null;
    }
    if (key === "location") {
      const place = [id.city, id.state].filter(Boolean).join(", ");
      return place ? { kind: "text", value: place, source: "profile" } : null;
    }
    if (LINK_KEYS.includes(key)) {
      const url = (id.links || {})[key];
      return url ? { kind: "text", value: url, source: "profile" } : null;
    }
    if (key in YES_NO_KEYS) {
      const answer = facts[YES_NO_KEYS[key]];
      return answer === "Yes" || answer === "No"
        ? { kind: "yesno", value: answer, source: "profile" } : null;
    }
    if (WRITTEN_KEYS.includes(key)) {
      const found = (data.written || []).find((w) => w.key === key);
      if (!found) return null;
      return { kind: "text", value: found.body,
               source: found.source === "composed" ? "written answer, from your sentences"
                                                   : "written answer, checked" };
    }
    return null;
  }

  // The option to pick for a yes/no answer: an option whose text starts with
  // that word. Anything else ("I will require sponsorship") is left alone.
  function chooseYesNo(options, answer) {
    const want = normalize(answer);
    if (want !== "yes" && want !== "no") return -1;
    const matches = [];
    options.forEach((text, i) => {
      const words = normalize(text).split(" ");
      if (words[0] === want) matches.push(i);
    });
    return matches.length === 1 ? matches[0] : -1;
  }

  // A field the form says it must have: the attribute, aria, or a trailing
  // asterisk (Lever draws a heavy one, U+2731).
  function isRequired(field) {
    return Boolean(field.required) || field.ariaRequired === "true" ||
      /[*\u2731]\s*$/.test(String(field.label || ""));
  }

  // --- open-ended questions (plan 35) -----------------------------------------
  // The ones left empty after a fill, offered in the panel for suggestions.

  // Yours to answer, never suggested. The same list is in jsa/suggest.py.
  const REFUSED = new RegExp(
    "\\b(salary|compensation|pay expectations?|desired pay|expected pay|pay range" +
    "|gender|race|racial|ethnicity|ethnic|hispanic|latino|veteran|disabilit(y|ies)" +
    "|sexual orientation|transgender|pronouns?|date of birth|how old|your age" +
    "|how did you (hear|learn|find out) about)\\b", "i");

  // Answered from the profile, a yes/no, a file, or never: not a question to
  // write an answer to.
  const CLOSED_KEYS = ["salary", "cover_letter", "resume", "preferred_name", "first_name",
    "last_name", "full_name", "email", "phone", "linkedin", "github", "portfolio",
    "website", "sponsorship", "relocation", "authorized", "location"];

  const QUESTION_START = /^(what|why|how|describe|tell|explain|share|give an example|walk us|in your own words)\b/;

  function isQuestionLike(label) {
    const raw = String(label || "").replace(/[*✱\s]+$/, "");
    return /\?$/.test(raw) || QUESTION_START.test(normalize(raw));
  }

  // field: {type, label, maxlength, empty, key}. A textarea, or a long text
  // input whose label asks something, left empty, that nothing else answers.
  function isOpenEnded(field) {
    if (!field || !field.empty) return false;
    if (field.type !== "textarea" && field.type !== "text") return false;
    if (field.key && CLOSED_KEYS.includes(field.key)) return false;
    const text = normalize(field.label);
    if (!text || REFUSED.test(text) || /\bcover letter\b/.test(text)) return false;
    if (field.type === "text") {
      if (field.maxlength > 0 && field.maxlength < 200) return false;
      if (!isQuestionLike(field.label)) return false;
    }
    return true;
  }

  // One normalization for matching a question to an earlier answer, shared
  // with jsa/suggest.py (question_key); test/question_cases.json holds both.
  function words(text) {
    return String(text || "").toLowerCase().replace(/[‘’']/g, "")
      .replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim();
  }

  function escapeRegExp(text) {
    return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function questionKey(text, company) {
    let key = words(text);
    const name = words(company);
    if (name) {
      key = key.replace(new RegExp("(?<![a-z0-9])" + escapeRegExp(name) + "(?![a-z0-9])", "g"),
                        "{company}");
    }
    return key.replace(/^(please |briefly |in a few sentences |tell us |in 2 3 sentences )+/, "")
      .trim();
  }

  // The label as the panel shows it: no trailing required-asterisk.
  function questionText(label) {
    return String(label || "").replace(/[*✱\s]+$/, "").replace(/\s+/g, " ").trim()
      .slice(0, 300);
  }

  const api = { normalize, keyForLabel, keyFor, valueFor, chooseYesNo, isRequired,
                isOpenEnded, isQuestionLike, questionKey, questionText, REFUSED,
                RULES, BOARD_FIELDS };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.JSAFill = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
