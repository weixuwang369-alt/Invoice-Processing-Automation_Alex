// Shared by static/app.js and static/invoice-detail.js. Loaded as a plain
// <script> tag on both pages (no build step, no module system) — keep this
// file to small, dependency-free helpers only.

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const DECISION_BADGE = { approved: "badge-green", escalated: "badge-amber", rejected: "badge-red" };
const PAYMENT_BADGE = { paid: "badge-green", skipped: "badge-amber", rejected: "badge-red" };
const SEVERITY_CLASS = { critical: "issue-critical", warning: "issue-warning", info: "issue-info" };
const EDIT_STATUS_BADGE = { pending: "badge-amber", approved: "badge-green", rejected: "badge-red" };

// Shared tooltip text for Validation/Decision/Payment headers — the invoice
// table (app.js) and the invoice breakdown pop-up (invoice-detail.js) both
// use these so the wording stays in sync.
const VALIDATION_HEADER_TITLE =
  "Validation: the application checks each invoice against inventory, arithmetic, and payment history.";
const DECISION_HEADER_TITLE =
  "Decision: the application approves, rejects, or escalates each invoice based on validation and policy.";
const PAYMENT_HEADER_TITLE =
  "Payment: the application pays approved invoices. It withholds payment for rejected or escalated invoices.";

// The application shows one of these while it works through an invoice, on
// the Single Invoice and Batch Invoices tabs, an approved edit's recheck,
// and the mini status indicator next to the notification bell.
const STAGE_LABEL = {
  recheck: "Passing the edited invoice back through the system",
  ingestion: "Reading the invoice",
  validation: "Checking the invoice",
  approval: "Getting an approval decision",
  payment: "Processing payment",
};

function money(n) {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
}

// Any value that came from an uploaded file or an edit form is untrusted —
// escape it before it goes into innerHTML. escapeHtml is for element text
// content; escapeAttr is for values placed inside a double-quoted HTML
// attribute (a narrower, and therefore not interchangeable, escape).
function escapeHtml(text) {
  if (text === null || text === undefined) return "";
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeAttr(text) {
  return String(text).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}
