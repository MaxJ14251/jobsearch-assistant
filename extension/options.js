"use strict";

const port = document.getElementById("port");
const key = document.getElementById("key");
const status = document.getElementById("status");

function say(text) { status.textContent = text; }

chrome.storage.local.get({ port: 8765, key: "" }).then((saved) => {
  port.value = saved.port;
  key.value = saved.key;
});

document.getElementById("save").addEventListener("click", async () => {
  const number = Number(port.value);
  if (!Number.isInteger(number) || number < 1 || number > 65535) {
    say("The port must be a whole number from 1 to 65535.");
    return;
  }
  await chrome.storage.local.set({ port: number, key: key.value.trim() });
  say("Saved.");
});

document.getElementById("test").addEventListener("click", async () => {
  say("Asking the dashboard…");
  const answer = await chrome.runtime.sendMessage({ type: "ping" });
  say(answer && answer.ok
    ? "Connected to the dashboard (version " + answer.result.version + ")."
    : (answer && answer.error) || "No answer.");
});
