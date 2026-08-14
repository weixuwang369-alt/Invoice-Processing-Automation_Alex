# Solution Notes

Companion to `README.md` (the assignment brief, left unmodified). This is the
translation layer: what was built, why it's built this way, and what it's
worth to Acme Corp.

## Business framing

Acme's $2M/year problem breaks down into two very different failure modes:
**data entry errors** (the 30% error rate) and **process latency** (the
5-day delay from email inbox to payment). This system attacks both —
structured extraction removes transcription error entirely on machine-
readable formats, and the four-stage agent pipeline collapses "extract →
validate → get a human's attention → pay" into a single synchronous run
that a human only has to look at when something's actually wrong (rejected
or escalated), not on every invoice.

## Architecture

```
                      ┌────────────┐
  invoice file  ───▶  │ Ingestion  │  format-routed extraction (see below)
                      └─────┬──────┘
                            ▼
                      ┌────────────┐
                      │ Validation │  inventory + arithmetic + fraud checks
                      └─────┬──────┘
                            ▼
                      ┌────────────┐
                      │  Approval  │  rule-gated LLM reasoning + critique pass
                      └─────┬──────┘
                       approved? ──── no ───▶ ┌──────────┐
                            │                  │  reject  │ → ledger, no payment
                           yes                 └──────────┘
                            ▼
                      ┌────────────┐
                      │    pay     │ → mock_payment() + ledger
                      └────────────┘
```

Orchestrated with LangGraph (`src/orchestration/graph.py`) — `approve` has a
real conditional edge, not an if-statement buried in one node, so the branch
is visible in the graph structure itself.

## The ingestion routing decision — and the evidence for it

The assignment names Grok as "the core reasoning engine," but running every
input through an LLM is the wrong default. JSON, XML, and CSV invoices
already carry their schema — a parser gets them right every time, for free,
in under a millisecond. An LLM call adds latency, cost, and a new failure
mode (hallucinated fields, rate limits, network dependency) to a problem
that doesn't need reasoning to solve. The one place reasoning is genuinely
required is free-form text — `.txt` invoices and PDF-extracted text — where
labels, layout, and spelling vary invoice to invoice ("Vendor" vs "Vndr" vs
"From", "2O26" for "2026", items embedded in an email body).

So `src/ingestion/agent.py` routes structured formats to deterministic
parsers (`src/ingestion/parsers.py`, using `pdfplumber` for PDF text — no
OCR binary needed, since the sample PDFs are vector-text, not scanned
images) and reserves the LLM call for genuinely unstructured text
(`src/ingestion/llm_extractor.py`).

`eval/run_extraction_eval.py` measures this instead of just asserting it. It
runs three arms against every sample invoice — a naive regex baseline, an
LLM-only pipeline, and the hybrid router — and scores each against
hand-labeled ground truth (`eval/ground_truth.py`), reporting field accuracy,
line-item F1, and latency, both overall and broken down by source format.

Running it requires a configured provider (see Running it, below) — there's
no offline mode, so no numbers are reproduced here. The comparison that
matters is on the free-text arm: regex-only should visibly underperform
Grok on `.txt`/PDF-extracted text (inconsistent labels, typos, OCR
artifacts), while the hybrid router should hit ~100% on `json`/`xml`/`csv`
by never asking an LLM to do a deterministic parser's job. Run
`python -m eval.run_extraction_eval` with a key configured to get the real
table:

```
python -m eval.run_extraction_eval    # writes eval_report.md / eval_report.json
```

## Validation — beyond the four scenarios in the README

`src/validation/agent.py` implements the four cases in the assignment's
scenario table, plus several the sample data plants beyond them:

- **Stock checks aggregate quantities per item before comparing to stock.**
  INV-1013 splits `WidgetA` across three line items (15 + 5 + 2 = 22 against
  15 in stock); checking line-by-line would miss this entirely.
- **Arithmetic consistency**: `subtotal + tax + shipping == total`, within a
  cent. This catches a genuinely planted trap — INV-1013's total (`$22,562.80`,
  identical in both the JSON and the generated PDF) is $50 higher than
  `subtotal + tax` (`$22,512.80`) — and also flags INV-1009, where the
  stated total (`-$250.00`) doesn't reconcile with its own subtotal, and
  INV-1007, whose source CSV states `14,750 + 885 ≠ 15,525` outright.
- **Duplicate-payment protection**: a `processed_invoices` ledger table
  blocks paying an invoice number that's already been marked `paid`. INV-1004
  and INV-1004_revised share an invoice number with different totals — a
  real duplicate-billing pattern — and get caught by this. As a side effect,
  reprocessing the same invoice in a different file format (e.g. INV-1011's
  `.txt` and `.pdf`, which are the same invoice) is also caught.
- **Non-USD currency** (INV-1014, EUR) is flagged for FX review rather than
  silently compared against a USD threshold.
- **Unparseable due dates** (INV-1003's `"yesterday"`) are flagged — on an
  invoice already carrying a zero-stock item and "URGENT... wire transfer
  preferred" language, this is one more fraud signal, not just a data bug.

## Approval — the self-correction loop

`src/approval/agent.py` makes two LLM calls, not one: a draft decision, then
a critique pass that's shown the draft and explicitly told to look for
policy violations or missed issues before it becomes final
(`critique_overturned` is logged whenever the second pass disagrees with the
first). The policy itself is simple and auditable: critical validation
issues reject; any warning-level issue escalates to a human rather than
being silently approved; invoices ≥ $10K get flagged for extra scrutiny.
Both calls always go to a real provider — there's no rule-engine stand-in —
which is also why approval is the one stage that unconditionally requires a
configured API key, regardless of how the invoice was ingested.

**The displayed reasoning is not free-form model prose.** Earlier, `reasoning`
and `critique` were single unstructured strings the model wrote however it
wanted — readable for one invoice, verbose or inconsistent for the next.
`DraftDecision` and `CritiqueResult` (`src/approval/agent.py`) now each
expose a few short, separately schema-enforced fields instead (e.g.
`policy_basis`, `key_facts`, `conclusion`), each with its own instruction to
write in **ASD-STE100 Simplified Technical English** — short sentences,
active voice, no contractions, plain vocabulary — the same standard the UI
copy follows (see Front-end, below). Python, not the model, then assembles
those fields into the final labeled text (`_format_reasoning` /
`_format_critique`): a fixed `Decision: … / Policy: … / Key facts: … /
Result: …` shape every time, regardless of provider. This is what makes the
output "reproducible" in the sense that matters here — the *structure* is
guaranteed by code, not by hoping the model follows formatting instructions
consistently; only the sentence content inside each field is the model's.
`ApprovalDecision` also carries a short `summary` field (the draft's
`conclusion`, or the critique's `finding` if it overturned the draft) —
that's what Payment detail text and the Decision column's hover tooltip
show, so a quick glance gets one clean sentence instead of the full
multi-line breakdown; the full structured text is what the Approval section
of the invoice breakdown pop-up shows (see Front-end, below). Both LLM
calls also run at `temperature=0` on every provider (the OpenAI-compatible
path already did; Anthropic's was added alongside this change), which is
the standard lever for making a given input produce the same wording run
to run — not a hard guarantee with any hosted model, but the closest lever
available without giving up live reasoning for a template.

**The critique's own fields can disagree with each other, and the code no
longer trusts that blindly.** Observed live during testing, not
hypothetical: a critique response had `agrees: true` and a `finding` that
said the draft's rejection was correct, but a `final_decision` of
`approved` — internally contradictory. The pre-existing code trusted
`final_decision` unconditionally, which meant an invoice with an
unresolved critical issue got paid on a self-contradictory model answer.
`ApprovalAgent.run()` now checks for exactly that shape —
`critique.agrees` is `true` but `critique.final_decision` differs from
the draft it just said it agreed with — and forces the decision to
`escalated` instead of acting on either value. This is deliberately
narrow: a critique that *openly* disagrees (`agrees: false`) and
overturns the draft is the normal, working self-correction path and is
untouched; only the case where the critique's own answer doesn't agree
with itself gets the safety net. `_format_critique` states this
explicitly in the audit text ("The critique gave an inconsistent
answer...") rather than silently relabeling the decision, and the app
fires a dedicated `invoice_escalated` notification whenever any invoice
resolves to `escalated` — not just this case — see Notifications, below.

## Batch concurrency and duplicate detection

Batch processing (`server.py::_process_batch`) started out as a plain
sequential loop over files — one invoice's full pipeline at a time. That's
slow (each invoice can cost up to two sequential approval LLM calls, plus
an ingestion LLM call for free-text/PDF files) but it was also, incidentally,
what made duplicate-payment protection correct: `PaymentAgent._log_ledger()`
only writes to the `processed_invoices` ledger at the very end of an
invoice's own run, so by the time invoice B reached validation, an
earlier-in-the-batch invoice A sharing its invoice number had already
finished and written its ledger row — B would see it and get rejected.
Parallelizing the loop naively breaks exactly this: two same-numbered
invoices dispatched at the same time both see an empty ledger and both
sail through, which is the one failure mode this system exists to prevent
(see INV-1004 vs INV-1004_revised in Validation, above).

The fix restructures `_process_batch` into three phases instead of one
per-file loop:

1. **Ingest every file concurrently** (`ingest_only`, `src/orchestration/graph.py`)
   — pure extraction, no shared state to race on. This can be the slowest
   phase for a batch of free-text/PDF invoices, since ingestion itself is
   an LLM call for those formats — parallelizing it is a real win, not
   just a formality.
2. **Validate every successfully-ingested invoice sequentially, in
   original file order** (`validate_with_batch_duplicates`). This is the
   phase that actually fixes the race: `ValidationAgent.run()` takes an
   optional `seen_in_batch: set[str]` parameter, and `_check_duplicate()`
   now checks a new invoice number against that set (populated as this
   loop goes) in addition to the ledger. The first occurrence of a number
   in the batch passes; every later occurrence in the *same* batch is
   flagged right here, before either one ever reaches approval or payment.
   No LLM calls happen in this phase, so running it sequentially costs
   nothing — the loop over even a large batch finishes in well under a
   second.
3. **Approve and pay every validated invoice concurrently**
   (`approve_and_pay_with_progress`). Safe to parallelize because phase 2
   already resolved every batch-internal duplicate before any invoice
   reaches this point; the only remaining concurrent writer is the ledger
   UPSERT itself, now serialized with a module-level `threading.Lock` in
   `src/payment/agent.py` so the write is atomic regardless of how many
   invoices finish approval at the same moment.

`seen_in_batch` defaults to `None`, and `_check_duplicate` only checks it
when it's truthy — so `ValidationAgent.run(invoice)` with no second
argument (the CLI's `main.py --all`, the Single Invoice tab, and edit
request rechecks) is byte-identical to before this change. `main.py --all`
in particular stays a plain sequential loop and was not touched; it never
had the race to begin with.

**A deliberate behavior change worth calling out**: today, the
batch-internal check flags the *second* occurrence of an invoice number
regardless of what happened to the first one — even if the first failed
validation or was rejected, not just if it was paid. Previously (checking
only the ledger, which is only written at the end of a pipeline run), a
"corrected resubmission" pattern — a broken invoice followed later by a
fixed version of the same invoice number, in the same batch — could still
get the second one paid, since the first one's rejection never reached the
ledger before the second one validated. Under the new rule that resubmission
is rejected as a batch duplicate instead. This is stricter, and deliberate:
resolving the same invoice number twice inside one batch run reads as
suspicious regardless of which copy looks more correct, and a genuine
correction can simply be run as its own follow-up batch, after the first
one lands in the ledger. On the bundled sample data this makes no
difference — every duplicate-numbered pair (INV-1004/INV-1004_revised,
INV-1011's `.txt`/`.pdf`) has its first occurrence paid either way.

Two smaller UI-facing consequences of running phases 1 and 3 concurrently:
`results` in a batch job's status now arrive in actual completion order,
not necessarily original file order; and the live batch status bar's
"current file"/stage fields mean "the most recently updated of however
many are in flight right now" rather than "the one file currently
running" — up to four can update it in the same second. (Stop's own
behavior changed since this was first written — see "Stopping a batch
immediately," below — it no longer waits for in-flight invoices to finish.)

Verified directly against the real duplicate-payment scenario rather than
just unit tests, since this is the one part of the system where a bug
means actual double payment: ran `invoice_1004.json` and
`invoice_1004_revised.json` (same invoice number, different totals) as one
batch against a fresh ledger — exactly one ledger row landed as `paid`,
the other was rejected with the new batch-duplicate message, and the mock
payment API fired exactly once. Re-running the identical batch without
resetting the ledger correctly rejected both, this time via the existing
"already paid" ledger message.

## Stopping a batch immediately

The original Stop design let whatever was already in flight (up to four
invoices at a time) finish naturally before the job transitioned to
`stopped` — described above as "gates new work, doesn't interrupt what's
running." That turned out to be the wrong default: on a batch of mostly
free-text/PDF invoices, "already in flight" could mean waiting several
more seconds for LLM calls that were about to produce invoices the user
had just asked to stop processing. Two separate problems, addressed
separately:

**Responsiveness — the job should say `stopped` almost immediately, not
after draining whatever's mid-call.** `_process_batch`'s phase 1 and phase
3 used to consume completions via `as_completed(futures)`, which blocks
until the *next* future resolves — meaning a Stop click was only noticed
between completions, which could be several seconds apart. Both phases now
drain through a new `_drain()` helper that polls with `concurrent.futures.wait(...,
timeout=_STOP_POLL_INTERVAL)` (0.2s) in a loop, re-checking `should_stop()`
every iteration instead of blocking on the next result. The moment a stop
is seen, `_drain()` returns immediately without waiting for whatever's
still pending, and the executor is shut down with `shutdown(wait=False,
cancel_futures=True)` instead of the `with`-block default (`wait=True`),
which would otherwise block exiting the function until every submitted
future — including ones just abandoned — finished. `_process_batch` also
now checks `should_stop()` *between* phases, not just within one: a stop
mid-ingestion skips validation and approval entirely rather than running
them against invoices about to be discarded anyway.

**Safety — nothing already in flight should reach payment.** Python can't
interrupt a thread mid-HTTP-call, so an approval's draft/critique LLM
calls that were already running when Stop was clicked still complete in
the background — abandoning the future doesn't kill the thread, just
stops *waiting* for it. The real requirement isn't "stop the LLM call
instantly" (not achievable without a much larger async rewrite), it's
"never let that invoice get paid or written to the ledger." So
`approve_and_pay_with_progress` (`src/orchestration/graph.py`) gained a
`should_stop` parameter, checked exactly once — right after approval
finishes, immediately before payment — raising a new `BatchStopped`
exception instead of proceeding. `server.py`'s `approve_one` catches it
and returns the same "skipped" sentinel already used for invoices that
never started, so a stopped-mid-approval invoice is purged identically to
one that was still queued: not logged, not counted, not shown, no ledger
row, no `mock_payment` call. An invoice whose approval call happened to
finish *before* should_stop() became true is unaffected — it completes
normally, exactly as if the batch hadn't been stopped.

Verified on an isolated instance against a real batch and a real LLM: a
20-invoice batch was stopped mid-flight, and the job reported `stopped`
within one polling cycle (well under a second) rather than waiting on
whatever was still running. Checking the ledger immediately after showed
exactly the invoices that had genuinely completed before the stop — then,
after waiting 15 more seconds for any abandoned background LLM calls to
finish naturally, the ledger was queried again and was byte-for-byte
identical: nothing snuck in late. The Action Log confirmed several
abandoned invoices' draft/critique calls *did* complete in the
background (wasted, not free) but correctly produced zero payments.
Repeated live in the browser, stopping a batch seconds after starting it
(still mid-ingestion): the UI dropped to `Idle` within about a second, 0
processed, and the ledger had zero rows. Two new unit tests
(`tests/test_graph_progress.py`) pin the core guarantee directly: calling
`approve_and_pay_with_progress` with a `should_stop` that returns `True`
raises `BatchStopped` and leaves no row in `processed_invoices`; with
`should_stop` returning `False`, it completes and pays normally.

## Keeping the tabs bar from wrapping

At a narrower window width, the tab labels themselves used to wrap onto a
second line — "Processed Invoices" splitting into "Processed" / "Invoices"
stacked — which grew the whole nav bar taller and shifted the page content
underneath it every time the window (or just the count of tabs, after
Action Log was added) didn't quite fit. `.tab` had no `white-space` rule,
so ordinary text wrapping took over wherever a button ran out of room.

Fixed with the standard pattern for a tab bar that might not fit: the
seven tab buttons are wrapped in a new `.tab-scroll` container
(`static/index.html`) with `white-space: nowrap` on `.tab` (labels can no
longer wrap, ever) and `overflow-x: auto` on the wrapper (if they still
don't fit, the tab row scrolls horizontally instead of growing vertically).
`.tab-scroll` needs `min-width: 0` — the classic flexbox trap where a flex
child otherwise refuses to shrink below its content's natural width,
which would just push the row wider than the viewport instead of
scrolling. Deliberately scoped to only the tab buttons, not the whole nav
row: mini-status and the notification bell sit outside `.tab-scroll` as
plain flex siblings, so they stay fixed at the right edge and fully
visible even when the tabs themselves are scrolled — `margin-left: auto`
on `#mini-status` still works exactly as before, since it only cares that
it's a flex child of `.tabs`, not what's immediately to its left.

Verified by shrinking the effective content width (both a real narrow
browser window and a scripted style override for a precise, repeatable
check) and reading the DOM directly: `.tab-scroll` reported a `scrollWidth`
of 855px against a `clientWidth` of 600px — genuinely overflowing and
scrolling, not just visually cramped — while `#mini-status`'s
`getBoundingClientRect()` stayed fully inside the 760px viewport,
unaffected. Tab clicks still worked normally in the narrow state, and the
header's height stayed constant regardless of window width.

## Front-end

`server.py` is a FastAPI backend that wraps the *exact same* LangGraph
pipeline the CLI uses (`main.process_invoice`) — it's a second entrypoint
into the same agents, not a reimplementation. `static/` is a vanilla
HTML/CSS/JS single-page app with no build step and no CDN dependencies
(everything self-contained, in keeping with the assignment's "no internet
for external APIs" spirit) served straight off disk.

Six views: **Single Invoice** (pick an invoice from the main invoice
folder or drag-and-drop upload → the same per-stage detail as the CLI
panel, rendered as cards), **Batch Invoices** (see below), **Processed
Invoices** (see below), **Edit Requests** and **Edit Approvals** (see
"Manual edits" below), and **Inventory** (live stock levels from
`inventory.db`).

**Processed invoices** (`GET /api/processed-invoices`) is a read-only
history table across *every* invoice the app has run, from either Single
Invoice or Batch Invoices. It's built on data that already existed —
`src/observability/logger.py`'s per-invoice JSON run log in `logs/` — with
no new persistence layer: the log's filename (timestamp + source stem) is
already unique, so it doubles as a stable `run_id` with no extra ID
generation needed. `_serialize()` in `server.py` now threads that
`run_id` through both `/api/process/start` and the batch pipeline, so the
same id a result carries the moment it's produced is exactly the id the
history table and the breakdown pop-up (below) use to look it up again
later. This tab also refreshes itself live: whenever a Single Invoice job
or a Batch Invoices job produces a new result, `refreshProcessedInvoicesIfVisible()`
re-fetches the table if this tab happens to be the one currently open —
a completed invoice shows up here without the user having to click away
and back.

The results table (used by Batch Invoices, Processed Invoices, and the
Edit Requests "View result" link — one `renderInvoiceTable()` in
`app.js`, not several copies) does five things beyond just listing rows:
- Hovering **Validation**, **Decision**, or **Payment** in the header
  shows what that column means, via the browser's native `title` tooltip
  — no tooltip library needed.
- Hovering a red or amber **value** (a validation failure, a
  rejected/escalated decision, a withheld payment) shows *why*: the actual
  validation issue messages, the approval agent's reasoning (plus the
  critique's reasoning if it overturned the draft), or the payment
  agent's detail string. A passing/approved/paid value carries no tooltip
  — there's no "why" to explain for a positive outcome.
- Clicking any row opens `static/invoice-detail.html?id={run_id}` in a
  genuine new browser window (`window.open(..., "_blank", "width=…,
  noopener")`, not a same-page modal, per how this was asked for). That
  page is a small standalone app in its own right — its own JS
  (`static/invoice-detail.js`) fetches `GET
  /api/processed-invoices/{run_id}` and renders vendor, a line-item table,
  and the subtotal/tax/shipping/total arithmetic that produced the total
  (the same fields `src/validation/agent.py`'s arithmetic check already
  reasons over), plus the full validation issue list and the approval
  agent's draft/critique reasoning — a genuine "how was this number
  reached," not just a re-display of the row it was opened from. Because
  every row everywhere links through the same `run_id` scheme, a Batch
  Invoices row and a Processed Invoices row for the same invoice open
  the identical breakdown.
- An **Edit** button per row opens that same pop-up directly in edit mode
  (`?edit=1`) instead of view mode — see "Manual edits and approvals,"
  below.
- A **Processed At** column shows exactly when — `r.processed_at`
  (already present on every row; nothing new to compute) rendered with
  `toLocaleString()`, the same formatting already used for timestamps in
  the Edit Requests/Approvals tables and the notification panel, so dates
  don't look formatted three different ways across the app.

**The batch summary's six stat cards (Processed, Approved, Rejected,
Escalated, Approved Payments, Flagged or Withheld) always sit in one
row.** `.stat-row` used to be `repeat(auto-fit, minmax(160px, 1fr))`,
which wrapped to two rows on anything narrower than roughly 960px of
content width — a fixed `repeat(6, 1fr)` guarantees one row regardless of
viewport, at the cost of the cards being able to shrink further than
before. A two-word label like "Approved Payments" wraps to two lines
in that narrower card rather than truncating with an ellipsis — tried the
ellipsis first, it read worse than just letting the label wrap.

**The main invoice folder** (`src/invoice_folder.py`) is a single concept
both the Single Invoice dropdown and Batch tab read from — it defaults to
the bundled dataset (`data/invoices/`) and can be changed from the Batch
tab: choose a folder via a native OS folder picker, optionally check "Set
as main invoice folder," and process. When checked, the uploaded files are
copied into a server-managed directory (`uploaded_invoices/`, gitignored)
and that becomes the new default everywhere — `/api/invoices`,
`/api/batch/start`, and the CLI's `--invoices-dir` default — until reset
back to the bundled dataset. The Batch tab intentionally has only one
control surface for "what gets processed": a folder picker, not a picker
plus a separate hardcoded "process the samples" button. With no new folder
chosen, "Process Folder" re-runs whatever the current main folder is —
so it also serves as the plain re-run-everything action.

**Batch jobs run on a background thread, not the request thread.** A
folder can hold hundreds of invoices, each needing one or two LLM calls, so
`POST /api/batch/start` (main folder) and `POST /api/batch/start-upload`
(a newly-chosen folder) return immediately with a job id and spawn a daemon
thread that does the actual processing (`server.py`'s `_run_batch_job`).
The frontend polls `GET /api/batch/status/{id}` roughly once a second and
renders a status bar showing which invoice is currently running and how
many of the total are done. `_process_batch` takes an `on_progress`
callback and a `should_stop` check so the same per-file loop backs both the
polling status and the Stop button — clicking Stop calls
`POST /api/batch/stop/{id}`, which sets a flag the job thread checks
*between* invoices, so whatever already completed is kept (results are
already written to the ledger per-invoice as they finish; nothing about a
stop is retroactive). Only one job runs at a time — starting a second while
one is `running` gets a 409 — but if the page reloads mid-run,
`GET /api/batch/current` lets it find and reattach to that job instead of
losing track of it. For an upload-started job, the chosen folder is copied
into `uploaded_invoices/` (and promoted to "main," if requested)
**before** the background thread starts, so that promotion survives even
if the job is stopped one file in; the job's own temp copy of the uploads
is cleaned up by the job thread itself once it finishes, not by the request
handler, since the request has already returned by then.

Folder ingestion is a genuine second batch entrypoint
(`POST /api/batch/start-upload`), not a variant of the main-folder path —
it shares the same per-file try/except and result rendering as
`/api/batch/start` so one bad file in the folder doesn't fail the batch,
and unsupported files (e.g. a stray `.py`) are filtered client-side and
reported back rather than erroring the whole request.

**Live per-stage progress.** Both Single Invoice and Batch Invoices show
which pipeline stage the current invoice is on (Reading, Checking, Getting
an approval decision, Processing payment), not just which file is running.
The compiled LangGraph has no per-node progress hook, so
`src/orchestration/graph.py` adds `run_with_progress()` — it calls the
exact same node functions the graph uses (`_ingest_node`, `_validate_node`,
…), one at a time, firing an `on_stage(name)` callback immediately before
each one starts. Business logic still lives only in the node
functions/agents; `build_graph()` and `run_with_progress()` are just two
ways to sequence the same four calls, so they can never drift apart in
behavior. Single Invoice processing became a background job for exactly
this reason: a single blocking `POST /api/process` request has nowhere to
push an intermediate stage update to, so it's now
`POST /api/process/start` + polled `GET /api/process/status/{id}`, mirroring
the batch job pattern that already existed. Inside a batch, `_process_batch`
now also accepts `on_stage`, `on_result`, and `on_error` callbacks —
`on_result`/`on_error` append into the job's `results`/`errors` lists
*immediately*, not only once the whole batch finishes, which is what makes
the next feature possible.

**The Batch Invoices table builds up live.** `handleJobUpdate()` in
`app.js` now calls `renderInvoiceTable()` on every poll tick while the job
is still `"running"`, using whatever's in `job.results` so far — not just
once at the end. Combined with the incremental `on_result` callback above,
a completed invoice appears in the table within one poll interval (900ms)
of finishing, while the rest of the batch keeps running underneath it.

## Manual edits and approvals

A processed invoice's fields aren't fixed forever. From the breakdown
pop-up, or the Edit button on any row in Processed Invoices or Batch
Invoices (including the live-building table above), a user can propose a
change to one or more fields — vendor, invoice number, dates, line items,
subtotal/tax/shipping/total, currency. That proposal is not applied
immediately: it's stored as a pending `EditRequest`
(`src/edit_requests.py`, a small gitignored JSON file store following the
same pattern as `key_store.py`/`invoice_folder.py`) and only takes effect
once approved.

**The approval is a deliberate mock.** There's no second user in this
system, so the same person who proposes an edit also approves it. Rather
than pretend otherwise, the UI says so out loud, in the exact place the
user encounters it: the notification fired when an edit needs approval
reads *"For this demo, you approve your own edit requests. In a real
deployment, a separate approver would do this,"* and the same sentence
appears as a banner on the Edit Approvals tab. **Edit Requests** is a
full history of everything submitted, whatever its status; **Edit
Approvals** is the actionable queue — pending requests only, with
Approve/Reject buttons. Both buttons disable themselves the instant
they're clicked (and the server rejects a second decision on an
already-decided request with `409`), so a double-click can't trigger two
rechecks of the same edit.

**Approving an edit reruns validation → approval → payment, not
ingestion.** The raw file was already extracted once; re-parsing it would
throw away the very correction the edit exists to make. Instead,
`recheck_with_progress()` (`src/orchestration/graph.py`) takes the
already-`ExtractedInvoice`, with the edited field(s) substituted in, and
runs the same `_validate_node → _approve_node → _pay_node/_reject_node`
sequence `run_with_progress()` uses, just starting one stage later. The
result is written as a **new** run log — a new `run_id`, linked back to
the original via `edited_from` — so the original run stays in the audit
trail unchanged and the corrected run appears alongside it in Processed
Invoices. `POST /api/edit-requests` diffs the *pydantic-coerced* dumps of
the original and edited invoice (not raw form strings) before storing
`changes`, specifically so `"1890"` vs `1890.0` doesn't show up as a
spurious change on every untouched numeric field.

**Approval runs as a background job, same as Single Invoice and Batch,
with one stage the other two don't have.** Rechecking is a real recheck —
two LLM calls, the same latency as processing one invoice — so
`POST /api/edit-requests/{id}/approve` no longer blocks the request until
it finishes; it starts a job and returns immediately, polled via
`GET /api/edit-jobs/{job_id}` exactly like `/api/process/status/{id}` and
`/api/batch/status/{id}`. Its first reported stage is a synthetic
`recheck` — fired manually right before `recheck_with_progress()`, which
then fires its own `validation`/`approval`/`payment` stages same as
always — specifically so the UI can say *"Passing the edited invoice back
through the system"* the instant Approve is clicked. That sentence exists
because this feature grew directly out of a user question about whether
editing genuinely re-enters the pipeline or just silently overwrites a
field; showing the handoff as its own first-class stage, not just
inferring it from "validation is running," answers that visibly rather
than by explanation. The endpoint guards against a second approve on the
same edit request while the first is still mid-recheck (a `409`,
`_edit_jobs` now tracks `edit_request_id` per job) — not just the
existing "already decided" guard, since the request stays `pending` in
the store until the job finishes, so a user switching tabs away and back
mid-recheck would otherwise see fresh, clickable Approve/Reject buttons
on a request that already has a job running against it.

This surfaces a real edge case worth calling out rather than hiding:
editing an invoice that's already `paid` will reliably come back
`rejected` on recheck, because the duplicate-payment check
(`processed_invoices` ledger, keyed by invoice number) sees the same
invoice number already marked `paid` and correctly refuses to pay it
twice. That's the ledger working as designed, not a bug in the edit
feature — see Known limitations for the one case (editing the invoice
*number* itself) where that protection has a gap.

## Mini status indicator

Three different actions can now be "in progress" with their own live
per-stage status bar — Single Invoice, Batch Invoices, and an edit
approval's recheck — but each status bar only exists inside its own tab.
Start a batch, switch to Inventory to poke around while it runs, and the
full status bar is gone from view even though the job is still working.
A compact indicator next to the notification bell, visible from every
tab, closes that gap: a small pill showing the current stage, using the
exact same `STAGE_LABEL` text as the full bars (`static/common.js`) — not
a simplified or re-worded version, so it never says something different
from what the tab it's mirroring says.

**It's never hidden.** The first version disappeared entirely when
nothing was running; now it always shows something — "Idle," with a
static gray dot instead of the pulsing accent-colored one — so its
absence never has to be interpreted as "nothing happening" versus
"broken." The dot's own color/animation is the only thing that changes
between states (`.mini-status.idle .status-dot`), not the indicator's
visibility.

**It also names the file.** `updateMiniStatus(source, stage, fileName)`
takes a third argument now, rendered as `"{stage label}: {file name}"` —
matching the same `"stage: file"` shape the full status bars already use
(`stageStatusHtml()`). The batch job already tracked `current_file`;
Single Invoice and the edit-recheck job didn't expose a filename in their
serialized job at all, so both `_serialize_single_job()` and
`_serialize_edit_job()` (`server.py`) gained a `file` field, computed
once at job creation (the same `display_name` already used elsewhere,
falling back to the on-disk basename) rather than re-derived on every
poll.

It doesn't run its own poller. Each of the three job types already polls
its own status every 900ms; `updateMiniStatus(...)` is just one more call
inside each of those three update handlers (`handleSingleJobUpdate`,
`handleJobUpdate`, `handleEditJobUpdate`), fed the same `job.stage`/`job.file`
values the full bar just rendered. A `null` stage means that source's job
isn't running. `renderMiniStatus()` then picks the first non-null source
in a fixed order (edit recheck, single invoice, batch) and shows its
label, falling back to "Idle" only when all three are null — so one job
finishing doesn't blank out (or overwrite the file name of) a different
job that's still running. In the CSS, the indicator itself carries the
`margin-left: auto` that pushes it (and the bell immediately after it) to
the right edge of the tabs row — this no longer depends on a hide/show
toggle to work correctly now that the indicator is permanently visible,
but the reasoning is unchanged from when it did.

**It's clickable, and jumps back to where the job was started.** An edit
recheck is only ever kicked off from Edit Approvals, a single-invoice job
only from Single Invoice, a batch job only from Batch Invoices — so
`MINI_STATUS_TAB` (`static/app.js`) maps each of the three sources to
exactly that tab, and a click on the indicator calls `switchToTab()` with
whichever source is currently showing. Idle has no source to jump to, so
the indicator drops its `cursor: pointer` and the click handler is a
no-op — clicking "Idle" does nothing rather than navigating somewhere
arbitrary.

## Batch status dropdown

The batch status bar shows one line — the most recently updated stage and
file — which stopped being the whole picture once batch processing became
concurrent (see "Batch concurrency and duplicate detection," above): up to
four invoices can be genuinely in flight at once, each on a different
stage, and the one-line bar can only ever show the latest of them. The bar
is now clickable (`static/app.js`, `toggleStatusDropdown`) and opens a
dropdown listing every invoice currently in flight and its own stage,
sourced from a new `job.in_progress` field the server tracks per file.

The tracking itself lives in `server.py`'s `_run_batch_job`: `on_stage`
gained an optional second parameter, `file_name`, and now records
`job["in_progress"][file_name] = stage` whenever it's given one, in
addition to its existing `job["stage"]` side effect. `_process_batch`
passes a file name at all three points that already call `on_stage` for a
specific invoice — phase 1's `ingest_one`, phase 2's per-file validation
loop, and phase 3's `approve_one` (wrapped in a small closure, since
`approve_and_pay_with_progress` in `src/orchestration/graph.py` still
calls `on_stage(stage)` with one argument — that function stays
file-agnostic on purpose, so a single-invoice caller never has to know
this parameter exists). An invoice drops out of `in_progress` the moment
it reaches `on_result` or `on_error`, so the dropdown always reflects
exactly what's still running, clearing entries live as the batch
progresses — never a stale list of already-finished invoices. Rendering
follows the same open/close-on-outside-click pattern as the notification
bell's panel (`static/app.js`), and re-renders on every 900ms poll only
while the dropdown is actually open.

## Notifications

A bell icon sits at the right edge of the tabs row (`static/index.html`;
deliberately not a `.tab`, so the existing tab-click handler's
`$$(".tab")` selector doesn't try to treat it as a panel switch). Clicking
it opens an in-page dropdown panel — not a new page, not a pop-up window,
per how this was asked for (contrast with the breakdown pop-up above,
which genuinely is a new window). `src/notifications.py` is a small
in-memory feed — the same tradeoff as the `_jobs` batch-job store: fine
for a single local user, doesn't survive a restart (see Known
limitations). Four things notify: a Single Invoice job completing, a
Batch Invoices job completing (see below), an edit request entering
`pending` status, and — separately from routine completion — any invoice
that resolves to `escalated`.

**Clicking a notification takes you to where you'd act on it**, rather
than just telling you something happened. `NOTIFICATION_TAB` maps each
notification `type` to a tab — `invoice_completed` (Single Invoice,
Batch, or an edit recheck alike) to Processed Invoices; `invoice_escalated`
and `edit_pending` both to Edit Approvals, since both describe something
waiting there for a decision. The tab-switching logic itself (previously
only reachable by clicking a `.tab` button directly) is now the shared
`switchToTab(tabName)`, so a notification click and a tab click do
exactly the same thing, including re-running that tab's load function —
clicking a `invoice_completed` notification doesn't just flip to
Processed Invoices, it also refreshes it. Notification types with no
mapped tab (there are none currently, but the mapping is a lookup, not
an exhaustive switch) simply render without the click affordance rather
than erroring.

**Escalation notifies unconditionally, on top of the routine one.** A
routine `invoice_completed` notification already respects
`batch_notify_mode` (per-invoice, or one summary at the end). Escalation
doesn't: `_notify_if_escalated()` fires an `invoice_escalated`
notification for every escalated invoice regardless of that setting,
because "the application withheld payment and a person needs to look at
this" shouldn't get silently folded into a batch summary count just
because the user picked the quieter notification mode. It fires from all
three places an invoice can finish — the single-invoice job, each batch
result, and an edit-request recheck — and renders with a distinct amber
left-border accent in both the panel and the bottom-right toast, so it
doesn't read as identical to a routine completion.

**Batch notifications are configurable.** A new Settings field,
`batch_notify_mode` (persisted via `key_store.py`, same no-clobber-on-blank
pattern as the provider/model fields), picks between notifying once when
the whole batch finishes, or once per invoice as each one completes —
the latter fires the full treatment (sound, toast, panel entry) for every
single invoice in the batch, not just an aggregate count.

Every notification does three things: a short synthesized beep (Web
Audio API — a couple of oscillator/gain nodes, not an audio file, so
there's no binary asset to ship), a bottom-right toast that fades out on
its own (`#notification-toasts`, deliberately separate from the existing
top-center `#toast` used for one-off save/error messages — different
part of the screen, different lifetime), and a line in the bell panel.
Two browser realities shaped the polling client in `app.js`:

- `AudioContext` starts **suspended** until a user gesture. A
  notification that arrives before the user has clicked anything (e.g.
  reattaching to an already-running batch on page load) would otherwise
  fail silently. A one-time `document.addEventListener("click", …, {once:
  true})` primes the context on the first click anywhere; sound is
  treated as best-effort everywhere else — if the context isn't ready
  yet, the toast and panel entry still happen, just without the beep.
- The **first** `GET /api/notifications` after page load only seeds
  `lastSeenNotificationId` — it does not play sounds or show toasts for
  anything already in the feed. Without that guard, opening the app after
  a batch of 20 finished overnight would replay a 20-notification beep
  storm on load instead of just showing the history quietly in the panel.

## Action log

A new tab that answers "what did the model actually see and say" — every
call the application makes to the configured LLM provider, in one place:
ingestion extraction (free-text/PDF invoices), and both approval passes
(draft, critique). Each entry records the provider/model, a short
"purpose" label, the invoice or file it was for, a preview of the prompt
sent, and either the model's structured result or the error if the call
failed.

**Logging lives at the call sites, not inside `LLMClient`.** `src/llm_client.py`
has exactly one job — talk to whichever provider is configured — and
doesn't know or care whether a given `complete_structured()` call is "an
approval draft" or "reading a free-text invoice." Only the caller knows
that, so `src/action_log.py` (a new module, deliberately shaped like
`src/notifications.py` — same in-memory list, same `record`/`list_all`/
`clear_all` surface) is called from the three actual call sites instead:
`extract_with_llm()` (`src/ingestion/llm_extractor.py`) wraps its one call,
and `ApprovalAgent.run()` (`src/approval/agent.py`) wraps both of its
calls through a small local `call()` closure so the try/log/re-raise logic
isn't duplicated for draft and critique. A failed call is logged too — the
`except` branch records the error and re-raises unchanged, so this is
purely observational and never swallows or alters a real failure.

**Context threads from where it's actually known.** `IngestionAgent.run()`
passes the file's basename down into `extract_with_llm(text, context=...)`
as a new optional parameter; `ApprovalAgent.run()` derives it from the
invoice itself (`invoice_number`, falling back to `vendor`). Neither
`LLMClient` nor `action_log.py` has to know what an "invoice" is — they
just pass a string through.

In-memory, like notifications and batch jobs — consistent with this
project's existing single-user-local-tool tradeoff (see Known
limitations) — and cleared by Full Reset alongside the rest of the
processing history (`action_log.clear_all()`, wired into
`/api/full-reset`). No pagination, same as Processed Invoices; fine for a
demo's worth of runs.

## Edit Requests / Edit Approvals tabs

Two thin read-mostly views over `src/edit_requests.py`'s store, split by
what the user is there to do:
- **Edit Requests** — everything submitted, whatever its status (pending,
  approved, rejected), a change summary (`field: old → new`), and a "View
  result" link once approved that opens the new run's breakdown.
- **Edit Approvals** — pending requests only, with the caveat banner and
  Approve/Reject buttons described above.

## Escalation resolution

An `escalated` `ApprovalDecision` means the model itself couldn't
confidently approve or reject — the app withholds payment
(`PaymentResult.status = "skipped"`) and needs a person to make the final
call. That queue lives in the Edit Approvals tab too, in a section above
the edit requests: "the escalation to human... should go into the edit
approvals tab, and should have an approval or denial button," per how
this was asked for.

Escalated invoices get no store of their own. An invoice's own
`approval.decision` field already IS the pending/resolved state, so the
tab just filters the same `GET /api/processed-invoices` list the
Processed Invoices tab reads (`r.approval.decision === "escalated"`) —
consistent with the "no new persistence layer" pattern used everywhere
else in this app. Resolving one — `POST /api/escalations/{run_id}/approve`
or `/deny` — does not create a new run the way approving an *edit*
does; nothing about the extracted invoice data changed, only who made
the final call, so `_resolve_escalation()` (`server.py`) overwrites that
same run's `approval`/`payment` sections in place and calls
`PaymentAgent.pay()` or `.log_rejection()` directly (approve actually
pays; deny withholds payment for real, not just cosmetically). Guarded
the same way as edit-request approval: a 409 if the invoice isn't
currently `escalated` (already resolved, or was never escalated), and
the frontend disables both buttons the instant either is clicked.

The original AI reasoning (`reasoning`, `critique`) is left untouched —
it is a historical record of *why the model escalated*, not something a
human resolution should overwrite. A new `ApprovalDecision.escalation_resolution`
field carries the human's part of the story instead: `_format_escalation_resolution()`
(`src/approval/agent.py`) is plain Python (no LLM call — resolving an
escalation is a human action, not a model stage) producing "A person
reviewed this escalated invoice and approved/rejected it. For this demo,
the user makes this decision. In a real deployment, the appropriate
approver would decide." — the same caveat framing used everywhere else
self-approval shows up in this demo. That field, once set, becomes the
new preferred "reason" text throughout the UI: `PaymentAgent.log_rejection()`
prefers it over the model's original `summary` for the Payment detail
line, `decisionReason()` prefers it for the Decision column's hover
tooltip, and the invoice breakdown pop-up shows it as a third
"Human resolution" block alongside Draft and Critique.

**A live-testing find, fixed along the way, unrelated to escalation
logic itself:** resolving an escalated invoice that had been uploaded
(not chosen from the main folder) surfaced a pre-existing bug — the
notification and the resolved record both showed a meaningless
server-generated temp filename (`tmp1ujrj68i.json`) instead of the file
the user actually uploaded. The immediate response right after
processing already showed the real name correctly (`_serialize()`'s
`display_name` parameter), but that name was never *persisted* into the
run log — `write_run_log()` only stored `source_file`, always the
server's temp path for an upload. Any *later* lookup (Processed
Invoices, the breakdown pop-up, this new escalation flow) fell back to
that temp path's basename. Fixed by adding a `display_name` field to the
run log record itself (`src/observability/logger.py`), threaded through
every `write_run_log()` call site that has one, with a shared
`_display_file()` helper in `server.py` so `list_processed_invoices()`,
`get_processed_invoice()`, and `_resolve_escalation()` all read it back
the same way. Log records written before this fix have no `display_name`
key and fall back to the old (temp-name) behavior — verified this
doesn't crash, just shows the less-friendly name for pre-existing runs.

## Full reset

`POST /api/inventory/reset` ("Reset Inventory and Ledger," on the Batch
Invoices tab) only ever touched `inventory.db` — restocking `inventory`
and wiping the `processed_invoices` duplicate-payment ledger, which is
all it needs to do between batch runs. It was never meant to clear
history: Processed Invoices reads `logs/*.json` (a separate store — see
Front-end, above), so those runs stayed visible after a reset, which
read as a bug from the outside ("why doesn't Reset clear everything?")
even though it was working exactly as designed.

Rather than change what that button does, a second, more thorough action
lives in Settings: **Full Reset**. `POST /api/full-reset` does the same
inventory/ledger reset, and also deletes every file in `logs/`
(`_clear_logs()`), clears `edit_requests.json`
(`edit_requests_store.clear_all()`), and empties the in-memory
notification feed (`notifications.clear_all()`) — everything the app has
ever recorded, in one action. It refuses to run while a batch or a
single invoice is still processing (`409`, same guard style as starting
a second batch) rather than deleting out from under a job that's about
to write a new log file.

This is the most destructive action in the app, so it doesn't get a
single click. Clicking "Full Reset…" reveals a type-to-confirm field —
the "Delete everything" button stays disabled until the user types
`RESET` exactly — rather than a native browser `confirm()`, both for
visual consistency with the rest of the app's custom UI and because a
blocking native dialog cannot be dismissed by the browser-automation
tooling used to verify this feature, unlike every other confirmation in
this app. On success every view that reads processed-invoice history,
edit requests, or notifications refreshes immediately — regardless of
which tab is currently active — rather than waiting for the user to
click away and back.

**A real, unrelated bug surfaced by this feature, fixed along the way:**
adding the Full Reset section made the Settings modal tall enough to
expose a pre-existing layout defect — `.modal` had no `max-height` or
`overflow-y`, so on a browser window shorter than the modal's natural
height, it overflowed both the top and bottom of the viewport with no
way to scroll to the clipped content. The "Delete everything" and
"Cancel" buttons landed below the fold and were completely unreachable.
Fixed with `max-height: calc(100vh - 48px); overflow-y: auto;` on
`.modal`, verified by shrinking the browser window and confirming the
modal now scrolls internally with both buttons reachable.

## Editable inventory

The Inventory tab's stock, unit price, and category are freely editable
in place — every row is a set of `<input>`s, not static text, and a
"Save Changes" button (disabled until something actually changes) writes
them all in one `POST /api/inventory/update` call. This is a deliberate
choice specific to this being a simulated environment: in a real
deployment inventory should sync from an actual ERP or warehouse system,
which would make hand-editing it here a second, unsynchronized source of
truth. With no real system to sync from, `inventory.db` already *is* the
system of record, and the bundled seed data is just its state-0
baseline — restorable any time via the "Reset to Original" button now on
the Inventory tab itself (previously this reset only lived on the Batch
Invoices tab; both call the same `/api/inventory/reset` endpoint, now
via one shared `resetInventory()` in `app.js` instead of two copies of
the same fetch-and-toast).

`item` — the one column that stays plain text, not an input — is
deliberately excluded from what `/api/inventory/update` accepts. It is
the primary key `item_aliases` references by foreign key and the exact
string the validation agent's normalization (`src/ingestion/normalize.py`)
matches against; renaming it here would silently break every alias
pointing at it. The endpoint only updates existing rows by that key —
matched — it doesn't add or rename items.

**Two small pre-existing issues fixed while wiring this up:**
1. `input[type="number"]` had never been styled — every number field
   anywhere in the app (this new inventory table, and the invoice edit
   form's quantity/price/total fields built earlier) rendered with the
   browser's plain white default instead of the app's dark theme. One
   line fixes both: added to the same selector list as the existing
   `input[type="text"]` rule.
2. Nothing distinguished "I haven't changed anything" from "I have
   unsaved changes" — Save Changes is disabled on load and on every
   successful save, and only enables on the first `input` event a cell
   actually fires, so it can't be clicked with nothing to save.

**Copy pass**: earlier drafts of the UI carried demo-flavored language —
"Mock inventory database," "Reset Demo Data," "Sample invoice," a tab
labeled "Batch / All Samples." All renamed (to "Inventory database,"
"Reset Inventory and Ledger," "From invoice folder," "Batch Invoices")
along with the internal identifiers that mirrored them
(`selectedSample`/`sample-select`/`loadSampleOptions`/the `sample` API
param all became `selectedInvoiceName`/`invoice-select`/`loadInvoiceOptions`/
`invoice_name`), so the code and the UI describe the same concept the same
way. The one legitimate survivor is `mock_payment()` — that's the
assignment's own literal mock banking API function name, not offline-mode
leftovers, and stays as specified. A later pass rewrote every tab and the
Settings panel's copy into ASD-STE100 Simplified Technical English (short,
active-voice sentences, one instruction per sentence, no em dashes, no
contractions) — this reaches all label/help/error/toast text in
`static/index.html` and `static/app.js`, plus the backend-origin strings
that render inside those tabs (validation issue messages, payment detail
text, `LLMNotConfiguredError`'s message). It intentionally does not touch
enum-style status labels (`approved`/`rejected`/`paid`) or this document.

**API key handling**: a gear icon opens a settings panel — pick a provider,
choose a model, paste a key, hit Save. The key is sent once to
`POST /api/settings`, which writes it to `.secrets/local_config.json`
(gitignored, chmod `600`, owner-read/write only) via `src/key_store.py`. It
is **never sent back to the browser** — on reload the field shows only a
fixed-width masked tail (`••••••••1234`, always 8 dots regardless of the
real key's length) pulled from `GET /api/settings`, never the real value.
An earlier version of `masked()` scaled the dot count 1:1 with the key's
actual length; a realistic 80-120 character key produced an equally long
masked string that overflowed the settings modal's fixed width. Fixed by
capping the mask to a constant length, with an `overflow-wrap` CSS rule on
`.key-status` as a backstop. This is a local-file store, not an OS
keychain: appropriate for a single-user local demo, not a multi-user
deployment (see Known limitations).

**Model selection**: each provider now has a second dropdown, populated
from `PROVIDER_MODELS` in `src/config.py` (a short curated list per
provider — a model not on the list is still reachable via the `LLM_MODEL`
environment variable, so the UI list isn't a hard ceiling). The chosen
model is stored per-provider in the same key store, alongside the key, and
`load_settings()` resolves it with the same precedence pattern as the key
itself: stored choice > `LLM_MODEL` env var > provider default. Saving a
blank model (or switching providers without picking one) never clobbers a
previously stored choice, mirroring how a blank key field doesn't clear a
stored key.

Making this work required one non-obvious change: `src/config.py` and
`src/llm_client.py` used to cache a `Settings`/`LLMClient` singleton at
import time, which is fine for the CLI (fresh process per invocation) but
would go stale in a long-running server process — saving a key through the
UI wouldn't take effect until restart. Both now construct fresh on every
call (`load_settings()`, `LLMClient()`); it's cheap (a couple of dict/env
lookups), so there's no real cost to always recomputing.

## Running it

**There is no offline/mock mode — this was a deliberate decision, not a gap.**
An earlier version of this system shipped a heuristic offline stand-in so
the whole pipeline was demoable with zero setup. It was removed: real
reasoning or a clear error, nothing in between. Concretely, that means:

- JSON/CSV/XML invoices still parse and validate with **zero configuration**
  — that path never touches an LLM.
- Free-text/PDF ingestion and **every** approval decision require a
  configured provider. Without one, `LLMClient()` raises
  `LLMNotConfiguredError` immediately with an actionable message, caught and
  surfaced cleanly by both the CLI's per-invoice error panels and the web
  UI's error toasts — not a silent fallback, not a crash with a stack trace.

**Fastest path — one command sets up the venv, installs dependencies, creates
`inventory.db` if it's missing, and starts the web UI:**

```bash
./run.sh                # web UI at http://localhost:8000
./run.sh --reset        # same, but also wipes and reseeds inventory.db first
```

The equivalent manual steps, or for running the CLI/eval instead of the web UI:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup_inventory_db.py --force        # creates inventory.db
python data/generate_pdfs.py                # optional, regenerates the 3 sample PDFs
cp .env.example .env                        # then set LLM_PROVIDER + the matching *_API_KEY
                                             # (or configure both through the web UI's Settings panel instead)

# CLI
python main.py --invoice_path=data/invoices/invoice_1001.txt
python main.py --all                        # batch mode + summary table

# Web UI — http://localhost:8000
python server.py

python -m eval.run_extraction_eval           # writes eval_report.md / .json — requires a configured key
pytest                                       # 79 tests, no API key required — LLM calls are monkeypatched
```

Grok (`LLM_PROVIDER=xai`) is the assignment's named engine; OpenAI and
Anthropic are wired up as drop-in alternatives (`LLM_PROVIDER=openai` /
`anthropic`) per the assignment's "other models are acceptable" allowance.
All three speak through the same `LLMClient` — swapping providers is a
one-line config change, not a code change.

`python main.py --all` writes to the `processed_invoices` ledger in
`inventory.db`. Running it a second time without resetting the DB will show
every previously-paid invoice as `rejected` with `duplicate_invoice` — that's
the dedup protection working as designed (see below), not a bug. Run
`python setup_inventory_db.py --force` (CLI) or the "Reset Inventory and
Ledger" button (web UI) between runs to reset it.

## Above/beyond

- Format-routed ingestion + the eval harness proving it, rather than
  asserting it (this was the specific ask that shaped this build).
- Item-name normalization (`src/ingestion/normalize.py`) against an
  alias table + fuzzy fallback, so "Widget A", "WidgetA (rush order)", and
  "Gadget-X" all resolve to the same inventory key before validation ever
  sees them.
- Duplicate-payment ledger, aggregated stock checks, and arithmetic
  consistency checks — none of which are in the README's four examples, all
  of which the sample data specifically exercises.
- A two-pass approval loop with a real critique step, not a single LLM call
  relabeled as "reflection."
- A provider-agnostic LLM client (`src/llm_client.py`) — Grok, OpenAI, or
  Anthropic behind one call surface, swappable via config with no code
  changes. No offline stand-in: every real invocation goes to a live model.
- Structured JSON run logs per invoice (`logs/`) plus a rich-formatted CLI
  with per-stage panels and a batch summary table shaped like the README's
  own scenario table.
- A FastAPI + vanilla-JS web UI on top of the same pipeline, with an
  in-browser settings panel for entering/rotating a provider API key that's
  written to a local gitignored, permission-locked file and never
  re-displayed in plaintext, plus a folder picker that can promote an
  arbitrary directory of invoices to be the app's main invoice folder —
  a single source of truth the Single Invoice dropdown, Batch tab, and
  CLI's `--invoices-dir` default all read from.
- Tests never hit a real API: `LLMClient.complete_structured` is
  monkeypatched per-test with canned responses (see `tests/conftest.py`'s
  `patch_llm` fixture) rather than relying on an offline mode baked into
  production code — the two concerns (testability, and what the shipped
  system actually does with no key) are kept separate.
- Batch runs are a real background job, not a blocking request: a live
  status bar shows which invoice is running and how many of the total are
  done, and a Stop button halts the run between invoices without losing
  whatever already completed — see Front-end, above.
- Per-provider model selection, not just provider selection — a curated
  short list per provider in `src/config.py`, with `LLM_MODEL` as an escape
  hatch for anything not on the list.
- All tab and Settings-panel copy, including backend-origin messages that
  render inside them, follows ASD-STE100 Simplified Technical English —
  short, active-voice, single-instruction sentences with controlled,
  consistent terminology, the same standard aerospace/technical
  documentation uses.
- A Processed Invoices tab giving a persistent audit view across every
  Single Invoice and Batch Invoices run, reusing the run-log JSON files
  already written for observability rather than adding a second data
  store — see Front-end, above. Table values explain themselves on hover
  (column definitions, and the specific reason behind any red/amber
  result), and every row opens a real breakdown of the invoice's math in
  its own pop-up window, linked by the same id from both tabs. The tab
  also refreshes itself live as invoices complete elsewhere in the app.
- Live per-stage progress on both Single Invoice and Batch Invoices
  (Reading → Checking → Getting an approval decision → Processing
  payment), and a Batch Invoices table that builds up row by row as each
  invoice finishes instead of only rendering once the whole batch is
  done — see "Live per-stage progress," above.
- A manual-edit workflow with a genuinely honest mock approval step: a
  proposed field edit doesn't take effect until "approved," the
  self-approval is called out explicitly in the notification and the UI
  (not glossed over), and approving one reruns validation → approval →
  payment against the edited value — skipping ingestion, since nothing
  about the raw file changed — producing a new, separately audited run
  linked back to the original. See "Manual edits and approvals," above.
- An in-page notification system (bell, unread badge, dropdown panel,
  bottom-right auto-dismissing toast, a short synthesized beep with no
  audio asset to ship) with a configurable batch mode — notify once per
  batch, or once per invoice inside it — and two guards a naive polling
  implementation would miss: a suspended `AudioContext` before the first
  user gesture, and not replaying the whole notification history as a
  beep storm on page load. See "Notifications," above.
- A human-in-the-loop resolution path for escalated invoices — approve
  or deny directly from the Edit Approvals tab, which actually pays or
  withholds payment for real, preserves the model's original escalation
  reasoning as a separate historical field rather than overwriting it,
  and states the same self-approval caveat everywhere the demo asks a
  person to stand in for a real approver. See "Escalation resolution,"
  above.
- A genuinely destructive Full Reset, gated behind type-to-confirm
  rather than a single click, that clears every store the quick
  per-batch reset intentionally leaves alone (run logs, edit requests,
  notifications) — with a running-job guard so it can't delete out from
  under an in-flight write. See "Full reset," above.
- Freely editable inventory with a one-click return to the original
  seed data — a deliberate call for this being a simulated environment
  with no real ERP behind it, and the direct cause of a genuine,
  previously-invisible CSS gap (unstyled `input[type="number"]`) getting
  fixed. See "Editable inventory," above.
- A third live-progress job (edit-request approval, alongside Single
  Invoice and Batch), whose first stage exists purely to make an
  otherwise-invisible design decision visible: approving an edit reruns
  the invoice through validation → approval → payment for real, and the
  UI says so — "Passing the edited invoice back through the system" —
  the instant the button is clicked, not after the fact. A mini status
  indicator by the notification bell then makes any of the three job
  types' progress visible from every tab, not just the one that started
  it. See "Mini status indicator," above.

**Verification note on the UI**: every API endpoint (`/api/settings`,
`/api/invoices`, `/api/process/start`, `/api/process/status/{id}`,
`/api/batch/start`, `/api/batch/status/{id}`,
`/api/batch/stop/{id}`, `/api/processed-invoices`,
`/api/processed-invoices/{run_id}`, `/api/inventory`, `/api/inventory/reset`)
was exercised directly with `curl`, and the full
page was then driven in an actual Chrome browser — sample selection,
drag-and-drop-style file upload (both JSON and PDF), the settings panel's
provider switch / save key / masked-redisplay / clear key flow, the batch
run and its business-impact stat cards, the inventory tab, and the
duplicate-payment rejection path — all confirmed working end-to-end with
screenshots at each step, console/network checked clean throughout. Two
real bugs surfaced during that pass and were fixed, not just noted:

1. `[hidden]` elements weren't actually hiding — `.field { display: flex }`
   in `app.css` was silently beating the browser's default
   `[hidden] { display: none }` because author styles always win over the
   UA stylesheet regardless of selector specificity. Fixed with an explicit
   `[hidden] { display: none !important; }` rule. (Surfaced when the settings
   panel still had a conditionally-hidden field, before mock removal
   simplified it to always-visible — the CSS fix stayed, since `hidden` is
   still used elsewhere, e.g. the toast and unconfigured-provider notice.)
2. Uploaded files displayed their server-side temp filename
   (`tmph1dzjxkc.json`) instead of the name the user actually uploaded.
   Fixed by threading the original `UploadFile.filename` through to the
   result payload instead of deriving it from the temp path — and reused
   for the same reason in the later multi-file folder-upload endpoint.

The folder-picker UI (added after mock removal) was verified the same way:
driven in a real browser via a native file-input selection, confirming
client-side extension filtering (a `.py` file dropped into the selection
was correctly excluded) and that the batch summary/table correctly render
per-file failures when no key is configured, rather than crashing.

**Verification note on the live-progress/edit/notification feature set**:
this was verified against a second, fully isolated server instance
(different port, `INVENTORY_DB_PATH`/`LOG_DIR`/`LOCAL_KEY_STORE_PATH`/
`EDIT_REQUESTS_STORE_PATH` all pointed outside the repo) so that testing
never touched the real port-8000 server, `inventory.db`, or `logs/` —
`run.sh`'s normal setup is untouched by any of this. Exercised end-to-end
with a real configured key and real model calls (not monkeypatched): a
`POST /api/process/start` job through all four stages with live stage
polling, a two-file `POST /api/batch/start-upload` batch with live
per-file stage and incrementally-growing `results`, an edit request
(diffed correctly against the coerced dump, not raw form values),
approving it (confirmed the arithmetic-mismatch *and* duplicate-invoice
rejection described in "Manual edits and approvals," above, and the
`edited_from` link showing up in `/api/processed-invoices`), a rejected
double-approve attempt correctly returning `409`, and both
`invoice_completed`/`edit_pending` notifications appearing through
`GET /api/notifications` with the exact caveat wording. `pytest`
(54 tests) and `pyflakes` were both clean throughout. A follow-up
in-browser pass against the same isolated instance then drove the actual
UI: the Single Invoice stage bar visibly stepped through Reading →
Getting an approval decision; the Processed Invoices tab picked up a
just-completed invoice with no manual refresh; the bell badge, dropdown
panel, and bottom-right toast all fired on real notifications (mark-
all-read on open, click-outside-to-close); the edit form rendered with
the caveat banner and pre-filled fields, submitted, and showed up
correctly in both Edit Approvals (with working Approve/Reject, buttons
disabling mid-request) and, after approving, Edit Requests' history with
a working "View result" link; and a live two-file batch visibly built
its table row-by-row while the second file was still on "Getting an
approval decision," firing one toast per invoice per the `per_invoice`
setting. (One tooling note, not an app issue: the breakdown pop-up's
`window.open(...)` — the exact "new window" behavior originally asked
for — opens a real OS-level window that the browser-automation
extension doesn't track as a tab; verified instead by navigating a
tracked tab straight to the same URL, which renders identically.)

That pass is also what surfaced the contradictory-critique behavior
described above. The guardrail and the `invoice_escalated` notification
that followed were verified with `pytest` (57 tests, three new: the
guardrail forcing `escalated`, the non-contradictory path staying
untouched, and `_format_critique`'s new text) and `pyflakes`, both clean —
not yet re-driven through a live browser session, since it's a small,
self-contained addition to already-verified paths (the escalation
notification reuses the same bell/toast/panel code exercised above; only
the trigger condition and message text are new).

Escalation resolution (approve/deny in the Edit Approvals tab) was
re-driven through a live browser session against the same isolated
instance, with real escalated invoices (a EUR invoice naturally
escalates on the currency-warning policy rule — no need to reproduce the
draft/critique contradiction to test this path, since escalation
resolution only cares that `decision == "escalated"`, not how it got
there). Confirmed: the escalations section rendering above the edit
requests section with the broadened caveat banner; Approve correctly
paying (ledger and payment status both flip to `paid`) and Deny
correctly withholding payment for real (`rejected`, not `skipped`);
both buttons disabling immediately on click; a 409 on trying to resolve
an already-resolved invoice; the routine and `invoice_escalated`
notifications both firing with the correct file name; and — this is
where the display-name bug above was actually caught — the escalation
notification initially showing a temp filename, fixed, then re-verified
showing the real uploaded name end to end (immediate response, a later
Processed Invoices lookup, and the resolution notification all agreeing).
58 tests and `pyflakes` clean throughout.

Full Reset was verified the same way: the 409 guard while a batch was
still running; a real reset via `curl` confirming `logs/`, edit
requests, and notifications all reached zero and the ledger/inventory
reset; and a full pass through the browser confirming the type-to-confirm
gating (disabled on a lowercase or partial match, enabled only on an
exact `RESET`), the modal-overflow bug and its fix (see "Full reset,"
above — this is exactly where that bug was caught, on the first attempt
to reach the confirm button on a shorter browser window), and every tab
(Processed Invoices, Edit Requests, Edit Approvals) reading empty
immediately after, with no manual reload. 61 tests and `pyflakes` clean.

The edit-recheck job and mini status indicator were verified together,
live: `curl` confirmed the job returns `stage: "recheck"` immediately on
approve, then progresses through `validation`/`approval`/`payment` on
successive polls; a genuine race — a second approve fired 0.3s after the
first, while it was still mid-recheck — correctly got `409` ("This edit
request is already being rechecked") rather than starting a duplicate
job. In the browser: started a Single Invoice job and watched the mini
indicator show "Reading the invoice," switched to the Inventory tab
mid-run and confirmed it kept updating there (the entire point of it
living outside any one tab's panel); watched it clear the instant the
job finished; then approved a pending edit request and watched "Passing
the edited invoice back through the system" appear in the indicator the
same render frame the click handler ran, before progressing through the
remaining stages and clearing again on completion.

A follow-up round of five smaller fixes was verified together, live: the
mini status indicator reading "Idle" with a static gray dot from the
moment the page loads, before anything has ever run; starting a Single
Invoice job and watching it switch to "Reading the invoice: invoice_1002.txt"
— confirming both the server-side `file` field addition and the
`"stage: file"` rendering; the Processed Invoices table showing a real
`Processed At` timestamp per row after that job completed; clicking an
`invoice_completed` notification in the panel and landing on Processed
Invoices with the panel closed; and a batch summary's six stat cards
rendering in a single row (confirmed via `renderBatchSummary()` called
directly against a real completed job's summary), with "Approved for
Payment" wrapping to two lines rather than the ellipsis-truncated version
tried first. 61 tests and `pyflakes` clean throughout — none of the five
touched code the existing test suite exercises directly, so no new tests
were added; all five were verified at the UI layer where they live.

The batch-concurrency restructure (see "Batch concurrency and duplicate
detection," above) got the highest verification bar of anything in this
project, since a bug there means an actual double payment rather than a
cosmetic issue. Beyond the four new unit tests (`seen_in_batch`'s
first-occurrence-passes/second-occurrence-flagged behavior, the ledger
check's priority over the batch check, and byte-identical behavior with no
`seen_in_batch` argument — 65 tests and `pyflakes` clean), it was verified
against the real scenario end to end on an isolated instance with a real
LLM: `invoice_1004.json` and `invoice_1004_revised.json` — genuinely
same invoice number, different totals — run as one batch against a fresh
ledger paid exactly one of the two and rejected the other with the new
"appears more than once in this batch" message; re-running the identical
batch without resetting rejected both, this time via the pre-existing
"already paid" ledger message; a grep of the mock payment API's log
confirmed it fired exactly once across both runs. A mid-batch Stop request
on an 8-file batch landed the job in `stopped` status having completed 4
of 8 — confirming the fix to a bug caught in design review, where the
original draft checked `should_stop()` once before dispatching all of a
phase's work to the thread pool instead of at the top of each worker,
which would have made Stop a no-op under the new concurrent phases.

Two small UI-polish rounds followed, verified live in the browser rather
than with new tests since neither touched code the existing suite
exercises: the Amount/Validation/Decision/Payment columns in the shared
invoice table (`renderInvoiceTable()`, used by both Batch Invoices and
Processed Invoices) center-aligned instead of a left/right/left mix, and
the invoice breakdown pop-up's Decision/Payment badges — previously
floating at top-right with no label — given small uppercase headers
(`.badge-field`, reusing the same tooltip text as the table's column
headers, now shared from `static/common.js` so the two pages can't drift
out of sync). Then a genuine bug: the Processed Invoices tab visibly
flickered while a batch ran, traced to `handleJobUpdate` refetching and
re-rendering that tab's table on every 900ms poll regardless of whether
anything had actually changed, each time briefly blanking it to a
"Loading…" placeholder first. Fixed by only refreshing when a batch job's
result count actually grows, and by making a background refresh swap the
table in place instead of blanking it first — verified on an isolated
instance by running a real 20-invoice batch with the tab open throughout:
zero `/api/processed-invoices` calls during stretches with no new
results, and a network log confirming the table only refetched when a
result had genuinely landed.

The three features in this round build on that same live-batch
infrastructure. The batch status bar's dropdown (see "Batch status
dropdown," above) needed the server to track per-file stage, not just a
single "most recent" stage/file pair, so `job["in_progress"]` was added
alongside the existing fields and `on_stage` gained an optional file-name
argument at exactly the three call sites in `_process_batch` that already
call it per-invoice. The mini status indicator's click-to-navigate reused
the existing three-source (`edit`/`single`/`batch`) state it already
tracked, just mapping each to the tab that would have started it. The
action log (see "Action log," above) is the only genuinely new subsystem
of the three — verified with new unit tests (`tests/test_action_log.py`,
plus one integration test each in `test_approval.py` and
`test_ingestion.py` confirming the wiring actually fires, not just that
the module works standalone — 77 tests and `pyflakes` clean) — and live on
the isolated instance: ran a real batch with a mix of structured and
free-text invoices, confirmed the Action Log tab showed one entry for
each free-text ingestion plus a draft/critique pair for each approval
(and correctly zero entries for JSON/CSV/XML files, which never call an
LLM at all), with the right invoice number or file name as context on
every row.

## Known limitations / next steps

- **The contradictory-critique guardrail only catches one specific shape
  of inconsistency.** `ApprovalAgent.run()` now escalates when
  `critique.agrees` is `true` but `critique.final_decision` disagrees
  with the draft (see "Approval — the self-correction loop," above) —
  the exact shape observed live during testing. It does not (and, with
  structured output enforcing the schema's types, largely cannot) catch
  every other way a model could be unreliable — a confidently wrong but
  internally *consistent* answer would sail through untouched, since
  self-consistency is all this guardrail checks for. It is a narrow
  backstop for one observed failure mode, not a general correctness
  guarantee on the model's reasoning.
- **Full Reset clears processing history, not configuration.** The
  provider/API key, the batch notification preference, and the main
  invoice folder selection (including anything copied into
  `uploaded_invoices/`) all survive a full reset on purpose — those are
  settings, not history, and re-entering an API key after every reset
  would defeat the point of storing one at all. If a true factory reset
  (wiping configuration too) is ever needed, that's `rm -rf .secrets
  uploaded_invoices inventory.db logs edit_requests.json` by hand, same
  as before this feature existed.
- **OCR**: the sample PDFs are vector-text, so `pdfplumber` extraction is
  sufficient; a scanned-image invoice would need an OCR pass (e.g.
  `pytesseract`) ahead of the same free-text extraction path. Deliberately
  left out — it's a system dependency that would break "clone and run" for
  a grader without Tesseract installed, and there's no scanned-image sample
  to justify it here.
- **Stock is read-only per run**: validation checks against the current
  inventory snapshot but doesn't decrement it as invoices are approved
  within a `--all` batch, so two approved invoices in the same run against
  a tight stock level wouldn't compound. A stateful mode (process in
  sequence, decrement on approval) would be a natural extension for a true
  batch-processing simulation.
- **No currency conversion**: non-USD invoices are flagged for manual
  review rather than auto-converted and compared against the USD scrutiny
  threshold.
- **Key storage is a local file, not a keychain, and the server has no
  auth.** Fine for `python server.py` on your own machine; if this were
  ever deployed for multiple users, the key store should move to an OS
  keychain or a real secrets manager, and the API would need
  authentication before it's reachable over a network.
- **Batch jobs live in memory, one at a time.** The job store
  (`server.py`'s `_jobs` dict) is not persisted, so a server restart loses
  the record of any job (finished or not), and only one batch can run at a
  time — a second start attempt while one is running gets a 409. Correct
  for a single local user; a real multi-tenant deployment would need a
  durable job queue (e.g. Redis or a database-backed table) instead.
- **Processed Invoices history has no pagination or retention limit.** It
  lists every file in `logs/`, so it grows without bound and gets slower
  to load as that directory grows — fine for a demo's worth of runs, not
  for a system running unattended for weeks. A real deployment would want
  a proper indexed store (or at least a cap-and-page API) instead of
  reading every JSON file on every request.
- **Notifications are in-memory, like the batch job store.** A server
  restart loses the notification history along with any read/unread
  state — consistent with the `_jobs` tradeoff above, not a new gap.
- **Edit requests are a gitignored JSON file (`edit_requests.json`), not a
  database.** Fine for the append-mostly, low-volume, single-user pattern
  this feature actually has; a real multi-approver deployment would want
  a real table with row-level locking instead of read-modify-write over
  a whole file.
- **Editing an invoice's number, specifically, can create a double
  payment under two different invoice numbers.** The duplicate-payment
  ledger is keyed by invoice number, so if an *already-paid* invoice is
  edited and the edit changes the invoice number itself, the recheck runs
  the duplicate check against the *new* number — finds no conflict — and
  can approve and pay it again, while the original payment under the old
  number still stands in the ledger. Editing any other field of an
  already-paid invoice is safe (the duplicate check still catches it,
  correctly rejecting the recheck — see "Manual edits and approvals,"
  above); this is specifically about the invoice-number field. Acceptable
  for a demo; a real deployment would want the duplicate check to also
  consider "was this run's original `edited_from` ancestor already paid,"
  not just the invoice number in isolation.
