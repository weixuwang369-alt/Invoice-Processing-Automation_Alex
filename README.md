# Invoice Processing Automation

This is a working multi-agent system that automates invoice processing end
to end. It reads an invoice (single or batch), checks it against inventory, gets an approval
decision from an AI reviewer, and pays or rejects it. I would appreciate reading the [Additional Features](#additional-features-above-and-beyond) section, which I believe showcases my effort and thoughtful decisions behind making a delightfully useful system.

The system runs two ways. It runs as a command-line tool. It also runs as
a full web application. The command-line tool meets the assignment's
specification, whereas the web application goes beyond it for a delightful experience. This README
explains both.

## Table of Contents

- [Overview](#overview)
- [How to Use](#how-to-use)
- [How It Works](#how-it-works)
- [Additional Features (Above and Beyond)](#additional-features-above-and-beyond)

## Overview

Acme Corp is a manufacturing firm. It loses two million dollars a year on
manual invoice processing. Staff read each invoice by hand. They check it
against a spreadsheet. They email a VP for approval. They pay through a
banking portal. Errors happen often. Payments arrive late.

This system replaces that manual chain with four automated stages:

1. **Ingestion** reads the raw invoice file and extracts its data.
2. **Validation** checks that data against the inventory database.
3. **Approval** reviews the invoice and reaches a decision.
4. **Payment** pays an approved invoice or logs the reason for a rejection.

Each stage is a separate agent. A stage passes its output to the next
stage. [LangGraph](#technical-requirements-met) wires the four stages
together into one pipeline. This mirrors how a real approvals team works:
one person checks the data, one person checks stock, one person signs off,
and one person pays.

Two things make this submission more than the minimum. First, the
Approval stage does not just make one decision and stop. It drafts a
decision, then critiques its own draft, the same way a careful reviewer
double-checks their own judgment before signing off. See
[How It Works](#how-it-works) for the full four-stage pipeline. Second,
the system does not stop at the command line. It also ships as a complete
web application with live progress, batch processing, manual edit and
approval workflows, notifications, and a full audit log of every AI
decision. See
[Additional Features](#additional-features-above-and-beyond) for the full
list.

## How to Use

### Setup

The system needs a recent version of Python 3. It was built and tested on
Python 3.14. It needs an API key for an LLM provider. Grok, reached
through [xAI](#technical-requirements-met), is the assignment's named
provider. OpenAI and Anthropic also work, since the assignment allows
other models.

The fastest path is one script. It creates a virtual environment,
installs dependencies, sets up the inventory database, and starts the web
application:

```bash
./run.sh                # starts the web application at http://localhost:8000
./run.sh --reset        # same, but also resets the inventory and payment ledger first
```

The manual steps, useful for the command-line tool or for running tests:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python setup_inventory_db.py --force        # creates inventory.db
cp .env.example .env                        # then set LLM_PROVIDER and the matching *_API_KEY
pytest                                      # runs the test suite, no API key needed
```

That last command runs the project's
[test suite](#test-suite). No key or network call is needed for it.

A key is not required for every use. JSON, CSV, and XML invoices parse
and validate with **zero configuration**, since that path never calls an
LLM. Free-text and PDF invoices need a key for extraction. Every approval
decision needs a key, since approval always reasons with the LLM. If no
key is set, the system raises a clear error at the exact point it needs
one. It does not fail silently, and it does not fall back to a fake
answer. See [No Offline Mode](#no-offline-mode) for why.

### Running the Command-Line Tool

This is the exact form the assignment specifies:

```bash
python main.py --invoice_path=data/invoices/invoice_1001.txt
```

The tool prints a structured result panel to the console. It shows the
extracted fields, the validation outcome, the approval reasoning, and the
payment result. It also writes a structured JSON log for the run. See
[Observability and Run Logs](#observability-and-run-logs) for the log
format.

The tool also processes an entire folder in one command:

```bash
python main.py --all
```

This prints one panel per invoice, then a summary table across the whole
run. See [Concurrent Batch Processing](#concurrent-batch-processing) for
the web application's much faster version of this same idea.

### Running the Web Application

```bash
python server.py
```

Then open `http://localhost:8000`. The web application is
[the largest part of this submission's "above and beyond" work](#the-web-application).
It has seven tabs.

- **Single Invoice** processes one file at a time, picked from the sample
  folder or uploaded fresh. A live status bar shows which stage is
  running. See [Live Status Indicators](#live-status-indicators).
- **Batch Invoices** processes a whole folder at once, concurrently, with
  a live table that fills in as each invoice finishes. See
  [Concurrent Batch Processing](#concurrent-batch-processing) and
  [Immediate, Safe Batch Stop](#immediate-safe-batch-stop).
- **Processed Invoices** lists every invoice the system has ever
  processed, from either tab above. Click a row for a full breakdown.
  Click Edit to propose a change. See
  [Manual Edit and Re-Approval Workflow](#manual-edit-and-re-approval-workflow).
- **Edit Requests** lists every edit a user has proposed, and its status.
- **Edit Approvals** lists escalated invoices and pending edit requests
  that need a decision. See
  [Escalation Resolution](#escalation-resolution).
- **Inventory** shows the stock database the Validation stage checks
  against. Stock, price, and category are editable. See
  [Editable Inventory](#editable-inventory).
- **Action Log** lists every call the system has made to the LLM
  provider, with the prompt and the result. See
  [Action Log](#action-log).

A bell icon shows live [notifications](#notifications). A Settings panel
holds the LLM provider choice, the API key, and a
[Full Reset](#full-reset) option.

## How It Works

This section maps directly to the assignment's four-stage workflow and
its technical requirements.

### Stage 1: Ingestion

The Ingestion agent reads one invoice file and produces a structured
record: vendor, amount, line items with quantities, and a due date.

The agent picks its method by file format, not by a single fixed method
for every file:

- **JSON, CSV, and XML** invoices carry their own structure. The agent
  parses them directly, with plain code. No LLM call happens. The result
  is exact every time.
- **Free-text and PDF** invoices do not carry structure. Vendors write
  them differently. Some contain typos. Some use inconsistent labels,
  such as "Vendor," "Vndr," or "From." The agent sends this raw text to
  the LLM and asks it to extract the same structured record. This is the
  one ingestion path where an LLM earns its cost, since the input truly
  needs to be read the way a person would read it.

This routing decision is not a guess. It is measured directly. See
[Extraction Accuracy Evaluation](#extraction-accuracy-evaluation).

After extraction, the agent normalizes each line item's name against the
inventory database. "Widget A," "WidgetA," and "Widget A (rush order)"
all resolve to the same inventory key, `WidgetA`. Without this step, a
correct invoice could fail validation over a labeling difference, not a
real stock problem.

### Stage 2: Validation

The Validation agent checks the extracted invoice against the inventory
database. This stage runs no LLM call. It is deterministic code, so a
given input always produces the same result. That makes it fully
testable without mocking anything.

It runs these checks, in order:

| Check | What it catches |
|---|---|
| Unknown item | A line item is not in the inventory database at all. |
| Invalid quantity | A line item's quantity is zero, negative, or missing. |
| Stock level | The requested quantity, summed across every line for that item, exceeds available stock. |
| Arithmetic | Subtotal plus tax plus shipping does not equal the stated total. |
| Due date | The due date cannot be parsed as a real date. |
| Currency | The invoice is not in USD, and needs manual foreign-exchange review. |
| Duplicate payment | This invoice number was already paid, or already appears earlier in the same batch. See [Duplicate Payment Protection](#duplicate-payment-protection). |

The stock check aggregates quantities per item before it compares against
stock. One sample invoice splits the same item across three separate
line items. A check that looked at each line alone would miss that the
combined request exceeds stock. This system does not miss it.

The sample data in `data/invoices/` was built to exercise every one of
these checks:

| Scenario | Invoice | Result |
|---|---|---|
| Normal order within stock | INV-1001, INV-1004, INV-1006 | Passes validation. |
| Quantity exceeds stock | INV-1002 (20 GadgetX requested, 5 in stock) | Flagged, insufficient stock. |
| Zero-stock, suspicious item | INV-1003 (FakeItem, 0 in stock, urgent wire-transfer language) | Flagged, out of stock and suspicious. |
| Item not in the database | INV-1008, INV-1016 | Flagged, unknown item. |
| Invalid data | INV-1009 (negative quantity) | Flagged, data integrity issue. |

### Stage 3: Approval

The Approval agent simulates a VP-level review. This is the stage the
assignment asks for a **reflection or critique loop**, and it is the
stage with the most agentic behavior in the system.

The agent makes two LLM calls, not one:

1. A **draft** call reasons about the invoice. It reaches one decision:
   approve, reject, or escalate to a human. It applies a fixed policy:
   - Any unresolved critical validation issue rejects the invoice.
   - Any warning-level issue escalates the invoice.
   - Any invoice at or above the scrutiny threshold (ten thousand
     dollars, matching the assignment's example) gets flagged for extra
     scrutiny.
2. A **critique** call receives the draft. It is explicitly told to look
   for mistakes in the draft, not to simply agree with it. If the
   critique finds a real problem, it can overturn the draft's decision.

This two-call design is the self-correction loop. It is not a single LLM
call labeled as if it reflected. It is a second, independent reasoning
pass that can catch and reverse a wrong first answer. One built-in safety
net goes further still: see
[Contradictory Critique Guardrail](#contradictory-critique-guardrail) for
what happens when the critique's own answer does not agree with itself.

The reasoning text shown to a user is not raw, unpredictable model prose.
See
[Structured, Reproducible Reasoning Text](#structured-reproducible-reasoning-text)
for how the system guarantees the same clear shape every time.

### Stage 4: Payment

The Payment agent acts on the approval decision.

- An **approved** invoice calls the mock payment function from the
  assignment brief, unchanged: `mock_payment(vendor, amount)`.
- A **rejected or escalated** invoice is not paid. The agent logs the
  decision and the reasoning behind it instead.

Either way, the agent writes a row to a payment ledger. That ledger is
what makes [Duplicate Payment Protection](#duplicate-payment-protection)
possible.

### Technical Requirements Met

| Assignment requirement | How this system meets it |
|---|---|
| LLM integration (Grok via xAI) | The default provider. OpenAI and Anthropic are supported too, since the assignment allows other models. See [Multi-Provider LLM Support](#multi-provider-llm-support). |
| Multi-agent orchestration | [LangGraph](https://github.com/langchain-ai/langgraph) wires the four stages together, with a real conditional edge after Approval that routes to Payment or to a rejection path. |
| Function calling / tool use and structured outputs | Every LLM call asks for a specific, schema-validated object back, using each provider's native tool-calling mechanism. A call cannot return free-form text where structured data is required. |
| Self-correction loop | The Approval stage's draft-then-critique design, described above, plus the [Contradictory Critique Guardrail](#contradictory-critique-guardrail). |
| No internet needed for the mocked parts | The inventory database and the payment function are fully local. Only the LLM call itself is a live network call, which the assignment brief expects. |
| Python, with the suggested libraries | `pdfplumber` for PDF text, `langgraph` for orchestration, plus `fastapi`, `pydantic`, and others for the web application. |

## Additional Features (Above and Beyond)

I wanted to showcase how I would take this system beyond a basic working program, while still maintaining a concretely carved-out aperture to satisfy the following goal and showcasing how I approach adding value to clients: has the system been made into something real, with additional
features, expanded assumptions, and added test coverage? This section
answers that question in full detail. Each entry below was linked to
from [How to Use](#how-to-use) or [How It Works](#how-it-works). This is
where each of those short mentions is explained completely. I am most proud of the front-end, using my experience with "delightful" UI/UX experience, as well as the batch processing capabilities using parallel processing workflows.

### The Web Application

The assignment asks for a command-line tool. This submission also ships
a complete web application, built with FastAPI and a small amount of
plain JavaScript, with no framework and no build step. It is a second
entry point into the exact same agents the command-line tool uses. It is
not a separate, parallel implementation.

The application has seven tabs, described in
[How to Use](#how-to-use): Single Invoice, Batch Invoices, Processed
Invoices, Edit Requests, Edit Approvals, Inventory, and Action Log. Every
feature described in the rest of this section lives inside this
application.

The interface follows Simplified Technical English rules, the same rules
this document follows: short sentences, plain words, and one idea at a
time. This applies both to the static interface text and to the
AI-generated reasoning a user reads for a decision. See
[Structured, Reproducible Reasoning Text](#structured-reproducible-reasoning-text).

### Live Status Indicators

A user should never wonder whether the system is still working. Three
layers of live status solve this.

First, each tab that runs a job (Single Invoice, Batch Invoices, an edit
recheck) shows its own status bar. The bar names the current stage in
plain words, such as "Getting an approval decision," and names the file
it is working on.

Second, a compact status pill sits next to the notification bell, visible
on every tab, not only the tab where a job was started. A user can switch
to the Inventory tab while a batch runs in the background, and still see
that it is running. Clicking this pill jumps back to the tab that started
the job. When nothing is running, it reads "Idle," with a plain gray dot,
rather than disappearing. Its absence never needs to be interpreted.

Third, while a batch is running, clicking its status bar opens a
dropdown. The dropdown lists every invoice currently in progress and the
exact stage each one is on. Since batch processing runs several invoices
at once, see [Concurrent Batch Processing](#concurrent-batch-processing),
a single line of status text cannot show everything happening at once.
This dropdown can. An invoice drops out of the list the instant it
finishes, so the list only ever shows genuine, current work.

### Concurrent Batch Processing

A naive batch implementation processes one invoice, waits for it to
finish, then starts the next one. This system does not. It processes a
batch in three phases, run largely at the same time instead of one file
after another.

1. **Ingest** every file in the batch at once, up to four files at a
   time. Extraction can itself be an LLM call, for free-text and PDF
   files, so this phase benefits from running concurrently, not only the
   approval phase.
2. **Validate** every ingested invoice, one at a time, in the batch's
   original file order. This phase runs no LLM calls, so it finishes
   almost instantly regardless of batch size. Running it in strict order
   is what makes
   [Duplicate Payment Protection](#duplicate-payment-protection) correct
   across the whole batch, not only within it.
3. **Approve and pay** every validated invoice, again up to four at a
   time. This is the most expensive phase, since approval makes two LLM
   calls per invoice. Running it concurrently is safe, because the
   validation phase before it has already resolved every duplicate.

This design keeps the safety guarantee of a strictly sequential batch,
while running close to four times faster on a batch of mixed file
formats. The concurrency limit is a constant in the code, so it is easy
to raise or lower, to even all at once.

### Duplicate Payment Protection

Paying the same invoice twice is the one mistake this system must never
make. Two independent checks guard against it, described in
[Validation](#stage-2-validation).

The first check looks at the payment ledger. Every invoice that reaches
the Payment stage, approved or rejected, writes one row to a ledger table
keyed by invoice number. If a new invoice arrives with a number already
marked paid in that ledger, Validation flags it immediately, and it never
reaches Payment a second time.

The second check exists because of
[Concurrent Batch Processing](#concurrent-batch-processing). Two
invoices sharing the same invoice number, submitted in the same batch,
would not yet see each other in the ledger, since neither has finished
processing yet. The sample data includes exactly this case: two files
represent the same real invoice, once as the original and once as a
revision. The batch's Validation phase runs in strict, single-file-at-a-
time order specifically to catch this. The first occurrence passes. Every
later occurrence of the same number, within that same batch, is flagged
as a duplicate before it can reach Approval or Payment.

This was verified directly against a real duplicate-payment scenario, not
only with unit tests, since a bug here means an actual double payment.
The two same-numbered sample invoices were run together in one batch
against a fresh ledger. Exactly one was paid. The mock payment function
fired exactly once. Running the identical batch a second time, without
resetting the ledger, correctly rejected both invoices that time, since
the first was now already marked paid.

### Immediate, Safe Batch Stop

A user can stop a running batch at any time. Stopping takes effect almost
immediately, in a fraction of a second, rather than waiting for whatever
invoice happens to be mid-call at that moment.

Any invoice still in flight when Stop is clicked is discarded, not
finished. It is not logged. It is not counted. It is not shown in the
results table. This matters because Python cannot interrupt a network
call already in progress. An approval call that was mid-flight when Stop
was clicked still completes in the background, since it cannot be killed
outright. The system guarantees that call can never reach payment. A
check sits between the approval decision and the payment call, and it
runs every single time. If a stop was requested, that check stops the
invoice there, before any money moves and before any ledger row is
written.

This guarantee was verified directly, not only assumed. A real batch was
stopped mid-run, against a real LLM. The payment ledger was read
immediately after the stop, and read again fifteen seconds later, to give
any abandoned background work time to finish. Both reads matched exactly.
Nothing arrived late.

### Manual Edit and Re-Approval Workflow

A processed invoice is not a dead end. Any invoice, whatever its outcome,
can be corrected by hand from the Processed Invoices tab.

A user edits any field: vendor, amount, line items, due date, and more.
Submitting the edit creates an edit request. It does not change anything
immediately. Approving that request re-runs the full pipeline against the
corrected data. It starts from Validation, not from Ingestion again,
since there is nothing left to re-extract. This re-run happens as a
background job, with its own live status. The first stage of that status
is named "Passing the edited invoice back through the system." This
tells a user that an edit is not a shortcut. It goes through the same
checks a fresh invoice would.

### Escalation Resolution

The Approval stage can escalate an invoice instead of approving or
rejecting it outright, for example when a warning-level validation issue
exists. An escalated invoice needs a human decision before anything else
happens to it.

Escalated invoices appear in the Edit Approvals tab, next to pending edit
requests, since both need the same kind of action: a person approves or
denies them. In this demo, the same user who submitted the invoice
resolves its own escalations. A note in the interface says so directly.
In a real deployment, a separate approver would see and resolve these
instead. A resolved escalation's reasoning is stored separately from the
AI's own reasoning, so the record always shows plainly which words are a
person's and which are the model's.

### Notifications

A bell icon in the top bar shows a live feed of events. These events
include an invoice finishing, a batch finishing, an invoice needing
escalation, and an edit request needing a decision. A new notification
plays a short sound and shows a toast message. This way, a user working
on a different tab does not miss it. Clicking a notification jumps
straight to the relevant tab. A Settings option controls whether batch
processing sends one notification per invoice, or one summary
notification once the whole batch finishes.

### Action Log

Every single call this system makes to the LLM provider is recorded.
This includes reading a free-text or PDF invoice, drafting an approval
decision, and critiquing that draft. The Action Log tab lists every one
of these calls. Each entry shows its timestamp, the invoice or file it
was for, the model used, the prompt that was sent, and the result. If
the call failed instead, the entry shows the error.

This exists because an invoice processing system for a real company
should never be a black box. A reader should be able to see exactly what
the AI was asked, and exactly what it answered, for every decision the
system ever made. This log clears along with the rest of a
[Full Reset](#full-reset). It does not survive a server restart, the
same as this project's other short-lived operational data, such as
notifications. This is a reasonable choice for a local, single-user
demo. A real production deployment would give this log durable storage
instead.

### Editable Inventory

The Inventory tab shows the exact stock database the Validation stage
checks every invoice against. In a real deployment, this would sync from
an ERP system automatically. In this simulated environment, there is no
ERP to sync from, so the values are edited directly: stock level, unit
price, and category, for any item.

A Reset to Original button restores the inventory to its starting values
at any time, so experimentation never has to be careful or permanent.

### Full Reset

A Settings option clears every piece of processing history the system
has produced: every processed invoice, every edit request, every
notification, and every action log entry. It also resets the inventory
and clears the payment ledger. This cannot be undone, so it sits behind a
confirmation step: a user must type the word "RESET" before the button to
delete everything becomes active. This exists so a demo, or a fresh
evaluation run, can always start from a truly clean state.

### Multi-Provider LLM Support

Grok, reached through xAI, is the assignment's named engine, and the
default. OpenAI and Anthropic are also fully supported, since the
assignment explicitly allows other models. Every one of the three
providers speaks through the exact same internal interface. Switching
providers is a one-line configuration change, in the Settings panel or in
an environment variable. It is never a code change.

### No Offline Mode

This system does not include a fake, offline stand-in for the LLM. This
was a deliberate decision, not a missing feature. An earlier version did
include one, so the whole pipeline could be demoed with no setup at all.
It was removed on purpose. The system now gives one of two outcomes only:
a real reasoning call to a real provider, or a clear, immediate error
naming exactly what configuration is missing. It never silently returns a
made-up answer. For a system whose entire purpose is trustworthy
financial decisions, a convincing fake answer is a worse failure mode
than an honest error.

JSON, CSV, and XML invoices are not affected by this choice at all, since
that ingestion path never calls an LLM in the first place.

### Contradictory Critique Guardrail

The critique call in the Approval stage is described in
[Stage 3: Approval](#stage-3-approval). It returns two fields together:
does it agree with the draft, and what is its own final decision. During
testing, a real response came back from the model with `agrees: true`.
This meant it agreed with the draft. But its `final_decision` field did
not match the draft's decision at all. That answer contradicted itself.

The system now checks for exactly this shape, every time. A critique can
claim agreement, but name a different decision than the one it claims to
agree with. When that happens, the system does not guess which half of
the answer to trust. It escalates the invoice to a human instead. This
check is deliberately narrow. A critique can openly disagree, and
overturn the draft on purpose. That is the normal, healthy
self-correction path, and this guardrail leaves it alone. Only a
critique that disagrees with itself triggers this guardrail.

### Structured, Reproducible Reasoning Text

The reasoning text a user reads for a decision is not free-form text
written however the model chooses. Earlier in this project, it was, and
the results were readable for one invoice and inconsistent for the next.

The model now fills in a small number of short fields for each decision.
Each field is validated on its own. The fields are: the policy that
applied, the key facts the model weighed, and its conclusion.

Plain Python code, not the model, then assembles those fields into one
fixed, labeled shape. It is always a "Decision" line, followed by a
"Policy" line, a "Key facts" line, and a "Result" line. This shape is the
same every time, on every provider. Only the sentence inside each field
comes from the model. The structure around it is guaranteed by code.

Every LLM call also runs at the lowest possible randomness setting, on
every provider. This is the standard lever for making a given input
produce closely similar wording from one run to the next.

### Extraction Accuracy Evaluation

A separate evaluation script, `eval/run_extraction_eval.py`, measures the
accuracy of the Ingestion stage. It checks the free-text and PDF sample
invoices against a hand-checked ground truth. This evaluation is why
[Stage 1: Ingestion](#stage-1-ingestion) routes by file format.
Structured formats are parsed with plain code. Only unstructured formats
pay the cost of an LLM call. That is the one path where a plain parser
genuinely cannot do the job.

### Observability and Run Logs

Every invoice this system ever processes, from any entry point, writes
one structured JSON log file. The log holds the extracted invoice, the
validation result, the approval reasoning, and the payment outcome, in
one place. The Processed Invoices tab, and the command-line tool's own
summary table, are both built by reading these same log files. Nothing
about a run is hidden inside a variable that only existed for one
process's lifetime.

### Test Suite

The project ships with seventy-nine automated tests. They cover:

- Every validation rule.
- The approval self-correction loop, and its contradiction guardrail.
- Duplicate-payment protection, within one run and across a batch.
- The batch stop-and-purge safety guarantee.
- The action log.

None of these tests need a real API key or a real network call. Every
LLM call in the test suite is replaced with a controlled, predictable
stand-in instead. Because of this, the suite runs in well under a
second. It produces the same result every time. The codebase is also
checked with `pyflakes` on every change, to keep it clean of unused code
and unused imports.
