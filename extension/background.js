// The only code in the extension that talks to anything (plan 30).
//
// It talks to one place: your own dashboard on 127.0.0.1, with the pairing
// key from the options page. Nothing else is fetched, nothing is sent
// anywhere else, and nothing here can submit a form.
"use strict";

async function settings() {
  const stored = await chrome.storage.local.get({ port: 8765, key: "" });
  const port = Number(stored.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error("The dashboard port in the options is not a port number.");
  }
  if (!stored.key) {
    throw new Error("Not paired: paste the key from the dashboard's Extension page " +
                    "into this extension's options.");
  }
  return { base: "http://127.0.0.1:" + port, key: stored.key };
}

async function call(path, init) {
  const { base, key } = await settings();
  let response;
  try {
    response = await fetch(base + path, {
      ...init,
      headers: { ...(init && init.headers), "X-JSA-Key": key },
      credentials: "omit",
      cache: "no-store",
    });
  } catch (err) {
    throw new Error("The dashboard is not answering. Start it with `jsa serve`.");
  }
  return response;
}

async function json(path, init) {
  const response = await call(path, init);
  const body = await response.json().catch(() => ({}));
  if (response.status === 401) {
    throw new Error(body.error || "The dashboard refused the key. Pair again.");
  }
  return { status: response.status, body };
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}

const HANDLERS = {
  // The page asking is the page being filled: its own address, from Chrome,
  // not whatever the message claims.
  async fill(message, sender) {
    const url = (sender && sender.url) || "";
    return (await json("/ext/fill?url=" + encodeURIComponent(url))).body;
  },
  async document(message) {
    const id = Number(message.id);
    if (!Number.isInteger(id) || id < 1) throw new Error("No such document.");
    const response = await call("/ext/document/" + id);
    if (!response.ok) throw new Error("The dashboard would not hand over that document.");
    return { data: toBase64(await response.arrayBuffer()),
             type: response.headers.get("content-type") || "" };
  },
  async applied(message) {
    const form = new URLSearchParams();
    form.set("job_id", String(Number(message.job_id)));
    form.set("resume", message.resume ? String(Number(message.resume)) : "");
    form.set("cover", message.cover ? String(Number(message.cover)) : "");
    form.set("confirm", message.confirm === "submitted" ? "submitted" : "");
    if (message.confirm_unapproved) form.set("confirm_unapproved", "1");
    const { status, body } = await json("/ext/applied", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form.toString(),
    });
    return { status, ...body };
  },
  async ping() {
    return (await json("/ext/ping")).body;
  },
};

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  // Only this extension's own pages and scripts.
  if (!sender || sender.id !== chrome.runtime.id) return false;
  const handler = HANDLERS[message && message.type];
  if (!handler) return false;
  handler(message, sender)
    .then((result) => reply({ ok: true, result }))
    .catch((err) => reply({ ok: false, error: String((err && err.message) || err) }));
  return true;  // the reply comes later
});
