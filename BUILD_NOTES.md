# Build Notes — What Was Actually Verified

Everything below was run for real in the build environment, not just written
and assumed to work. Kept here so the distinction between "proven" and "written
but untested" stays honest as the project continues.

## Verified for real

- **Postgres schema + guardrail roles** (`scripts/schema.sql`,
  `scripts/setup_db_roles.sql`): loaded into a live Postgres 16 instance. All 14
  synthetic requests seeded successfully.
- **DB-enforced guardrails, tested adversarially**: connected AS `pa_agent_role`
  and confirmed it can read requests and insert its own decisions/audit
  entries, and confirmed `UPDATE`/`DELETE` on `pa_requests`, `pa_decisions`,
  and `audit_log` are all rejected with `permission denied` — not just granted
  in theory. Same adversarial test run against `pa_admin_role`, confirming it
  can read everything (including the audit log) but cannot write anywhere.
- **Intake / completeness agent** (`src/agents/intake_agent.py`): run against
  all 14 synthetic scenarios via the real, restricted DB connection.
  14/14 correct pending_docs vs. proceeds classification
  (`tests/test_intake_agent.py`).
- **Audit logging**: confirmed the intake test run actually wrote 14 rows to
  `audit_log` with full structured detail, readable via `pa_admin_role`.
- **Policy ingestion / chunking** (`scripts/ingest_policy_pinecone.py --dry-run`):
  run against all 4 real policy documents. Header-aware chunking, effective-date
  extraction, and section-header metadata tagging all confirmed working —
  21 chunks produced across the 4 policies, each correctly tagged.
- **Full graph orchestration** (`src/orchestrator.py`, via
  `tests/test_orchestrator_routing.py`): the actual compiled LangGraph run
  end-to-end with a scripted fake model standing in for the LLM. 6/6 routing
  scenarios correct, including:
  - clean approve and clean deny pass straight through
  - a deny without a citation triggers exactly one retry, and is accepted once
    corrected
  - a deny without a citation on **both** attempts hard-escalates rather than
    ever letting an invalid denial through
  - a stated "approve" below the confidence threshold is overridden to escalate
  - malformed (non-JSON) model output escalates rather than crashing the
    pipeline
- **Real LLM reasoning + real Pinecone retrieval** (`eval/run_eval.py`, run on
  the developer's own machine with real Anthropic/Pinecone/OpenAI keys): all
  14 `eval/test_cases.jsonl` cases run through the actual decision agent.
  **14/14 decision accuracy, 4/4 citation correctness on deny cases, 0/9
  false-escalation rate.** `ESCALATION_THRESHOLD=0.75` required no tuning to
  hit this — both genuinely ambiguous cases (CGM newly-diagnosed, bariatric
  comorbidity-unclear) correctly escalated, and no clean-cut case was
  incorrectly escalated.
  - Getting here required two real fixes caught by this eval run, not
    invented in advance: (1) the ingestion script was extracting
    `service_code` as `"CPT 72148"` while requests used the bare `"72148"`,
    so retrieval silently returned zero chunks every time; (2) the Decision
    Agent's prompt never included the request's actual clinical content
    (only service code + type), so two clinically different requests for the
    same service produced identical decisions. Both are now fixed —
    `PARequest.clinical_notes` carries the case-specific content into the
    prompt.
  - A first pass also showed weak citation specificity (50%, citing bare
    section names like "Coverage Criteria" instead of the specific clause
    violated) — tightened via an explicit prompt instruction, since a vague
    citation doesn't actually satisfy CMS-0057-F's specific-reason
    requirement even when the decision itself is correct.
  - Both fixes and the tightened citation prompt were re-verified after
    adding `clinical_notes` as a real column to `pa_requests` (not just
    passed in directly from the eval JSONL) — same 14/14, 4/4, 0/9 result,
    confirming the DB layer now genuinely supports what eval already proved
    works, not just a shortcut around it.

## Not yet verified — needs real credentials

Nothing — Phase 1 is fully built and verified end-to-end, including the
Streamlit UI. All four tabs (Submit Request, Requests & Decisions, Reviewer
Queue, Audit Log) confirmed working live, on the developer's own machine,
against real Postgres, real Anthropic, and real Pinecone. The Reviewer Queue
flow specifically was confirmed via direct database inspection after a
UI-level submission — not just a backend script — with the reviewer's
decision correctly recorded under their own ID via `pa_admin_role`, distinct
from the agent's own decisions.

One real bug was caught and fixed along the way: `record_reviewer_decision`
was silently writing through `pa_agent_role` instead of `pa_admin_role` (the
underlying `record_decision` function had no `role` parameter to route
through), which would have defeated the intended separation between "the
agent decided this" and "a person decided this" even though it didn't cause
a visible failure. Fixed by adding an explicit `role` parameter, verified
adversarially the same way every other guardrail in this project has been.

## Phase 1: Complete

---

## Phase 2 (Claims / Payment Integrity) — Verified for real

- **Postgres schema + guardrail roles** (`scripts/schema_claims.sql`,
  `scripts/setup_claims_db_roles.sql`): three new/extended roles —
  `claims_agent_role` (decisioning), `claims_intake_role` (submission),
  `pa_admin_role` extended (audit + SIU/examiner reviewer decisions). All
  tested adversarially the same way Phase 1's roles were: each can do
  exactly what it should and is rejected with `permission denied` for
  everything else — including a real gap caught mid-build (`claims_agent_role`
  needed `SELECT` on `pa_requests`, not just `pa_decisions`, since the PA
  Cross-Reference Agent's lookup joins the two).
- **Synthetic claims data** (`scripts/generate_synthetic_claims.py`): 17
  claims (9 background + 8 individually eval-scored scenarios) spanning
  every outcome type — pay, deny (three distinct reasons), duplicate,
  escalate, flag_siu — with three scenarios deliberately referencing real
  Phase 1 PA decisions by their actual `request_id`, not invented ones.
- **Claim intake agent** (`src/agents/claim_intake_agent.py`): 8/8 via
  `tests/test_claim_intake_agent.py`, including correctly identifying the
  *specific* claim_id a duplicate matches, not just that one exists.
- **Rule-based fraud signal computation** (`src/tools/fraud_signals.py`):
  correctly discriminates a genuinely planted 8x-10x volume anomaly from
  normal-volume providers, verified directly against live data
  (`tests/test_fraud_signals.py`).
- **Coverage Agent** (`src/agents/coverage_agent.py`): 4/4 via
  `tests/test_coverage_agent.py` — citation-retry logic and
  forced-low-confidence-on-repeated-failure both proven with a scripted
  model (no live LLM needed for this test).
- **PA Cross-Reference Agent** (`src/agents/pa_xref_agent.py`): 9/9 via
  `tests/test_pa_xref_agent.py`, covering all five branches (matches /
  denied / missing / pending / mismatch).
- **Reconciliation logic** (`src/agents/reconciliation.py`): 8/8 via
  `tests/test_reconciliation.py` — a pure function, fully tested with
  scripted signals, no DB or LLM needed at all.
- **Fraud Agent** (`src/agents/fraud_agent.py`): 4/4 via
  `tests/test_fraud_agent.py`, using REAL computed signals (the actual 8x-10x
  anomaly) with a scripted model standing in for the live LLM call.
- **Claims Orchestrator** (`src/claims_orchestrator.py`): 5/5 via
  `tests/test_claims_orchestrator.py`. This is the first genuinely parallel
  fan-out/join in the whole project (Phase 1 was purely linear) — the test
  specifically proves `reconcile_node` runs exactly once with all three
  signals present, not once per predecessor with partial state
  (`reconcile_wrote_exactly_one_decision: before=0 after=1`).
- **Real LLM reasoning, real Pinecone retrieval, real end-to-end graph**
  (`eval/run_claims_eval.py`, run on the developer's own machine): all 8
  `eval/claims_test_cases.jsonl` cases through the actual claims graph.
  **8/8 decision accuracy, 3/3 citation correctness, 0/5 false-escalation
  rate, 0/7 false-SIU-flag rate.**
  - Getting here required diagnosing three genuinely distinct real issues,
    layered on top of each other, none of which a threshold change actually
    fixed (an early instinct to just retune `SIU_FLAG_THRESHOLD` would have
    papered over all three without fixing any of them):
    1. `pa_xref_agent`'s mismatch check compared a claim's date against the
       PA's *most recent* decision — but re-running Phase 1's eval inserts a
       fresh, later-timestamped decision row every time, so a legitimately
       pre-authorized service eventually looked "mismatched" purely because
       the PA had since been re-confirmed. Fixed by comparing against the
       *earliest* approval on file instead (`get_earliest_pa_approval_date`).
    2. Three synthetic scenarios (two clean-outcome tests plus the
       deliberate fraud-anomaly test) accidentally shared the same
       provider — since `fraud_signals` operates at the provider+service
       level, correctly flagging that provider as anomalous silently
       contaminated the two unrelated scenarios. Fixed by isolating the
       planted anomaly onto its own dedicated synthetic provider.
    3. The Fraud Agent's prompt leaned hard on caution against false
       positives ("only flag when genuinely supported... not on a borderline
       reading") and this bled into the model second-guessing an
       unambiguous, explicitly-computed `volume_anomaly=True` boolean it was
       directly handed — treating ground truth as merely a data point to
       re-derive rather than a settled fact. Fixed by rewording the prompt
       to state explicitly that the boolean is decisive on its own.
  - A fourth issue was a process mistake, not a code bug: after fixing #2,
    only the regenerated `seed_claims.sql` was handed off — the
    correspondingly regenerated `eval/claims_test_cases.jsonl` (from the same
    generator script) was not, so the eval script kept reading a stale
    provider assignment from the JSONL even after the database itself was
    correct. Worth remembering going forward: `generate_synthetic_claims.py`
    produces both files from one source and they must be updated together.

## Phase 2: Core logic complete and verified end-to-end (8/8 real eval).
UI extension built (`src/ui.py`, tabs 4-7: Submit Claim, Claims &
Decisions, SIU Queue, Claims Examiner Queue) and live-click-tested through
the actual browser, not just boot-tested:
- **Confirmed live**: Submit Claim → real-time decisioning, observed
  multiple times with real citations matching the eval run — pay, two
  distinct deny reasons (PA-missing, coverage-independent), duplicate
  detection (correctly caught a same-day resubmission), and flag_siu (the
  real planted-anomaly claim, `1d5a8aa5...`, showing the exact 8.0x
  citation from the eval run)
- **Confirmed live**: SIU Queue displays the flagged claim with full fraud
  signal detail, and submitting an investigator resolution correctly
  emptied the queue (`"Nothing flagged for SIU right now"` after
  submission) — strong evidence the reviewer-decision write succeeded and
  the query correctly excludes resolved cases
- **NOT yet tested**: the Claims Examiner Queue's reviewer flow at all
  (display or resolution) — deferred, not something to assume works by
  analogy to the SIU queue just because the code is structurally similar
- **NOT independently confirmed**: that `Claims & Decisions` shows the
  investigator's own ID (not `agent`) after the SIU resolution above — the
  queue clearing implies it, but this wasn't directly screenshotted the way
  Phase 1's equivalent check was via a direct SQL query

**Honest bottom line**: Phase 2 is not fully verified end-to-end through the
UI yet, in the same complete sense Phase 1 is. The logic layer is proven;
one specific UI path (Claims Examiner Queue) genuinely is not.

---

## Phase 2 UI extension — Verified for real

- **App boots cleanly** with all 8 tabs (4 PA + 4 Claims): HTTP 200, no
  server-side crash, no error banner, across the full default-state script
  run.
- **`list_claims_with_decisions`**: confirmed against the live DB — all 17
  claims returned correctly.
- **SIU Queue full lifecycle**: a real `flag_siu` decision written, confirmed
  it appears in `list_siu_queue`, resolved via
  `record_claim_reviewer_decision` (as an investigator, not the agent),
  confirmed it drops off the queue afterward — same rigor as Phase 1's
  Reviewer Queue test.
- **Claims Examiner Queue full lifecycle**: same test, independently, with a
  real `escalate` decision — confirmed appears, confirmed resolves and drops
  off.
- **Audit Log tab**: extended to search both PA request IDs and claim IDs
  against the shared `audit_log` table — not independently re-tested here,
  but relies on the same `get_audit_trail` function already proven working
  in Phase 1, applied to `claim_id` values that Phase 2's own agents already
  write to via `write_audit_entry`.

## Not yet verified

- **Actually clicking through the UI in a browser** — everything above was
  verified via direct function calls and a boot/crash check, the same
  limitation Phase 1's UI had before you personally tested it live. The
  Submit Claim tab's Streamlit widget wiring (dropdowns, multiselect, the
  submit button's live graph invocation) has not been exercised by an actual
  person clicking through it yet.

## Phase 2 UI: Backend logic fully verified. Needs a live human pass
(same as Phase 1's UI did) before it's genuinely complete, not just built.
