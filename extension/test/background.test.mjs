// background.js's apply-session messages, run in Node with Chrome and fetch
// stood in for: it records before it moves on, never records on a skip,
// and opens only a queued board address.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const here = (name) => fileURLToPath(new URL(name, import.meta.url));

const QUEUE = {
  items: [
    { job_id: 1, title: "A", company: "X", kind: "greenhouse", external_id: "111",
      apply_url: "https://job-boards.greenhouse.io/x/jobs/111", warnings: [] },
    { job_id: 2, title: "B", company: "X", kind: "greenhouse", external_id: "222",
      apply_url: "https://job-boards.greenhouse.io/x/jobs/222", warnings: [] },
  ],
  left_out: { needs_approval: 0, by_hand: 0, closed: 0 },
};

function worker({ appliedStatus = 200, appliedBody = { ok: true } } = {}) {
  const events = [];
  const stores = { local: { port: 8765, key: "k" }, session: {} };
  const area = (name) => ({
    get: async (defaults) => ({ ...defaults, ...stores[name] }),
    set: async (values) => { Object.assign(stores[name], values); },
  });
  let listener = null;
  const respond = (status, body) => ({
    status, ok: status < 400, headers: { get: () => "application/json" },
    json: async () => body, arrayBuffer: async () => new ArrayBuffer(0),
  });
  const context = {
    URL, URLSearchParams, Number, String, Error, Uint8Array, btoa: (s) => s,
    globalThis: null,
    chrome: {
      runtime: { id: "me", onMessage: { addListener: (fn) => { listener = fn; } } },
      storage: { local: area("local"), session: area("session") },
      tabs: { update: async (tabId, props) => { events.push(["navigate", tabId, props.url]); } },
    },
    fetch: async (address, init) => {
      const path = address.replace("http://127.0.0.1:8765", "");
      events.push(["fetch", path, (init && init.method) || "GET"]);
      if (path === "/ext/queue") return respond(200, QUEUE);
      if (path === "/ext/applied") return respond(appliedStatus, appliedBody);
      return respond(404, {});
    },
  };
  context.globalThis = context;
  context.importScripts = (name) =>
    vm.runInContext(readFileSync(here("../" + name), "utf8"), context);
  vm.createContext(context);
  vm.runInContext(readFileSync(here("../background.js"), "utf8"), context);
  const sender = { id: "me", tab: { id: 7 }, url: QUEUE.items[0].apply_url };
  const send = (message, from = sender) => new Promise((resolve) => {
    listener(message, from, resolve);
  });
  return { send, events, stores };
}

test("starting fetches the queue and opens nothing", async () => {
  const w = worker();
  const reply = await w.send({ type: "session_start" });
  assert.equal(reply.ok, true);
  assert.equal(reply.result.state.items.length, 2);
  assert.deepEqual(w.events, [["fetch", "/ext/queue", "GET"]]);
});

test("'I submitted this, next' records first, then opens the next form", async () => {
  const w = worker();
  await w.send({ type: "session_start" });
  const reply = await w.send({ type: "session_submitted", job_id: 1, resume: 5 });
  assert.equal(reply.ok, true);
  assert.deepEqual(w.events.slice(1), [
    ["fetch", "/ext/applied", "POST"],
    ["navigate", 7, QUEUE.items[1].apply_url],
  ]);
});

test("a refused record moves nothing on", async () => {
  const w = worker({ appliedStatus: 409,
                     appliedBody: { ok: false, needs: "confirm_unapproved" } });
  await w.send({ type: "session_start" });
  const reply = await w.send({ type: "session_submitted", job_id: 1, resume: 5 });
  assert.equal(reply.result.recorded.needs, "confirm_unapproved");
  assert.equal(w.events.some((e) => e[0] === "navigate"), false);
  assert.equal(w.stores.session.session.index, 0);
});

test("'Skip, next' never records", async () => {
  const w = worker();
  await w.send({ type: "session_start" });
  await w.send({ type: "session_skip", job_id: 1 });
  assert.equal(w.events.some((e) => e[1] === "/ext/applied"), false);
  assert.deepEqual(w.events.at(-1), ["navigate", 7, QUEUE.items[1].apply_url]);
});

test("only the job the session is on", async () => {
  const w = worker();
  await w.send({ type: "session_start" });
  const reply = await w.send({ type: "session_submitted", job_id: 2 });
  assert.equal(reply.ok, false);
  assert.equal(w.events.length, 1);
});

test("the last job ends it without opening anything", async () => {
  const w = worker();
  await w.send({ type: "session_start" });
  await w.send({ type: "session_skip", job_id: 1 });
  const reply = await w.send({ type: "session_submitted", job_id: 2 });
  assert.equal(reply.result.done, true);
  assert.equal(w.events.filter((e) => e[0] === "navigate").length, 1);
});

test("a stored address that isn't a board is refused", async () => {
  const w = worker();
  await w.send({ type: "session_start" });
  w.stores.session.session.items[1].apply_url = "https://evil.example/jobs/222";
  const reply = await w.send({ type: "session_skip", job_id: 1 });
  assert.equal(reply.ok, false);
  assert.match(reply.error, /Refused/);
  assert.equal(w.events.some((e) => e[0] === "navigate"), false);
});

test("messages from anything but this extension are ignored", async () => {
  const w = worker();
  let answered = false;
  // The listener returns false and never replies to a stranger.
  w.send({ type: "session_start" }, { id: "someone-else", tab: { id: 1 } })
    .then(() => { answered = true; });
  await new Promise((r) => setImmediate(r));
  assert.equal(answered, false);
  assert.deepEqual(w.events, []);
});
