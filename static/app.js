// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

let toastTimer = null;
function toast(message, isError = false) {
  const el = $("#toast");
  el.textContent = message;
  el.style.background = isError ? "var(--red)" : "var(--text)";
  el.style.color = isError ? "#fff" : "var(--bg)";
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 3200);
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

// Shared by the tab buttons themselves and by anything else that needs to
// jump to a tab programmatically (e.g. clicking a notification).
function switchToTab(tabName) {
  const tab = $(`.tab[data-tab="${tabName}"]`);
  if (!tab) return;
  $$(".tab").forEach((t) => t.classList.remove("active"));
  $$(".panel").forEach((p) => p.classList.remove("active"));
  tab.classList.add("active");
  $(`#tab-${tabName}`).classList.add("active");
  if (tabName === "inventory") loadInventory();
  if (tabName === "processed") loadProcessedInvoices();
  if (tabName === "edit-requests") loadEditRequests();
  if (tabName === "edit-approvals") loadEditApprovals();
  if (tabName === "action-log") loadActionLog();
  // The main invoice folder can change from the Batch tab, so refresh
  // whenever this tab becomes visible rather than only once at page load.
  if (tabName === "single") loadInvoiceOptions();
}

$$(".tab").forEach((tab) => {
  tab.addEventListener("click", () => switchToTab(tab.dataset.tab));
});

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

async function loadSettings() {
  const res = await fetch("/api/settings");
  const data = await res.json();

  const badge = $("#provider-badge");
  if (data.configured) {
    badge.textContent = `${data.active_provider} · ${data.active_model}`;
    badge.className = "badge badge-green";
  } else {
    badge.textContent = `${data.active_provider} · no key`;
    badge.className = "badge badge-amber";
  }

  $("#provider-select").value = data.active_provider;
  $("#batch-notify-select").value = data.batch_notify_mode || "on_complete";
  renderKeyField(data);
  renderModelField(data);
  return data;
}

function renderKeyField(data) {
  const provider = $("#provider-select").value;
  const info = data.providers[provider];

  $("#key-status").textContent = info && info.has_key ? `Stored key: ${info.masked}` : "No key stored";
  $("#api-key-input").value = "";
  $("#unconfigured-notice").hidden = Boolean(info && info.has_key);
}

function renderModelField(data) {
  const provider = $("#provider-select").value;
  const models = (data.available_models && data.available_models[provider]) || [];
  const info = data.providers[provider];
  const select = $("#model-select");

  select.innerHTML = models.map((m) => `<option value="${escapeAttr(m)}">${escapeHtml(m)}</option>`).join("");
  select.value = (info && info.model) || models[0] || "";
}

$("#open-settings").addEventListener("click", async () => {
  $("#settings-overlay").classList.add("open");
  const data = await loadSettings();
  $("#provider-select").dataset.cache = JSON.stringify(data);
});
$("#close-settings").addEventListener("click", () => {
  $("#settings-overlay").classList.remove("open");
  closeFullResetConfirm();
});
$("#settings-overlay").addEventListener("click", (e) => {
  if (e.target.id === "settings-overlay") {
    $("#settings-overlay").classList.remove("open");
    closeFullResetConfirm();
  }
});

$("#provider-select").addEventListener("change", () => {
  const cached = $("#provider-select").dataset.cache;
  if (cached) {
    const data = JSON.parse(cached);
    renderKeyField(data);
    renderModelField(data);
  }
});

$("#save-settings").addEventListener("click", async () => {
  const provider = $("#provider-select").value;
  const apiKey = $("#api-key-input").value.trim();
  const model = $("#model-select").value;
  const batchNotifyMode = $("#batch-notify-select").value;
  const res = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, api_key: apiKey || null, model: model || null, batch_notify_mode: batchNotifyMode }),
  });
  if (!res.ok) return toast("The application could not save the settings.", true);
  const data = await res.json();
  $("#provider-select").dataset.cache = JSON.stringify(data);
  renderKeyField(data);
  renderModelField(data);
  toast(`Saved. The application now uses ${provider}.`);
  loadSettings();
});

$("#clear-key").addEventListener("click", async () => {
  const provider = $("#provider-select").value;
  const res = await fetch(`/api/settings/${provider}/key`, { method: "DELETE" });
  const data = await res.json();
  $("#provider-select").dataset.cache = JSON.stringify(data);
  renderKeyField(data);
  toast(`The application cleared the stored key for ${provider}.`);
  loadSettings();
});

// Full reset — the nuclear option. Gated behind a type-to-confirm step
// (not a native confirm() dialog, to stay consistent with the rest of
// the app's custom UI) rather than a single click, since this
// permanently deletes every processed invoice, edit request, and
// notification.
function closeFullResetConfirm() {
  $("#full-reset-confirm").hidden = true;
  $("#full-reset-input").value = "";
  $("#confirm-full-reset-btn").disabled = true;
}

$("#open-full-reset-btn").addEventListener("click", () => {
  $("#full-reset-confirm").hidden = false;
  $("#full-reset-input").value = "";
  $("#confirm-full-reset-btn").disabled = true;
  $("#full-reset-input").focus();
});

$("#cancel-full-reset-btn").addEventListener("click", closeFullResetConfirm);

$("#full-reset-input").addEventListener("input", (e) => {
  $("#confirm-full-reset-btn").disabled = e.target.value.trim() !== "RESET";
});

$("#confirm-full-reset-btn").addEventListener("click", async () => {
  $("#confirm-full-reset-btn").disabled = true;
  $("#cancel-full-reset-btn").disabled = true;
  try {
    const res = await fetch("/api/full-reset", { method: "POST" });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "The application could not complete the full reset.");

    closeFullResetConfirm();
    $("#settings-overlay").classList.remove("open");
    toast("The application deleted all processed invoice history and reset the inventory.");

    // Every view that reads processed-invoice history, edit requests,
    // notifications, or the action log is now stale — refresh them all,
    // not just whichever tab happens to be active right now.
    loadInventory();
    loadProcessedInvoices();
    loadEditRequests();
    loadEditApprovals();
    loadActionLog();
    pollNotifications();
    $("#batch-summary").innerHTML = "";
    $("#batch-table-wrap").innerHTML = "";
    $("#single-result").innerHTML = "";
  } catch (err) {
    toast(err.message, true);
  } finally {
    $("#cancel-full-reset-btn").disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Single invoice — runs as a background job on the server (see
// /api/process/start), so this page can poll for live per-stage progress
// the same way the Batch Invoices tab does.
// ---------------------------------------------------------------------------

let selectedFile = null;
let selectedInvoiceName = "";

async function loadInvoiceOptions() {
  const res = await fetch("/api/invoices");
  const items = await res.json();
  const select = $("#invoice-select");

  // Clear everything except the placeholder — this re-runs whenever the
  // main invoice folder might have changed, so stale/duplicate options
  // from a previous folder must not accumulate.
  select.querySelectorAll("option[value]:not([value=''])").forEach((o) => o.remove());
  selectedInvoiceName = "";
  select.value = "";

  items.forEach((inv) => {
    const opt = document.createElement("option");
    opt.value = inv.name;
    opt.textContent = `${inv.name} (${inv.format})`;
    select.appendChild(opt);
  });
}

$("#invoice-select").addEventListener("change", (e) => {
  selectedInvoiceName = e.target.value;
  if (selectedInvoiceName) {
    selectedFile = null;
    $("#file-input").value = "";
    $("#dropzone-hint").textContent = "Drag a file here. Or click to browse.";
  }
  updateProcessButton();
});

const dropzone = $("#dropzone");
const fileInput = $("#file-input");
fileInput.addEventListener("change", () => {
  selectedFile = fileInput.files[0] || null;
  if (selectedFile) {
    selectedInvoiceName = "";
    $("#invoice-select").value = "";
    $("#dropzone-hint").textContent = selectedFile.name;
  }
  updateProcessButton();
});
["dragenter", "dragover"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  })
);
["dragleave", "drop"].forEach((evt) =>
  dropzone.addEventListener(evt, (e) => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  })
);
dropzone.addEventListener("drop", (e) => {
  const dropped = e.dataTransfer.files[0];
  if (dropped) {
    fileInput.files = e.dataTransfer.files;
    selectedFile = dropped;
    selectedInvoiceName = "";
    $("#invoice-select").value = "";
    $("#dropzone-hint").textContent = dropped.name;
  }
  updateProcessButton();
});

function updateProcessButton() {
  $("#process-btn").disabled = (!selectedFile && !selectedInvoiceName) || Boolean(activeSingleJobId && singlePollTimer);
}

function stageStatusHtml(stage, fileName) {
  const label = STAGE_LABEL[stage] || "Processing";
  return (
    `<span class="status-dot"></span>` +
    `<span class="status-text">${escapeHtml(label)}${fileName ? `: <span class="status-file">${escapeHtml(fileName)}</span>` : ""}</span>`
  );
}

// Mini status, next to the bell — always visible on every tab (shows
// "Idle" when nothing is running, rather than disappearing, so its
// absence never has to be interpreted), mirroring whichever of the three
// job types is currently running. Each job's own poller calls
// updateMiniStatus(source, stage, fileName) on every update (stage null
// means that source's job is no longer running); this just picks one to
// show, so a still-running job doesn't get hidden by a different one
// finishing.
const miniStatusState = { edit: null, single: null, batch: null };

// Each source maps to the tab a user would click to have started that kind
// of job in the first place -- an edit recheck is only ever kicked off from
// Edit Approvals, a single job from Single Invoice, a batch job from Batch
// Invoices -- so clicking the mini indicator while one is running jumps
// back to that tab.
const MINI_STATUS_TAB = { edit: "edit-approvals", single: "single", batch: "batch" };

function updateMiniStatus(source, stage, fileName) {
  miniStatusState[source] = stage ? { stage, fileName } : null;
  renderMiniStatus();
}

function renderMiniStatus() {
  const el = $("#mini-status");
  const activeSource = ["edit", "single", "batch"].find((k) => miniStatusState[k]);
  const active = activeSource ? miniStatusState[activeSource] : null;

  if (!active) {
    el.classList.add("idle");
    delete el.dataset.tab;
    $("#mini-status-text").textContent = "Idle";
    return;
  }

  el.classList.remove("idle");
  el.dataset.tab = MINI_STATUS_TAB[activeSource];
  const label = STAGE_LABEL[active.stage] || "Processing";
  $("#mini-status-text").textContent = active.fileName ? `${label}: ${active.fileName}` : label;
}

$("#mini-status").addEventListener("click", () => {
  const tab = $("#mini-status").dataset.tab;
  if (tab) switchToTab(tab);
});

let activeSingleJobId = null;
let singlePollTimer = null;

function renderSingleStatus(job) {
  const bar = $("#single-status-bar");
  bar.hidden = false;
  bar.innerHTML = stageStatusHtml(job.stage, null);
}

function stopSinglePolling() {
  if (singlePollTimer) clearInterval(singlePollTimer);
  singlePollTimer = null;
}

async function pollSingleJob() {
  if (!activeSingleJobId) return stopSinglePolling();
  const res = await fetch(`/api/process/status/${activeSingleJobId}`);
  if (!res.ok) return stopSinglePolling();
  handleSingleJobUpdate(await res.json());
}

function handleSingleJobUpdate(job) {
  activeSingleJobId = job.id;

  if (job.status === "running") {
    renderSingleStatus(job);
    updateProcessButton();
    updateMiniStatus("single", job.stage, job.file);
    if (!singlePollTimer) singlePollTimer = setInterval(() => pollSingleJob(), 900);
    return;
  }

  stopSinglePolling();
  $("#single-status-bar").hidden = true;
  updateProcessButton();
  updateMiniStatus("single", null);

  const resultArea = $("#single-result");
  if (job.status === "completed" && job.result) {
    resultArea.innerHTML = "";
    resultArea.appendChild(renderResultCard(job.result));
    refreshProcessedInvoicesIfVisible();
  } else {
    resultArea.innerHTML = `<div class="issue-item issue-critical">${escapeHtml(job.error || "The application could not process the invoice.")}</div>`;
  }
}

$("#process-btn").addEventListener("click", async () => {
  const resultArea = $("#single-result");
  resultArea.innerHTML = "";
  $("#process-btn").disabled = true;

  try {
    const form = new FormData();
    const params = new URLSearchParams();
    if (selectedInvoiceName) params.set("invoice_name", selectedInvoiceName);
    if (selectedFile) form.append("file", selectedFile);

    const res = await fetch(`/api/process/start?${params.toString()}`, { method: "POST", body: selectedFile ? form : undefined });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "The application could not start processing.");
    handleSingleJobUpdate(data);
  } catch (err) {
    resultArea.innerHTML = `<div class="issue-item issue-critical">${escapeHtml(err.message)}</div>`;
    updateProcessButton();
  }
});

// ---------------------------------------------------------------------------
// Rendering helpers
// ---------------------------------------------------------------------------

function renderResultCard(data) {
  const { invoice, validation, approval, payment, file } = data;
  const wrap = document.createElement("div");
  wrap.className = "result-card";

  const decisionBadge = approval ? DECISION_BADGE[approval.decision] : "badge-neutral";
  const paymentBadge = payment ? PAYMENT_BADGE[payment.status] : "badge-neutral";

  const issuesHtml = validation.issues.length
    ? `<ul class="issue-list">${validation.issues
        .map((i) => `<li class="issue-item ${SEVERITY_CLASS[i.severity]}">[${i.severity}] ${escapeHtml(i.code)}: ${escapeHtml(i.message)}</li>`)
        .join("")}</ul>`
    : `<span class="badge badge-green">No issues</span>`;

  wrap.innerHTML = `
    <div class="result-card-header">
      <h3>${escapeHtml(file)}${invoice?.invoice_number ? " · " + escapeHtml(invoice.invoice_number) : ""}</h3>
      <div class="row tight" style="margin:0">
        <span class="badge ${decisionBadge}">${approval ? approval.decision : "—"}</span>
        <span class="badge ${paymentBadge}">${payment ? payment.status : "—"}</span>
      </div>
    </div>
    <div class="result-card-body">
      <div class="stat-block">
        <h4>Vendor</h4><div class="value">${escapeHtml(invoice?.vendor || "—")}</div>
      </div>
      <div class="stat-block">
        <h4>Amount</h4><div class="value">${invoice ? money(invoice.total) + " " + escapeHtml(invoice.currency) : "—"}</div>
      </div>
      <div class="stat-block">
        <h4>Extraction method</h4><div class="value mono">${escapeHtml(invoice?.extraction_method || "—")}</div>
      </div>
      <div class="stat-block">
        <h4>Validation</h4>${issuesHtml}
      </div>
      <div class="stat-block" style="grid-column: 1 / -1">
        <h4>Approval reasoning</h4>
        <div class="reasoning-block">
          <span class="label">Draft</span>${escapeHtml(approval?.reasoning || "—")}
        </div>
        <div class="reasoning-block">
          <span class="label">Critique${approval?.critique_overturned ? " (changed the draft decision)" : ""}</span>
          <span class="${approval?.critique_overturned ? "overturned" : ""}">${escapeHtml(approval?.critique || "—")}</span>
        </div>
      </div>
      <div class="stat-block" style="grid-column: 1 / -1">
        <h4>Payment</h4>
        <div class="value">${escapeHtml(payment?.detail || "—")}</div>
      </div>
    </div>
  `;
  return wrap;
}

// ---------------------------------------------------------------------------
// Batch — folder-driven. "Process Folder" either uploads a newly-chosen
// folder, or (if none was chosen this session) re-runs the current main
// invoice folder server-side. Checking "Set as main invoice folder" before
// processing promotes the chosen folder to be the new default.
// ---------------------------------------------------------------------------

const FOLDER_SUPPORTED_EXTENSIONS = [".txt", ".json", ".csv", ".xml", ".pdf"];
let selectedFolderFiles = [];
let selectedFolderLabel = "";

function isSupportedFile(file) {
  const name = file.name.toLowerCase();
  return FOLDER_SUPPORTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function resetFolderSelection() {
  selectedFolderFiles = [];
  selectedFolderLabel = "";
  $("#folder-input").value = "";
  $("#folder-dropzone-hint").textContent = "Click to choose a folder…";
  $("#set-main-checkbox").checked = false;
}

async function loadMainFolderStatus() {
  const res = await fetch("/api/invoice-folder");
  const data = await res.json();
  $("#main-folder-status").innerHTML =
    `Main invoice folder: <strong>${escapeHtml(data.label)}</strong> (${data.file_count} file(s))`;
  $("#use-bundled-btn").hidden = !data.is_custom;
  return data;
}

$("#folder-input").addEventListener("change", (e) => {
  const all = Array.from(e.target.files);
  selectedFolderFiles = all.filter(isSupportedFile);
  selectedFolderLabel = all.length && all[0].webkitRelativePath
    ? all[0].webkitRelativePath.split("/")[0]
    : "Uploaded folder";

  const hint = $("#folder-dropzone-hint");
  if (!all.length) {
    hint.textContent = "Click to choose a folder…";
  } else if (!selectedFolderFiles.length) {
    hint.textContent = "This folder has no supported invoice files.";
  } else {
    hint.textContent = `${selectedFolderLabel}: ${selectedFolderFiles.length} invoice(s) found`;
  }
});

// A batch runs on the server as a background job, since a folder can hold
// many invoices and each one needs 1-2 LLM calls. The page polls the job's
// status and updates the status bar until the job stops running.
let activeJobId = null;
let pollTimer = null;
let lastKnownBatchResultsCount = 0;
let stopRequested = false;

function setBatchControls(running) {
  $("#process-folder-btn").disabled = running;
  $("#stop-batch-btn").hidden = !running;
  $("#stop-batch-btn").disabled = false;
}

// Click the batch status bar to see every invoice currently in flight (up
// to _BATCH_CONCURRENCY of them at once, now that ingestion and
// approval/payment run concurrently — see SOLUTION.md, "Batch concurrency
// and duplicate detection") and which stage each one is on. An invoice
// drops out of the list the moment it finishes, since job.in_progress
// (server.py) only ever holds what's still running.
let batchStatusDropdownOpen = false;
let lastBatchJob = null;

function renderStatusDropdown(job) {
  const dropdown = $("#batch-status-dropdown");
  const inProgress = (job && job.in_progress) || [];
  dropdown.innerHTML = inProgress.length
    ? inProgress
        .map(
          (p) =>
            `<div class="status-dropdown-item"><span class="file">${escapeHtml(p.file)}</span><span class="stage">${escapeHtml(
              STAGE_LABEL[p.stage] || p.stage
            )}</span></div>`
        )
        .join("")
    : `<div class="status-dropdown-item"><span class="file">Nothing in flight right now.</span></div>`;
}

function toggleStatusDropdown(open) {
  batchStatusDropdownOpen = open;
  $("#batch-status-dropdown").hidden = !open;
  if (open) renderStatusDropdown(lastBatchJob);
}

$("#batch-status-bar").addEventListener("click", () => {
  if ($("#batch-status-bar").hidden) return;
  toggleStatusDropdown(!batchStatusDropdownOpen);
});
document.addEventListener("click", (e) => {
  if (batchStatusDropdownOpen && !$("#batch-status-wrap").contains(e.target)) toggleStatusDropdown(false);
});

function renderStoppingStatus(job) {
  const bar = $("#batch-status-bar");
  bar.hidden = false;
  bar.classList.add("stopping", "clickable");
  bar.innerHTML = `<span class="status-dot"></span><span class="status-text">Stopping. The application discards any invoices still in progress rather than finishing them.</span>`;
  if (batchStatusDropdownOpen) renderStatusDropdown(job);
}

function renderRunningStatus(job) {
  const bar = $("#batch-status-bar");
  bar.hidden = false;
  bar.classList.remove("stopping");
  bar.classList.add("clickable");
  bar.innerHTML = stageStatusHtml(job.stage, job.current_file) + `<span class="status-count">${job.processed} of ${job.total} complete</span>`;
  if (batchStatusDropdownOpen) renderStatusDropdown(job);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
}

function handleJobUpdate(job) {
  activeJobId = job.id;

  if (job.status === "running") {
    lastBatchJob = job;
    setBatchControls(true);
    if (stopRequested) renderStoppingStatus(job);
    else renderRunningStatus(job);
    updateMiniStatus("batch", stopRequested ? null : job.stage, job.current_file);
    // Build the table up live, as each invoice finishes — same row format
    // as the completed-batch table below, just re-rendered on every poll.
    if (job.results && job.results.length) renderInvoiceTable($("#batch-table-wrap"), job.results);
    if (!pollTimer) pollTimer = setInterval(() => pollActiveJob(), 900);
    // Only refresh Processed Invoices when a new invoice has actually
    // landed, not on every 900ms poll — polling this unconditionally
    // rebuilt the whole table (and briefly showed its "Loading…"
    // placeholder) several times a second with nothing new to show,
    // which is what caused the tab to visibly flicker during a batch run.
    if (job.results.length !== lastKnownBatchResultsCount) {
      lastKnownBatchResultsCount = job.results.length;
      refreshProcessedInvoicesIfVisible();
    }
    return;
  }

  stopPolling();
  setBatchControls(false);
  $("#batch-status-bar").hidden = true;
  toggleStatusDropdown(false);
  lastBatchJob = null;
  stopRequested = false;
  updateMiniStatus("batch", null);
  lastKnownBatchResultsCount = 0;

  if (job.summary) {
    renderBatchSummary(job.summary);
    renderInvoiceTable($("#batch-table-wrap"), job.results);
  }
  if (job.status === "stopped") toast(`The batch stopped. The application saved ${job.results.length} invoice(s).`);
  if (job.errors && job.errors.length) toast(`The application could not process ${job.errors.length} invoice(s).`, true);
  if (job.skipped && job.skipped.length) toast(`The application skipped ${job.skipped.length} unsupported file(s).`);
  if (job.status === "error" && !job.summary) {
    $("#batch-summary").innerHTML = `<div class="issue-item issue-critical">The batch could not finish. Check the server log.</div>`;
  }
  loadMainFolderStatus();
  refreshProcessedInvoicesIfVisible();
}

async function pollActiveJob() {
  if (!activeJobId) return stopPolling();
  const res = await fetch(`/api/batch/status/${activeJobId}`);
  if (!res.ok) return stopPolling();
  handleJobUpdate(await res.json());
}

$("#process-folder-btn").addEventListener("click", async () => {
  const usingNewFolder = selectedFolderFiles.length > 0;
  stopRequested = false;
  $("#batch-summary").innerHTML = "";
  $("#batch-table-wrap").innerHTML = "";

  try {
    let res;
    if (usingNewFolder) {
      const form = new FormData();
      selectedFolderFiles.forEach((f) => form.append("files", f, f.name));
      form.append("set_as_main", $("#set-main-checkbox").checked ? "true" : "false");
      form.append("folder_label", selectedFolderLabel);
      res = await fetch("/api/batch/start-upload", { method: "POST", body: form });
    } else {
      res = await fetch("/api/batch/start", { method: "POST" });
    }

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "The application could not start the batch.");
    if (usingNewFolder) resetFolderSelection();
    loadMainFolderStatus();
    handleJobUpdate(data);
  } catch (err) {
    toast(err.message, true);
  }
});

$("#stop-batch-btn").addEventListener("click", async () => {
  if (!activeJobId) return;
  stopRequested = true;
  $("#stop-batch-btn").disabled = true;
  renderStoppingStatus(lastBatchJob);
  await fetch(`/api/batch/stop/${activeJobId}`, { method: "POST" });
});

async function reattachToActiveBatch() {
  const res = await fetch("/api/batch/current");
  if (!res.ok) return;
  const job = await res.json();
  if (job && job.status === "running") {
    toast("A batch is already running. The page reattached to it.");
    handleJobUpdate(job);
  }
}

$("#use-bundled-btn").addEventListener("click", async () => {
  await fetch("/api/invoice-folder/reset", { method: "POST" });
  resetFolderSelection();
  await loadMainFolderStatus();
  toast("The application reset the main invoice folder to the bundled dataset.");
});

$("#reset-inventory-btn").addEventListener("click", async () => {
  await resetInventory();
  $("#batch-summary").innerHTML = "";
  $("#batch-table-wrap").innerHTML = "";
});

function renderBatchSummary(summary) {
  $("#batch-summary").innerHTML = `
    <div class="stat-card"><h4>Processed</h4><div class="big">${summary.total_processed}</div></div>
    <div class="stat-card"><h4>Approved</h4><div class="big green">${summary.counts.approved}</div></div>
    <div class="stat-card"><h4>Rejected</h4><div class="big red">${summary.counts.rejected}</div></div>
    <div class="stat-card"><h4>Escalated</h4><div class="big">${summary.counts.escalated}</div></div>
    <div class="stat-card"><h4>Approved Payments</h4><div class="big green">${money(summary.amount_approved)}</div></div>
    <div class="stat-card"><h4>Flagged or Withheld</h4><div class="big red">${money(summary.amount_flagged)}</div></div>
  `;
}

// A reusable results table: the Batch Invoices tab and the Processed
// Invoices tab both render this same shape of row, and both open the same
// breakdown pop-up window on click. Column headers explain what Validation,
// Decision, and Payment mean (VALIDATION_HEADER_TITLE etc., common.js); a
// red or amber value also explains why, via the browser's native title
// tooltip. The Edit button opens the same pop-up directly in edit mode.

function validationReason(validation) {
  if (!validation || validation.passed) return "";
  return validation.issues.map((i) => i.message).join(" ");
}

function decisionReason(approval) {
  if (!approval || approval.decision === "approved") return "";
  // escalation_resolution, when set, is a person's final call on a
  // previously-escalated invoice -- more relevant here than the model's
  // original summary, which only explains why it was escalated.
  return approval.escalation_resolution || approval.summary || approval.reasoning || "";
}

function paymentReason(payment) {
  if (!payment || payment.status === "paid") return "";
  return payment.detail || "";
}

function openInvoiceDetail(runId, opts = {}) {
  if (!runId) return;
  const params = new URLSearchParams({ id: runId });
  if (opts.edit) params.set("edit", "1");
  window.open(`/static/invoice-detail.html?${params.toString()}`, "_blank", "width=760,height=860,noopener");
}

function renderInvoiceTable(wrap, results) {
  const rows = results
    .map((r) => {
      const inv = r.invoice || {};
      const vReason = validationReason(r.validation);
      const dReason = decisionReason(r.approval);
      const pReason = paymentReason(r.payment);
      return `
        <tr class="row-clickable" data-run-id="${escapeAttr(r.run_id || "")}">
          <td>${escapeHtml(inv.invoice_number || r.file)}</td>
          <td>${escapeHtml(inv.vendor || "—")}</td>
          <td class="num center">${money(inv.total)}</td>
          <td class="center"${vReason ? ` title="${escapeAttr(vReason)}"` : ""}><span class="badge ${
        r.validation.passed ? "badge-green" : "badge-red"
      }">${r.validation.passed ? "passed" : r.validation.issues.length + " issue(s)"}</span></td>
          <td class="center"${dReason ? ` title="${escapeAttr(dReason)}"` : ""}><span class="badge ${
        DECISION_BADGE[r.approval.decision]
      }">${r.approval.decision}</span></td>
          <td class="center"${pReason ? ` title="${escapeAttr(pReason)}"` : ""}><span class="badge ${
        PAYMENT_BADGE[r.payment.status]
      }">${r.payment.status}</span></td>
          <td>${r.processed_at ? escapeHtml(new Date(r.processed_at).toLocaleString()) : "—"}</td>
          <td><button class="btn-link edit-row-btn" type="button" data-run-id="${escapeAttr(r.run_id || "")}">Edit</button></td>
        </tr>`;
    })
    .join("");

  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th>Invoice</th><th>Vendor</th><th class="center">Amount</th>
        <th class="center" title="${VALIDATION_HEADER_TITLE}">Validation</th>
        <th class="center" title="${DECISION_HEADER_TITLE}">Decision</th>
        <th class="center" title="${PAYMENT_HEADER_TITLE}">Payment</th>
        <th>Processed At</th>
        <th></th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;

  wrap.querySelectorAll("tr.row-clickable").forEach((tr) => {
    tr.addEventListener("click", () => openInvoiceDetail(tr.dataset.runId));
  });
  wrap.querySelectorAll(".edit-row-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      openInvoiceDetail(btn.dataset.runId, { edit: true });
    });
  });
}

// ---------------------------------------------------------------------------
// Processed invoices — a history across every Single Invoice and Batch
// Invoices run, read from the server's per-invoice run logs. Refreshed
// live (not just on tab click) whenever an invoice finishes anywhere in
// the app, so a completed invoice shows up here immediately if this tab
// is already open.
// ---------------------------------------------------------------------------

async function loadProcessedInvoices(opts = {}) {
  const wrap = $("#processed-table-wrap");
  // A background refresh (a batch/single/edit job completing an invoice
  // while this tab happens to be open) swaps the table in place once the
  // fetch resolves — blanking it to "Loading…" first, as a tab-click load
  // does, tore the table down and rebuilt it on every refresh, which is
  // what made the tab visibly flicker.
  if (!opts.silent) wrap.innerHTML = `<p class="spinner-text">Loading&hellip;</p>`;

  const res = await fetch("/api/processed-invoices");
  const rows = await res.json();

  if (!rows.length) {
    wrap.innerHTML = `<div class="empty-state">The application has not processed any invoices yet.</div>`;
    return;
  }
  renderInvoiceTable(wrap, rows);
}

function refreshProcessedInvoicesIfVisible() {
  if ($("#tab-processed").classList.contains("active")) loadProcessedInvoices({ silent: true });
}

// ---------------------------------------------------------------------------
// Edit requests / Edit approvals
// ---------------------------------------------------------------------------

// Approving an edit request starts a background recheck job (validation ->
// approval -> payment, real LLM calls), same pattern as Single Invoice and
// Batch — polled here rather than blocking, and reflected in the mini
// status indicator next to the bell so it's visible from any tab.
let activeEditJobId = null;
let editPollTimer = null;

function stopEditPolling() {
  if (editPollTimer) clearInterval(editPollTimer);
  editPollTimer = null;
}

async function pollEditJob() {
  if (!activeEditJobId) return stopEditPolling();
  const res = await fetch(`/api/edit-jobs/${activeEditJobId}`);
  if (!res.ok) return stopEditPolling();
  handleEditJobUpdate(await res.json());
}

function handleEditJobUpdate(job) {
  activeEditJobId = job.id;

  if (job.status === "running") {
    updateMiniStatus("edit", job.stage, job.file);
    if (!editPollTimer) editPollTimer = setInterval(() => pollEditJob(), 900);
    return;
  }

  stopEditPolling();
  updateMiniStatus("edit", null);

  if (job.status === "completed" && job.result) {
    toast("The application approved the edit and rechecked the invoice.");
  } else {
    toast(job.error || "The application could not approve this edit.", true);
  }
  loadEditApprovals();
  refreshProcessedInvoicesIfVisible();
}

function formatEditValue(v) {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return `${v.length} line item(s)`;
  return String(v);
}

function formatChanges(changes) {
  return Object.entries(changes || {})
    .map(([field, c]) => `${field}: ${formatEditValue(c.old)} → ${formatEditValue(c.new)}`)
    .join("; ");
}

async function loadEditRequests() {
  const wrap = $("#edit-requests-table-wrap");
  wrap.innerHTML = `<p class="spinner-text">Loading&hellip;</p>`;
  const res = await fetch("/api/edit-requests");
  const rows = await res.json();

  if (!rows.length) {
    wrap.innerHTML = `<div class="empty-state">No edit requests yet. Open an invoice's breakdown and click Edit to propose a change.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table>
      <thead><tr><th>Invoice</th><th>Change</th><th>Status</th><th>Requested at</th><th>Result</th></tr></thead>
      <tbody>${rows
        .map(
          (r) => `
        <tr>
          <td>${escapeHtml(r.edited_invoice?.invoice_number || r.run_id)}</td>
          <td>${escapeHtml(formatChanges(r.changes))}</td>
          <td><span class="badge ${EDIT_STATUS_BADGE[r.status] || "badge-neutral"}">${r.status}</span></td>
          <td>${r.requested_at ? new Date(r.requested_at).toLocaleString() : "—"}</td>
          <td>${r.new_run_id ? `<button class="btn-link view-result-btn" type="button" data-run-id="${escapeAttr(r.new_run_id)}">View result</button>` : "—"}</td>
        </tr>`
        )
        .join("")}</tbody>
    </table>
  `;

  wrap.querySelectorAll(".view-result-btn").forEach((btn) => {
    btn.addEventListener("click", () => openInvoiceDetail(btn.dataset.runId));
  });
}

async function loadEditApprovals() {
  const wrap = $("#edit-approvals-table-wrap");
  wrap.innerHTML = `<p class="spinner-text">Loading&hellip;</p>`;

  // Escalations have no store of their own — an invoice's own
  // approval.decision is "escalated" or it isn't, so this filters the same
  // processed-invoices list the Processed Invoices tab reads.
  const [editsRes, invoicesRes] = await Promise.all([
    fetch("/api/edit-requests?status=pending"),
    fetch("/api/processed-invoices"),
  ]);
  const edits = await editsRes.json();
  const allInvoices = await invoicesRes.json();
  const escalations = allInvoices.filter((r) => r.approval && r.approval.decision === "escalated");

  if (!edits.length && !escalations.length) {
    wrap.innerHTML = `<div class="empty-state">Nothing is waiting for approval.</div>`;
    return;
  }

  let html = "";

  if (escalations.length) {
    html += `
      <h3 class="section-heading">Escalated invoices</h3>
      <table>
        <thead><tr><th>Invoice</th><th>Vendor</th><th>Amount</th><th>Why it was escalated</th><th></th></tr></thead>
        <tbody>${escalations
          .map((r) => {
            const reason = validationReason(r.validation) || decisionReason(r.approval) || "—";
            return `
          <tr class="row-clickable" data-run-id="${escapeAttr(r.run_id)}">
            <td>${escapeHtml(r.invoice.invoice_number || r.file)}</td>
            <td>${escapeHtml(r.invoice.vendor || "—")}</td>
            <td class="num">${money(r.invoice.total)}</td>
            <td>${escapeHtml(reason)}</td>
            <td class="row tight" style="margin:0; flex-wrap:nowrap">
              <button class="btn btn-primary btn-sm approve-escalation-btn" type="button">Approve</button>
              <button class="btn btn-danger-ghost btn-sm deny-escalation-btn" type="button">Deny</button>
            </td>
          </tr>`;
          })
          .join("")}</tbody>
      </table>
    `;
  }

  if (edits.length) {
    html += `
      <h3 class="section-heading">Edit requests</h3>
      <table>
        <thead><tr><th>Invoice</th><th>Change</th><th>Requested at</th><th></th></tr></thead>
        <tbody>${edits
          .map(
            (r) => `
        <tr data-edit-id="${escapeAttr(r.id)}">
          <td>${escapeHtml(r.edited_invoice?.invoice_number || r.run_id)}</td>
          <td>${escapeHtml(formatChanges(r.changes))}</td>
          <td>${r.requested_at ? new Date(r.requested_at).toLocaleString() : "—"}</td>
          <td class="row tight" style="margin:0; flex-wrap:nowrap">
            <button class="btn btn-primary btn-sm approve-edit-btn" type="button">Approve</button>
            <button class="btn btn-danger-ghost btn-sm reject-edit-btn" type="button">Reject</button>
          </td>
        </tr>`
          )
          .join("")}</tbody>
      </table>
    `;
  }

  wrap.innerHTML = html;

  wrap.querySelectorAll("tr[data-run-id]").forEach((tr) => {
    const runId = tr.dataset.runId;
    const approveBtn = tr.querySelector(".approve-escalation-btn");
    const denyBtn = tr.querySelector(".deny-escalation-btn");

    tr.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      openInvoiceDetail(runId);
    });

    approveBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      approveBtn.disabled = true;
      denyBtn.disabled = true;
      try {
        const res = await fetch(`/api/escalations/${runId}/approve`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "The application could not approve this invoice.");
        toast("The application approved the escalated invoice.");
        loadEditApprovals();
        refreshProcessedInvoicesIfVisible();
      } catch (err) {
        toast(err.message, true);
        approveBtn.disabled = false;
        denyBtn.disabled = false;
      }
    });

    denyBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      approveBtn.disabled = true;
      denyBtn.disabled = true;
      try {
        const res = await fetch(`/api/escalations/${runId}/deny`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "The application could not deny this invoice.");
        toast("The application denied the escalated invoice.");
        loadEditApprovals();
        refreshProcessedInvoicesIfVisible();
      } catch (err) {
        toast(err.message, true);
        approveBtn.disabled = false;
        denyBtn.disabled = false;
      }
    });
  });

  wrap.querySelectorAll("tr[data-edit-id]").forEach((tr) => {
    const id = tr.dataset.editId;
    const approveBtn = tr.querySelector(".approve-edit-btn");
    const rejectBtn = tr.querySelector(".reject-edit-btn");

    approveBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      try {
        const res = await fetch(`/api/edit-requests/${id}/approve`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "The application could not approve this edit.");
        handleEditJobUpdate(data);
      } catch (err) {
        toast(err.message, true);
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
      }
    });

    rejectBtn.addEventListener("click", async () => {
      approveBtn.disabled = true;
      rejectBtn.disabled = true;
      try {
        const res = await fetch(`/api/edit-requests/${id}/reject`, { method: "POST" });
        if (!res.ok) {
          const data = await res.json();
          throw new Error(data.detail || "The application could not reject this edit.");
        }
        toast("The application rejected the edit.");
        loadEditApprovals();
      } catch (err) {
        toast(err.message, true);
        approveBtn.disabled = false;
        rejectBtn.disabled = false;
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Notifications — polls the server's small in-memory feed (src/notifications.py).
// Anything newer than the last id this page has seen gets a sound, a
// bottom-right toast, and a line in the bell panel. The first poll after
// load just seeds the "last seen" cursor — it must not replay history as a
// beep storm.
// ---------------------------------------------------------------------------

let lastSeenNotificationId = null;
let audioCtx = null;

function ensureAudioContext() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (Ctx) audioCtx = new Ctx();
  } else if (audioCtx.state === "suspended") {
    audioCtx.resume();
  }
}
// AudioContext starts suspended until a user gesture — this primes it on
// the first click anywhere, so a later notification can actually play.
document.addEventListener("click", ensureAudioContext, { once: true });

function playNotificationSound() {
  if (!audioCtx) return; // no gesture yet — best-effort, skip silently
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = "sine";
  osc.frequency.value = 880;
  gain.gain.setValueAtTime(0.0001, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.15, audioCtx.currentTime + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.28);
  osc.connect(gain);
  gain.connect(audioCtx.destination);
  osc.start();
  osc.stop(audioCtx.currentTime + 0.3);
}

function showNotificationToast(notification) {
  const stack = $("#notification-toasts");
  const el = document.createElement("div");
  el.className = `notification-toast${notification.type === "invoice_escalated" ? " escalated" : ""}`;
  el.textContent = notification.message;
  stack.appendChild(el);
  setTimeout(() => el.classList.add("hide"), 4500);
  setTimeout(() => el.remove(), 5000);
}

// Where clicking a notification should take you. invoice_completed covers
// Single Invoice, Batch, and edit-recheck completions alike -- all three
// land in Processed Invoices. Both escalation and a pending edit need the
// same action (a decision in Edit Approvals), so both map there too.
const NOTIFICATION_TAB = {
  invoice_completed: "processed",
  invoice_escalated: "edit-approvals",
  edit_pending: "edit-approvals",
};

function renderNotificationPanel(list) {
  const panelList = $("#notification-list");
  if (!list.length) {
    panelList.innerHTML = `<div class="empty-state">No notifications yet.</div>`;
    return;
  }
  panelList.innerHTML = list
    .map((n) => {
      const targetTab = NOTIFICATION_TAB[n.type] || "";
      return `
      <div class="notification-item${n.read ? "" : " unread"}${n.type === "invoice_escalated" ? " escalated" : ""}"${
        targetTab ? ` data-tab="${targetTab}"` : ""
      }>
        <p>${escapeHtml(n.message)}</p>
        <span class="notification-time">${new Date(n.created_at).toLocaleString()}</span>
      </div>`;
    })
    .join("");

  panelList.querySelectorAll(".notification-item[data-tab]").forEach((el) => {
    el.addEventListener("click", () => {
      switchToTab(el.dataset.tab);
      $("#notification-panel").hidden = true;
    });
  });
}

function updateNotificationBadge(list) {
  const count = list.filter((n) => !n.read).length;
  const badge = $("#notification-badge");
  badge.textContent = count > 9 ? "9+" : String(count);
  badge.hidden = count === 0;
}

async function pollNotifications() {
  try {
    const res = await fetch("/api/notifications");
    if (!res.ok) return;
    const list = await res.json(); // newest first

    if (lastSeenNotificationId === null) {
      lastSeenNotificationId = list.length ? list[0].id : 0;
      renderNotificationPanel(list);
      updateNotificationBadge(list);
      return;
    }

    const fresh = list.filter((n) => n.id > lastSeenNotificationId);
    if (fresh.length) {
      lastSeenNotificationId = list[0].id;
      fresh
        .slice()
        .reverse() // oldest-first, so a burst plays in the order it happened
        .forEach((n) => {
          playNotificationSound();
          showNotificationToast(n);
        });
    }
    renderNotificationPanel(list);
    updateNotificationBadge(list);
  } catch (err) {
    // Best-effort — a dead server shouldn't spam the console every few seconds.
  }
}

$("#notification-bell").addEventListener("click", async (e) => {
  e.stopPropagation();
  const panel = $("#notification-panel");
  const opening = panel.hidden;
  panel.hidden = !opening;
  if (opening) {
    const res = await fetch("/api/notifications/read-all", { method: "POST" });
    if (res.ok) {
      const list = await res.json();
      renderNotificationPanel(list);
      updateNotificationBadge(list);
    }
  }
});
document.addEventListener("click", (e) => {
  const panel = $("#notification-panel");
  const bell = $("#notification-bell");
  if (!panel.hidden && !panel.contains(e.target) && !bell.contains(e.target)) panel.hidden = true;
});

setInterval(pollNotifications, 3000);

// ---------------------------------------------------------------------------
// Inventory
// ---------------------------------------------------------------------------

// Freely editable — there's no real ERP for this simulated environment to
// sync from, so inventory.db is the system of record, and the seed data
// is just its state-0 baseline (restorable any time via Reset to
// Original). Item name is the one fixed column: it's the lookup key
// validation normalizes against and item_aliases references by foreign
// key, so /api/inventory/update deliberately never lets it change.
async function loadInventory() {
  const res = await fetch("/api/inventory");
  const data = await res.json();
  const wrap = $("#inventory-table-wrap");
  $("#save-inventory-btn").disabled = true;

  if (!data.initialized || !data.items.length) {
    wrap.innerHTML = `<div class="empty-state">The application found no inventory database. Click Reset to Original to create one.</div>`;
    return;
  }

  wrap.innerHTML = `
    <table>
      <thead><tr><th>Item</th><th>Stock</th><th>Unit price</th><th>Category</th></tr></thead>
      <tbody>${data.items
        .map(
          (i) => `<tr data-item="${escapeAttr(i.item)}">
            <td>${escapeHtml(i.item)}</td>
            <td class="num"><input type="number" step="1" class="num-input inventory-stock" value="${i.stock}" /></td>
            <td class="num"><input type="number" step="any" class="num-input inventory-price" value="${i.unit_price ?? ""}" /></td>
            <td><input type="text" class="inventory-category" value="${escapeAttr(i.category || "")}" /></td>
          </tr>`
        )
        .join("")}</tbody>
    </table>
  `;

  wrap.querySelectorAll("input").forEach((input) => {
    input.addEventListener("input", () => {
      $("#save-inventory-btn").disabled = false;
    });
  });
}

$("#save-inventory-btn").addEventListener("click", async () => {
  const items = $$("#inventory-table-wrap tr[data-item]").map((tr) => {
    const price = tr.querySelector(".inventory-price").value;
    return {
      item: tr.dataset.item,
      stock: Number(tr.querySelector(".inventory-stock").value) || 0,
      unit_price: price === "" ? null : Number(price),
      category: tr.querySelector(".inventory-category").value.trim() || null,
    };
  });

  $("#save-inventory-btn").disabled = true;
  try {
    const res = await fetch("/api/inventory/update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "The application could not save the inventory changes.");
    toast("The application saved the inventory changes.");
    loadInventory();
  } catch (err) {
    toast(err.message, true);
    $("#save-inventory-btn").disabled = false;
  }
});

async function resetInventory() {
  await fetch("/api/inventory/reset", { method: "POST" });
  toast("The application reset the inventory. It restocked every item and cleared the payment ledger.");
  loadInventory();
}

$("#reset-inventory-tab-btn").addEventListener("click", () => resetInventory());

// ---------------------------------------------------------------------------
// Action log — every LLM call the application makes (ingestion extraction,
// approval draft, approval critique), recorded server-side at the call
// site (see src/action_log.py). Read-only, loaded on tab click like
// Processed Invoices; no live polling since nothing here needs
// second-by-second freshness.
// ---------------------------------------------------------------------------

function renderActionLogRow(e) {
  const resultCell = e.error
    ? `<span class="badge badge-red" title="${escapeAttr(e.error)}">error</span>`
    : `<span class="action-log-text">${escapeHtml(e.result_summary || "—")}</span>`;
  return `
    <tr>
      <td>${escapeHtml(new Date(e.timestamp).toLocaleString())}</td>
      <td>${escapeHtml(e.purpose)}</td>
      <td>${escapeHtml(e.context || "—")}</td>
      <td>${escapeHtml(e.provider)} &middot; ${escapeHtml(e.model)}</td>
      <td class="action-log-text">${escapeHtml(e.prompt_preview)}</td>
      <td>${resultCell}</td>
    </tr>`;
}

async function loadActionLog() {
  const wrap = $("#action-log-table-wrap");
  wrap.innerHTML = `<p class="spinner-text">Loading&hellip;</p>`;

  const res = await fetch("/api/action-log");
  const rows = await res.json();

  if (!rows.length) {
    wrap.innerHTML = `<div class="empty-state">The application has not called the LLM provider yet.</div>`;
    return;
  }
  wrap.innerHTML = `
    <table>
      <thead><tr>
        <th>Time</th><th>Purpose</th><th>Invoice / File</th><th>Model</th><th>Prompt</th><th>Result</th>
      </tr></thead>
      <tbody>${rows.map(renderActionLogRow).join("")}</tbody>
    </table>
  `;
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadSettings();
loadInvoiceOptions();
loadMainFolderStatus();
reattachToActiveBatch();
pollNotifications();
