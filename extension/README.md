# Fill, you submit

A Chrome extension that fills Greenhouse, Lever and Ashby application forms
from your own job-search tracker. You check the form and press its Submit
yourself; the extension never does. Why, and what it may reach:
[ADR 0031](../docs/decisions/0031-autofill-in-your-browser-you-press-submit.md).

## Install

1. Start the dashboard: `jsa serve`.
2. Open its **Extension** page (linked at the foot of Pipeline,
   `http://127.0.0.1:8765/extension`) and press **Connect the browser
   extension**. Copy the key it shows; it is shown once.
3. In Chrome, open `chrome://extensions`, turn on **Developer mode**, choose
   **Load unpacked** and pick this `extension` folder.
4. Open the extension's options, paste the key, check the port (8765) and
   press **Test the connection**.

## Use

1. Save the job in the dashboard and approve its resume (and cover letter,
   if you want one).
2. Open the job's application page. A panel offers **Fill this
   application**.
3. Read the form. Fields it filled are outlined green, required ones it left
   for you amber, and the panel lists each with where it came from. Salary
   is never filled.
4. Press the page's own **Submit**. Then **I submitted this** in the panel
   records the application in your tracker.

## An apply session

Pipeline's **Start applying (N ready)** opens the first ready job's form:
saved jobs on these three boards with an approved resume, best match first.
The panel shows "job 1 of N". Fill, check, press the page's Submit, then:

- **I submitted this, next** records it and opens the next form;
- **Skip, next** moves on and records nothing;
- **End session** stops, with a summary.

Nothing moves on without one of those clicks, and the session is forgotten
when you close the browser.

## About this company and role

Under the panel's buttons, a collapsed section shows what this posting asks
for in education, what the company's postings ask for, and who holds this
kind of job nationwide (BLS and O*NET; see the dashboard's About this data
page). If the company's board hasn't been read this week, the dashboard
reads it once, from the board itself, and says "Checking … job board" while
it does. What postings ask for is not who gets hired, and the section says
so.

## Files

- `manifest.json`: permissions (`storage` only) and hosts (127.0.0.1 and
  the three boards).
- `background.js`: the only code that makes requests, to your dashboard.
- `fill.js`: which field gets what (pure functions).
- `session.js`: the apply session's state (pure functions; only your
  buttons move it).
- `research.js`: "About this company and role" as plain text (pure
  functions).
- `content.js`: fills the page and shows the panel.
- `options.html`, `options.js`: the port and the pairing key.
- `test/`: `node --test extension/test/*.test.mjs`, and fixture forms you
  can open in any browser (`python -m http.server --directory extension`,
  then `/test/forms/greenhouse.html`) with a stand-in for the dashboard.
