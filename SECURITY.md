# Security

## Reporting a problem

Use GitHub's private reporting: the **Security** tab of this repository,
then **Report a vulnerability**. The report is visible only to the
maintainer. Please do not open a public issue for anything that could expose
someone's data or let a web page act on the dashboard.

Only the current `main` is supported. There are no releases to backport to.

Worth reporting, for example:

- anything that lets a document, email or message leave the machine without
  the person running the tool doing it themselves;
- a way for a web page, another local process or another host to read the
  tracker or act through the dashboard;
- a path by which the profile's name, address, phone or email reaches a
  model prompt;
- a generated document that states something the profile does not.

## What the tool does and does not do on a network

**It sends nothing on your behalf.** There is no code path that submits an
application, sends email or messages anyone. Every approval is a person's,
made in the CLI or the dashboard, and applying is done by that person
outside the tool.

**The dashboard binds to loopback.** `jsa serve` listens on `127.0.0.1` by
default and refuses a non-loopback address unless `JSA_ALLOW_PUBLIC_BIND=1`
is set. It also checks the `Host` header, so a page that points its own
domain at `127.0.0.1` is refused, and every action that changes state
needs a per-session token that a cross-site page cannot read.

**What does leave the machine**, and only when you run the command that does
it:

- `jsa discover` and `jsa verify` read public job boards (GET requests;
  Workday boards take their search as a POST body). Nothing about you is in
  those requests, except that the nationwide source is asked about the
  cities in your profile. `jsa add <link>` fetches the one page you give it.
- `jsa inbox` (only if you set it up) reads your mailbox over IMAP, read-only:
  the folder is opened read-only, nothing is marked read, and nothing is
  sent or changed. What it keeps is each matching reply's subject, date and
  sender domain, locally; no mail text goes anywhere. The app password stays
  in `.env`.
- `jsa enrich` sends posting text to the model API configured in `.env`.
  `jsa tailor`, `jsa prep` and `jsa outreach-draft` also send the profile's
  bullets, and refuse before any network call if your name, address, phone
  or email appears in the prompt.

Your profile, tracker, `.env` and generated documents are gitignored, and
`tools/scan_secrets.py` refuses a commit that carries them or their values.
