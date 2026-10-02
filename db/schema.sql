-- Agentic job search assistant — application tracker schema (SQLite).
--
-- This database is the shared spine: discovery writes jobs, tailoring writes
-- documents, the dashboard writes approvals, outreach and interview prep read
-- everything. Every module reads/writes here and nowhere else.
--
-- Conventions:
--   * All timestamps are ISO-8601 UTC strings ('2026-09-09T15:04:05Z').
--   * Enum-ish columns are constrained with CHECK, not a lookup table.
--   * Nothing is ever hard-deleted; rows are archived with `archived_at`.
--
-- Apply with:  sqlite3 jobsearch.db < db/schema.sql

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------------
-- Sources: where listings come from (build step 3).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,          -- 'anthropic-greenhouse'
    kind            TEXT NOT NULL
                      CHECK (kind IN ('greenhouse','lever','ashby','workday','workable','custom','rss','themuse','manual','other')),
    url             TEXT NOT NULL,                 -- the JSON feed / RSS endpoint
    company_id      INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    enabled         INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    poll_interval_h INTEGER NOT NULL DEFAULT 24,
    last_polled_at  TEXT,
    last_status     TEXT,                          -- 'ok' or the error message
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    archived_at     TEXT
);

-- ---------------------------------------------------------------------------
-- Companies.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    slug            TEXT NOT NULL UNIQUE,          -- normalized key: 'anthropic'
    website         TEXT,
    careers_url     TEXT,
    industry        TEXT,
    size_bucket     TEXT CHECK (size_bucket IN ('1-10','11-50','51-200','201-1000','1001-5000','5000+')),
    hq_location     TEXT,
    -- Freeform research the interview-prep module fills in and reuses.
    research_notes  TEXT,
    priority        INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5), -- 1 = dream job
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    archived_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_companies_priority ON companies(priority) WHERE archived_at IS NULL;

-- ---------------------------------------------------------------------------
-- Jobs: one row per discovered listing. Dedup key is (source_id, external_id).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS jobs (
    id                INTEGER PRIMARY KEY,
    company_id        INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    source_id         INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    external_id       TEXT,                        -- the board's own job id
    title             TEXT NOT NULL,
    department        TEXT,
    location          TEXT,
    remote            TEXT CHECK (remote IN ('remote','hybrid','onsite','unknown')),
    employment_type   TEXT CHECK (employment_type IN ('full-time','part-time','contract','internship','unknown')),
    seniority         TEXT CHECK (seniority IN ('intern','entry','junior','mid','senior','staff','principal','unknown')),
    -- Pay as the posting states it (jsa/salary.py, ADR 0006). Whole dollars in
    -- salary_period units. NULL min with NULL text = no pay figure found.
    salary_min        INTEGER,
    salary_max        INTEGER,
    salary_currency   TEXT DEFAULT 'USD',
    salary_period     TEXT CHECK (salary_period IN ('year','hour')),
    salary_text       TEXT,                        -- the words it was read from
    salary_source     TEXT,                        -- 'field' (the board's pay data, n22) or 'text'
    url               TEXT NOT NULL,
    description       TEXT,                        -- full JD text, used by tailoring + prep
    description_hash  TEXT,                        -- detect edits to a reposted JD
    description_origin TEXT,                       -- NULL from a feed; 'pasted' by the operator (ADR 0020)
    posted_at         TEXT,
    discovered_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    closed_at         TEXT,                        -- set when the listing disappears
    -- Scoring produced by the discovery filter; explains itself in match_reasons.
    match_score       REAL,                        -- 0.0-1.0
    match_reasons     TEXT,                        -- JSON array of strings
    -- Regional variants of one req share a dedup_key; the dashboard shows
    -- the best-scoring row per key and counts the rest.
    dedup_key         TEXT,
    -- Which title tier matched: engineering (tier 1) or sales (tier 2).
    -- Sales roles are in scope but deliberately ranked below engineering.
    track             TEXT NOT NULL DEFAULT 'engineering'
                        CHECK (track IN ('engineering','sales')),
    -- ---- LLM enrichment (jsa enrich) ------------------------------------
    -- Facts the deterministic filter structurally cannot see, because they
    -- live in prose: "Bachelor's degree in CS or equivalent" buried in a
    -- requirements list, or a clearance mentioned only in the body.
    -- NULL means "not yet enriched", not "no".
    degree_required     INTEGER CHECK (degree_required IN (0,1)),
    clearance_required  INTEGER CHECK (clearance_required IN (0,1)),
    years_required      INTEGER,
    tech_stack          TEXT,          -- JSON array of strings
    enrichment_note     TEXT,          -- one-line rationale from the model
    enriched_at         TEXT,
    enrichment_model    TEXT,          -- provenance: which model produced this
    enrichment_hash     TEXT,          -- description_hash when enriched; re-run on change
    archived_at       TEXT,
    UNIQUE (source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_company     ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered  ON jobs(discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_score       ON jobs(match_score DESC) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_jobs_dedup       ON jobs(dedup_key);
CREATE INDEX IF NOT EXISTS idx_jobs_unenriched  ON jobs(enriched_at) WHERE enriched_at IS NULL;

-- ---------------------------------------------------------------------------
-- Applications: the pipeline. At most one live application per job.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS applications (
    id                INTEGER PRIMARY KEY,
    job_id            INTEGER NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'saved'
                        CHECK (status IN (
                            'saved',        -- discovered and kept
                            'drafting',     -- tailoring in progress
                            'ready',        -- docs drafted, awaiting human approval
                            'applied',
                            'phone_screen',
                            'technical',
                            'onsite',
                            'offer',
                            'rejected',
                            'withdrawn',
                            'ghosted'
                        )),
    saved_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    applied_at        TEXT,
    last_activity_at  TEXT,
    next_action       TEXT,                        -- 'follow up with recruiter'
    next_action_due   TEXT,
    -- Denormalized for fast dashboard sorting; kept in sync by the app layer.
    resume_doc_id     INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    cover_doc_id      INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    referral_contact_id INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    notes             TEXT,
    archived_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status) WHERE archived_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_apps_due    ON applications(next_action_due) WHERE next_action_due IS NOT NULL;

-- Every status change is appended here — the tracker's audit trail.
CREATE TABLE IF NOT EXISTS application_events (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    occurred_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    actor           TEXT NOT NULL DEFAULT 'agent' CHECK (actor IN ('agent','human')),
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_app ON application_events(application_id, occurred_at);

-- Replies found in the operator's mailbox, read-only (ADR 0021). A
-- SUGGESTION for a person to confirm or dismiss: nothing here moves an
-- application. No body is stored; `rule` names the phrase rule that matched,
-- not text from the mail.
CREATE TABLE IF NOT EXISTS inbox_replies (
    id              INTEGER PRIMARY KEY,
    message_id      TEXT NOT NULL UNIQUE,          -- RFC Message-ID: a re-run never duplicates
    application_id  INTEGER REFERENCES applications(id) ON DELETE CASCADE,  -- NULL: ambiguous
    candidates      TEXT,                          -- JSON application ids when ambiguous
    received_at     TEXT,
    sender_domain   TEXT,
    subject         TEXT,
    kind            TEXT NOT NULL CHECK (kind IN ('rejection','interview','offer','received','other')),
    suggested_stage TEXT,
    rule            TEXT,
    state           TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','confirmed','dismissed')),
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- What was actually submitted, fixed when `jsa applied` runs (ADR 0012).
-- applications.resume_doc_id means "latest drafted" and moves on every
-- redraft; this does not. One row per kind, written once, never updated: a
-- kind missing here was not sent, or was not said to be.
CREATE TABLE IF NOT EXISTS submitted_documents (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('resume','cover_letter')),
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    version         INTEGER NOT NULL,
    approved        INTEGER NOT NULL CHECK (approved IN (0,1)),  -- at submission
    sha256          TEXT,                          -- of the file at submission; NULL if it was missing
    submitted_at    TEXT NOT NULL,
    UNIQUE (application_id, kind)
);

CREATE TRIGGER IF NOT EXISTS trg_submitted_is_permanent
BEFORE UPDATE ON submitted_documents
BEGIN
    SELECT RAISE(ABORT, 'what was submitted is a record of the past; it is never edited');
END;

-- Keep applications.last_activity_at honest without app-layer bookkeeping.
CREATE TRIGGER IF NOT EXISTS trg_app_event_touch
AFTER INSERT ON application_events
BEGIN
    UPDATE applications
       SET last_activity_at = NEW.occurred_at
     WHERE id = NEW.application_id;
END;

-- ---------------------------------------------------------------------------
-- Documents: generated resumes and cover letters on disk.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY,
    job_id          INTEGER REFERENCES jobs(id) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('resume','cover_letter','portfolio_note','other')),
    path            TEXT NOT NULL,                 -- relative to the repo root
    format          TEXT NOT NULL DEFAULT 'docx' CHECK (format IN ('docx','pdf','md','txt')),
    version         INTEGER NOT NULL DEFAULT 1,
    -- Provenance: which bullets were selected, which keywords matched/missed.
    bullet_ids      TEXT,                          -- JSON array of master_profile bullet ids
    keywords_matched TEXT,                         -- JSON array
    keywords_missing TEXT,                         -- JSON array
    model           TEXT,                          -- 'claude-opus-5'
    note            TEXT,                          -- how it was produced, when that is not obvious
    coach_findings  TEXT,                          -- JSON: the resume report at draft time (ADR 0023)
    prompt_hash     TEXT,                          -- reproducibility
    generated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    approved_at     TEXT,                          -- set only by a human action
    archived_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_docs_job ON documents(job_id, kind, version DESC);

-- ---------------------------------------------------------------------------
-- Contacts and outreach (build step 6).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY,
    company_id      INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    title           TEXT,
    linkedin_url    TEXT,
    email           TEXT,
    relationship    TEXT CHECK (relationship IN ('cold','warm','alum','referral','recruiter','friend')),
    notes           TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    archived_at     TEXT
);

CREATE TABLE IF NOT EXISTS outreach (
    id              INTEGER PRIMARY KEY,
    contact_id      INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    job_id          INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
    channel         TEXT NOT NULL CHECK (channel IN ('linkedin_connect','linkedin_dm','email','other')),
    purpose         TEXT NOT NULL CHECK (purpose IN ('referral_ask','informational','follow_up','thank_you')),
    draft_body      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'drafted'
                      CHECK (status IN ('drafted','approved','sent','replied','declined','discarded')),
    drafted_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    approved_at     TEXT,
    sent_at         TEXT,                          -- set by the user after sending manually
    reply_summary   TEXT,
    archived_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_outreach_status ON outreach(status);

-- ---------------------------------------------------------------------------
-- Interview prep (build step 6).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS interview_prep (
    id              INTEGER PRIMARY KEY,
    application_id  INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    round           TEXT CHECK (round IN ('phone_screen','technical','system_design','behavioral','onsite','final')),
    questions       TEXT,                          -- JSON array of {question, why, answer_notes}
    company_brief   TEXT,
    generated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    archived_at     TEXT
);

-- ---------------------------------------------------------------------------
-- Approval queue — the human-in-the-loop gate.
--
-- HARD RULE: nothing that leaves this machine (an application submission, an
-- outreach message) may happen without a row here whose `decision` = 'approved'
-- and whose `decided_by` = 'human'. The agent may never write 'approved'.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS approvals (
    id              INTEGER PRIMARY KEY,
    subject_type    TEXT NOT NULL CHECK (subject_type IN ('application','outreach','document')),
    subject_id      INTEGER NOT NULL,
    summary         TEXT NOT NULL,                 -- what the human is being asked to okay
    -- 'superseded' is closed BY THE TOOL, never by a person: a v1 stops
    -- needing a decision the moment v2 exists, and asking a human to reject
    -- their own superseded draft was bookkeeping dressed as judgement. It is
    -- deliberately not 'approved', so nothing outbound can key off it.
    decision        TEXT NOT NULL DEFAULT 'pending'
                      CHECK (decision IN ('pending','approved','rejected','superseded')),
    decided_by      TEXT CHECK (decided_by IN ('human','tool')),
    decided_at      TEXT,
    feedback        TEXT,                          -- why rejected / what to change
    requested_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_approvals_pending ON approvals(decision) WHERE decision = 'pending';

-- The real rule: a subject may accumulate any number of decided approvals over
-- time (reject, redraft, approve), but only ONE may be awaiting a decision.
--
-- This replaces a UNIQUE on (subject_type, subject_id, requested_at), which
-- looked like duplicate protection but keyed on a second-resolution timestamp:
-- queueing a redraft within the same second as the original collided. That is
-- precisely the reject-then-re-render flow.
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_one_pending
    ON approvals(subject_type, subject_id) WHERE decision = 'pending';

-- A decision must record who made it and when. Enforced, not just documented.
CREATE TRIGGER IF NOT EXISTS trg_approval_requires_human
BEFORE UPDATE OF decision ON approvals
WHEN NEW.decision IN ('approved','rejected')
 AND (NEW.decided_by IS NOT 'human' OR NEW.decided_at IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'approvals.decision requires decided_by=human and decided_at');
END;

-- The other direction, and just as important: the tool may close a superseded
-- draft, and may never sign it as a person. Without this, 'superseded' would
-- be a hole in the audit trail rather than a distinct entry in it.
CREATE TRIGGER IF NOT EXISTS trg_supersede_is_never_human
BEFORE UPDATE OF decision ON approvals
WHEN NEW.decision = 'superseded'
 AND (NEW.decided_by IS NOT 'tool' OR NEW.decided_at IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'a superseded approval is closed by the tool, not a human');
END;

-- ---------------------------------------------------------------------------
-- Dashboard views (build step 5).
-- ---------------------------------------------------------------------------

-- New, unreviewed matches, best first.
--
-- Regional variants of one req (same dedup_key) collapse to their best-scoring
-- row, with `variant_count` reporting how many were folded in. A NULL dedup_key
-- is treated as unique to itself.
CREATE VIEW IF NOT EXISTS v_new_matches AS
WITH open_jobs AS (
    SELECT j.*,
           COALESCE(j.dedup_key, 'job:' || j.id) AS grp
      FROM jobs j
     WHERE j.archived_at IS NULL
       AND j.closed_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM applications a WHERE a.job_id = j.id)
),
ranked AS (
    SELECT o.*,
           ROW_NUMBER() OVER (
               PARTITION BY o.grp
               ORDER BY o.match_score DESC, o.discovered_at DESC, o.id
           ) AS rn,
           COUNT(*) OVER (PARTITION BY o.grp) AS variant_count
      FROM open_jobs o
)
SELECT r.id            AS job_id,
       c.name          AS company,
       r.title,
       r.location,
       r.remote,
       r.match_score,
       r.match_reasons,
       r.url,
       r.track,
       r.discovered_at,
       r.variant_count,
       -- the group a folded card stands for; the radius looks at every copy
       r.dedup_key,
       -- populated by `jsa enrich`; NULL means not yet looked at
       r.degree_required,
       r.clearance_required,
       r.years_required,
       r.tech_stack,
       r.enrichment_note,
       r.enriched_at,
       -- pay as the posting states it, for the dashboard's area analytics
       r.salary_min,
       r.salary_max,
       r.salary_period,
       r.salary_currency
  FROM ranked r
  JOIN companies c ON c.id = r.company_id
 WHERE r.rn = 1
 ORDER BY r.match_score DESC, r.discovered_at DESC;

-- The live pipeline.
CREATE VIEW IF NOT EXISTS v_pipeline AS
SELECT a.id            AS application_id,
       c.name          AS company,
       j.title,
       a.status,
       a.applied_at,
       a.last_activity_at,
       a.next_action,
       a.next_action_due,
       j.url
  FROM applications a
  JOIN jobs j      ON j.id = a.job_id
  JOIN companies c ON c.id = j.company_id
 WHERE a.archived_at IS NULL
   AND a.status NOT IN ('rejected','withdrawn','ghosted')
 ORDER BY a.last_activity_at DESC;

-- Everything waiting on the user.
CREATE VIEW IF NOT EXISTS v_awaiting_approval AS
SELECT ap.id           AS approval_id,
       ap.subject_type,
       ap.subject_id,
       ap.summary,
       ap.requested_at
  FROM approvals ap
 WHERE ap.decision = 'pending'
 ORDER BY ap.requested_at;
