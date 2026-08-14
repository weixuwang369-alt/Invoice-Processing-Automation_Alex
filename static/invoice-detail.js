let invoiceData = null;
let editMode = false;

async function load() {
  const content = $("#detail-content");
  const id = new URLSearchParams(window.location.search).get("id");

  if (!id) {
    content.innerHTML = `<div class="issue-item issue-critical">This window needs an invoice id. Open it by clicking a row in the application.</div>`;
    return;
  }

  let data;
  try {
    const res = await fetch(`/api/processed-invoices/${encodeURIComponent(id)}`);
    data = await res.json();
    if (!res.ok) throw new Error(data.detail || "The application could not load this invoice.");
  } catch (err) {
    content.innerHTML = `<div class="issue-item issue-critical">${escapeHtml(err.message)}</div>`;
    return;
  }

  invoiceData = data;
  document.title = `Invoice breakdown: ${data.file}`;
  editMode = new URLSearchParams(window.location.search).get("edit") === "1";
  render();
}

function render() {
  if (editMode) renderEditForm();
  else renderView();
}

function renderView() {
  const content = $("#detail-content");
  const data = invoiceData;
  const { invoice, validation, approval, payment, edited_from } = data;

  const lineItemRows = (invoice?.line_items || [])
    .map(
      (li) => `
        <tr>
          <td>${escapeHtml(li.item)}</td>
          <td class="num">${li.quantity}</td>
          <td class="num">${money(li.unit_price)}</td>
          <td class="num">${money(li.amount)}</td>
        </tr>`
    )
    .join("");

  const issuesHtml =
    validation && validation.issues.length
      ? `<ul class="issue-list">${validation.issues
          .map(
            (i) =>
              `<li class="issue-item ${SEVERITY_CLASS[i.severity]}">[${i.severity}] ${escapeHtml(i.code)}: ${escapeHtml(i.message)}</li>`
          )
          .join("")}</ul>`
      : `<span class="badge badge-green">No issues found</span>`;

  content.innerHTML = `
    <div class="detail-header">
      <div>
        <h1>${escapeHtml(invoice?.invoice_number || data.file)}</h1>
        <p class="muted small">${escapeHtml(data.file)}<br>Processed at: ${escapeHtml(data.processed_at || "—")}</p>
        ${edited_from ? `<p class="muted small">This is the result of an approved edit. <a href="#" id="view-original-link">View the original run</a>.</p>` : ""}
      </div>
      <div class="row tight" style="margin:0">
        <div class="badge-field">
          <span class="badge-field-label" title="${DECISION_HEADER_TITLE}">Decision</span>
          <span class="badge ${DECISION_BADGE[approval?.decision] || "badge-neutral"}">${approval?.decision || "—"}</span>
        </div>
        <div class="badge-field">
          <span class="badge-field-label" title="${PAYMENT_HEADER_TITLE}">Payment</span>
          <span class="badge ${PAYMENT_BADGE[payment?.status] || "badge-neutral"}">${payment?.status || "—"}</span>
        </div>
        <button id="edit-invoice-btn" class="btn btn-ghost" type="button">Edit</button>
      </div>
    </div>

    <div class="card">
      <h2>Vendor</h2>
      <p>${escapeHtml(invoice?.vendor || "—")}</p>
    </div>

    <div class="card">
      <h2>How the application calculated the total</h2>
      ${
        lineItemRows
          ? `<table>
              <thead><tr><th>Item</th><th class="num">Quantity</th><th class="num">Unit price</th><th class="num">Amount</th></tr></thead>
              <tbody>${lineItemRows}</tbody>
            </table>`
          : `<p class="muted small">This invoice has no line items.</p>`
      }
      <div class="calc-summary">
        <div><span>Subtotal</span><span>${money(invoice?.subtotal)}</span></div>
        <div><span>Tax</span><span>${money(invoice?.tax_amount)}</span></div>
        <div><span>Shipping</span><span>${money(invoice?.shipping)}</span></div>
        <div class="calc-total"><span>Total</span><span>${money(invoice?.total)} ${escapeHtml(invoice?.currency || "")}</span></div>
      </div>
    </div>

    <div class="card">
      <h2>Validation</h2>
      <p class="muted small">The application checked this invoice against inventory, arithmetic, and payment history.</p>
      ${issuesHtml}
    </div>

    <div class="card">
      <h2>Approval</h2>
      <div class="reasoning-block">
        <span class="label">Draft</span>${escapeHtml(approval?.reasoning || "—")}
      </div>
      <div class="reasoning-block">
        <span class="label">Critique${approval?.critique_overturned ? " (changed the draft decision)" : ""}</span>
        <span class="${approval?.critique_overturned ? "overturned" : ""}">${escapeHtml(approval?.critique || "—")}</span>
      </div>
      ${
        approval?.escalation_resolution
          ? `<div class="reasoning-block">
              <span class="label">Human resolution</span>${escapeHtml(approval.escalation_resolution)}
            </div>`
          : ""
      }
    </div>

    <div class="card">
      <h2>Payment</h2>
      <p>${escapeHtml(payment?.detail || "—")}</p>
    </div>
  `;

  $("#edit-invoice-btn").addEventListener("click", () => {
    editMode = true;
    render();
  });
  const originalLink = $("#view-original-link");
  if (originalLink) {
    originalLink.addEventListener("click", (e) => {
      e.preventDefault();
      window.location.search = `?id=${encodeURIComponent(edited_from)}`;
    });
  }
}

// Editable fields cover the "pertinent figures" — the extraction metadata
// (extraction_method, extraction_warnings, source_file) stays fixed since
// it describes how the ORIGINAL value was produced, not a fact about the
// invoice itself.
function renderEditForm() {
  const content = $("#detail-content");
  const data = invoiceData;
  const invoice = data.invoice || {};

  const lineItemRows = (invoice.line_items || [])
    .map(
      (li, i) => `
        <tr>
          <td><input type="text" data-field="line_items.${i}.item" value="${escapeAttr(li.item ?? "")}" /></td>
          <td><input type="number" step="any" class="num-input" data-field="line_items.${i}.quantity" value="${li.quantity ?? ""}" /></td>
          <td><input type="number" step="any" class="num-input" data-field="line_items.${i}.unit_price" value="${li.unit_price ?? ""}" /></td>
          <td><input type="number" step="any" class="num-input" data-field="line_items.${i}.amount" value="${li.amount ?? ""}" /></td>
        </tr>`
    )
    .join("");

  content.innerHTML = `
    <div class="detail-header">
      <div>
        <h1>Edit invoice</h1>
        <p class="muted small">${escapeHtml(data.file)}</p>
      </div>
    </div>

    <div class="notice">
      This edit needs approval before it takes effect. For this demo, you approve your own edit requests.
      In a real deployment, a separate approver would do this.
    </div>

    <div class="card">
      <h2>Invoice fields</h2>
      <div class="edit-grid">
        <label class="field"><span>Vendor</span><input type="text" data-field="vendor" value="${escapeAttr(invoice.vendor ?? "")}" /></label>
        <label class="field"><span>Invoice number</span><input type="text" data-field="invoice_number" value="${escapeAttr(invoice.invoice_number ?? "")}" /></label>
        <label class="field"><span>Date</span><input type="text" data-field="date" value="${escapeAttr(invoice.date ?? "")}" /></label>
        <label class="field"><span>Due date</span><input type="text" data-field="due_date" value="${escapeAttr(invoice.due_date ?? "")}" /></label>
        <label class="field"><span>Currency</span><input type="text" data-field="currency" value="${escapeAttr(invoice.currency ?? "USD")}" /></label>
      </div>
    </div>

    <div class="card">
      <h2>Line items</h2>
      ${
        lineItemRows
          ? `<table>
              <thead><tr><th>Item</th><th class="num">Quantity</th><th class="num">Unit price</th><th class="num">Amount</th></tr></thead>
              <tbody>${lineItemRows}</tbody>
            </table>`
          : `<p class="muted small">This invoice has no line items.</p>`
      }
    </div>

    <div class="card">
      <h2>Totals</h2>
      <div class="edit-grid">
        <label class="field"><span>Subtotal</span><input type="number" step="any" class="num-input" data-field="subtotal" value="${invoice.subtotal ?? ""}" /></label>
        <label class="field"><span>Tax</span><input type="number" step="any" class="num-input" data-field="tax_amount" value="${invoice.tax_amount ?? ""}" /></label>
        <label class="field"><span>Shipping</span><input type="number" step="any" class="num-input" data-field="shipping" value="${invoice.shipping ?? ""}" /></label>
        <label class="field"><span>Total</span><input type="number" step="any" class="num-input" data-field="total" value="${invoice.total ?? ""}" /></label>
      </div>
    </div>

    <div class="row tight">
      <button id="submit-edit-btn" class="btn btn-primary" type="button">Submit for approval</button>
      <button id="cancel-edit-btn" class="btn btn-ghost" type="button">Cancel</button>
    </div>
    <div id="edit-form-msg"></div>
  `;

  $("#cancel-edit-btn").addEventListener("click", () => {
    editMode = false;
    render();
  });
  $("#submit-edit-btn").addEventListener("click", submitEdit);
}

async function submitEdit() {
  const content = $("#detail-content");
  const inputs = content.querySelectorAll("[data-field]");
  const edited = { line_items: (invoiceData.invoice.line_items || []).map((li) => ({ ...li })) };

  inputs.forEach((input) => {
    const path = input.dataset.field;
    const raw = input.value;
    const value = input.type === "number" ? (raw === "" ? null : Number(raw)) : raw;
    if (path.startsWith("line_items.")) {
      const [, idx, key] = path.split(".");
      edited.line_items[Number(idx)][key] = value;
    } else {
      edited[path] = value;
    }
  });

  const submitBtn = $("#submit-edit-btn");
  const cancelBtn = $("#cancel-edit-btn");
  submitBtn.disabled = true;
  cancelBtn.disabled = true;

  try {
    const res = await fetch("/api/edit-requests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: invoiceData.run_id, edited_invoice: edited }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "The application could not submit this edit.");

    content.innerHTML = `
      <div class="card">
        <h2>Edit submitted</h2>
        <p>This edit is pending approval. Open the Edit Approvals tab in the main window to approve or reject it.</p>
      </div>
    `;
  } catch (err) {
    $("#edit-form-msg").innerHTML = `<div class="issue-item issue-critical">${escapeHtml(err.message)}</div>`;
    submitBtn.disabled = false;
    cancelBtn.disabled = false;
  }
}

load();
