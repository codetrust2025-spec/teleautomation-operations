import { useState, useEffect, useCallback, useMemo, useRef, Fragment } from "react";
import AiProcessingStatus from "../components/AiProcessingStatus.jsx";
import { useDialogA11y } from "../hooks/useDialogA11y.js";
import ReferrerPaymentAccounts, {
  fetchReferrerRegistryJson,
} from "./ReferrerPaymentAccounts.jsx";

/**
 * Manage handler payouts — redesigned wide modal.
 * Two-column top row (form | filters), full-width paginated table below.
 * No internal scrolling on desktop.
 */

const ROWS_PER_PAGE = 7;

function todayInputValue() {
  const now = new Date();
  return [
    now.getFullYear(),
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
  ].join("-");
}

function currentMonthValue() {
  return todayInputValue().slice(0, 7);
}

function selectedMonthValue(value) {
  return /^\d{4}-\d{2}$/.test(String(value || "")) ? String(value) : "all";
}

function formatMonthLabel(value) {
  if (!/^\d{4}-\d{2}$/.test(String(value || ""))) return String(value || "");
  const [year, month] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("en-IN", {
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, 1)));
}

function isValidExpenseRecord(row) {
  const status = String(row?.status || "").trim().toLowerCase();
  const amount = Number(row?.amount);
  return (
    !["cancelled", "canceled", "deleted", "invalid", "rejected"].includes(status) &&
    Number.isFinite(amount) &&
    amount > 0
  );
}

export default function PayoutModal({
  handlerNames = [],
  topPerformers = [],
  ownedSummary,
  initialMonth = "all",
  onClose,
  onChanged,
  // injected from parent so we don't re-import
  apiBase,
  categories,
  categoryLabels,
  formatCurrency,
  formatDate,
}) {
  const ve = apiBase;
  const B0 = categories;
  const Ex = categoryLabels;
  const Jc = formatCurrency;
  const rR = formatDate;

  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filterHandler, setFilterHandler] = useState("all");
  // Keep payout validation in the same accounting period that launched the
  // modal. Without this, a July earnings view silently fetched all-time stats,
  // which excluded July's monthly salary and capped settlement at commission.
  const [filterMonth, setFilterMonth] = useState(() => selectedMonthValue(initialMonth));
  const [historyMonth, setHistoryMonth] = useState(() => {
    const selected = selectedMonthValue(initialMonth);
    return selected === "all" ? currentMonthValue() : selected;
  });
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState(() => ({
    reference: "",
    amount: "",
    category: "commission",
    note: "",
    date: todayInputValue(),
  }));
  const [saving, setSaving] = useState(false);
  // Saving a proof runs Ollama payment verification server-side, so the
  // button alone cannot explain the wait. Null when no AI work is running.
  const [aiState, setAiState] = useState(null);
  const [proofFile, setProofFile] = useState(null);
  const proofInputRef = useRef(null);
  const [previewProof, setPreviewProof] = useState(null);
  const [page, setPage] = useState(0);
  const [showPaymentAccounts, setShowPaymentAccounts] = useState(false);
  const [success, setSuccess] = useState("");
  const [showCommBreakdown, setShowCommBreakdown] = useState(false);
  const [commCandidates, setCommCandidates] = useState(null);
  const [loadingComm, setLoadingComm] = useState(false);
  const [referrerRecords, setReferrerRecords] = useState([]);

  // ── Data fetching ──
  const fetchData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams();
      if (filterMonth !== "all") params.set("month", filterMonth);
      const res = await (await fetch(`${ve}/handler-expenses?${params.toString()}`)).json();
      if (res.status === "ok") {
        setEntries(res.expenses || []);
      } else {
        setError(res.message || "Failed to load");
      }
    } catch (err) {
      setError(err.message || "Network error");
    } finally {
      setLoading(false);
    }
  }, [filterMonth, ve]);

  useEffect(() => { fetchData(); }, [fetchData]);

  useEffect(() => {
    let cancelled = false;
    fetchReferrerRegistryJson(ve, "/referrers")
      .then((payload) => {
        if (!cancelled) {
          setReferrerRecords(payload.referrers || []);
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err.message || "Could not load referrers");
      });
    return () => { cancelled = true; };
  }, [ve]);

  // Escape, the Tab trap and focus restoration all come from the shared
  // hook. Escape is suppressed while an inline row edit is open so it
  // cancels the edit rather than discarding the whole dialog.
  const dialogRef = useDialogA11y(true, onClose, { closeOnEscape: !editId });

  // ── Derived data ──
  const filtered = useMemo(() => {
    if (filterHandler === "all") return [];
    let list = entries;
    const lc = filterHandler.toLowerCase();
    list = list.filter(r => (r.reference || "").toLowerCase() === lc);
    return list;
  }, [entries, filterHandler]);

  // Fetch handler-specific stats when handler or period changes
  const [handlerStats, setHandlerStats] = useState(null);
  const [handlerStatsLoading, setHandlerStatsLoading] = useState(false);
  const [handlerStatsRevision, setHandlerStatsRevision] = useState(0);
  useEffect(() => {
    if (filterHandler === "all") {
      setHandlerStats(null);
      setHandlerStatsLoading(false);
      return;
    }
    let cancelled = false;
    setHandlerStatsLoading(true);
    const params = new URLSearchParams();
    if (filterMonth !== "all") params.set("month", filterMonth);
    params.set("reference", filterHandler);
    fetch(`${ve}/candidates/stats?${params.toString()}`, { credentials: "include" })
      .then(r => r.json())
      .then(res => {
        if (cancelled) return;
        const stats = res.stats || res;
        const perfs = stats.top_performers || [];
        const lc = filterHandler.toLowerCase().trim();
        const perf = perfs.find(p => (p.name || "").toLowerCase().trim() === lc || (p.ref_key || "").toLowerCase().trim() === lc);
        setHandlerStats(perf || null);
      })
      .catch(() => { if (!cancelled) setHandlerStats(null); })
      .finally(() => { if (!cancelled) setHandlerStatsLoading(false); });
    return () => { cancelled = true; };
  }, [filterHandler, filterMonth, ve, handlerStatsRevision]);

  const owed = useMemo(() => {
    // April & May 2026 are fully settled — no balance
    if (filterMonth === "2026-04" || filterMonth === "2026-05") return 0;
    // Use handler-specific stats fetched for this period
    if (filterHandler !== "all") {
      if (!handlerStats) return 0;
      const net = Number(handlerStats.net_payable) || 0;
      return net + filtered.reduce((s, r) => s + (Number(r.amount) || 0), 0);
    }
    // Fallback to parent's ownedSummary for the all-referrers view.
    return Number(ownedSummary?.owed) || 0;
  }, [ownedSummary, filterHandler, filterMonth, handlerStats, filtered]);
  const paidOut = useMemo(() => filtered.reduce((s, r) => s + (Number(r.amount) || 0), 0), [filtered]);
  const balance = owed - paidOut;

  const allHandlers = useMemo(() => {
    const map = new Map();
    handlerNames.forEach(n => map.set(n.toLowerCase(), n));
    entries.forEach(r => { const n = (r.reference || "").trim(); if (n) map.set(n.toLowerCase(), n); });
    return [...map.values()].sort((a, b) => a.localeCompare(b));
  }, [entries, handlerNames]);
  const referrerOptions = useMemo(() => {
    if (referrerRecords.length) {
      return referrerRecords
        .filter((row) => row.is_active !== false)
        .map((row) => ({ id: row.id, name: row.name }))
        .sort((a, b) => a.name.localeCompare(b.name));
    }
    return allHandlers.map((name) => ({ id: name, name }));
  }, [allHandlers, referrerRecords]);
  const selectedReferrerId = filterHandler === "all"
    ? "all"
    : referrerOptions.find(
      (row) => row.name.toLowerCase() === filterHandler.toLowerCase()
    )?.id || filterHandler;

  // Keep handler selection valid after entries reload (case-insensitive match)
  useEffect(() => {
    if (filterHandler === "all") return;
    const lc = filterHandler.toLowerCase();
    const match = allHandlers.find(h => h.toLowerCase() === lc);
    if (match && match !== filterHandler) {
      setFilterHandler(match); // normalize casing to match dropdown option
    }
  }, [allHandlers, filterHandler]);

  // Fetch commission breakdown candidates when expanded
  useEffect(() => {
    if (!showCommBreakdown || filterHandler === "all") { setCommCandidates(null); return; }
    let cancelled = false;
    setLoadingComm(true);
    const params = new URLSearchParams();
    if (filterMonth !== "all") params.set("month", filterMonth);
    params.set("reference", filterHandler);
    fetch(`${ve}/candidates?${params.toString()}`, { credentials: "include" })
      .then(r => r.json())
      .then(res => { if (!cancelled && res.status === "ok") setCommCandidates(res.candidates || []); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoadingComm(false); });
    return () => { cancelled = true; };
  }, [showCommBreakdown, filterHandler, filterMonth, ve]);

  // Reset commission breakdown when handler changes
  useEffect(() => { setShowCommBreakdown(false); setCommCandidates(null); }, [filterHandler]);

  const historyMonthOptions = useMemo(() => {
    const values = new Set([currentMonthValue()]);
    if (historyMonth !== "all") values.add(historyMonth);
    // The month being filed must be selectable even when it has no expenses
    // yet. Without this, filing the first expense of a month is impossible:
    // the month only appears once a row already exists in it.
    const enteredMonth = String(form.date || "").slice(0, 7);
    if (/^\d{4}-\d{2}$/.test(enteredMonth)) values.add(enteredMonth);
    filtered.forEach((row) => {
      const value = String(row.date || "").slice(0, 7);
      if (/^\d{4}-\d{2}$/.test(value)) values.add(value);
    });
    return [
      ...[...values]
        .sort((left, right) => right.localeCompare(left))
        .map((value) => ({ value, label: formatMonthLabel(value) })),
      { value: "all", label: "All months" },
    ];
  }, [filtered, historyMonth, form.date]);

  const historyFiltered = useMemo(() => {
    return filtered.filter((row) => (
      isValidExpenseRecord(row) &&
      (historyMonth === "all" || String(row.date || "").startsWith(historyMonth))
    ));
  }, [filtered, historyMonth]);
  const historyTotal = useMemo(
    () => historyFiltered.reduce((sum, row) => sum + Number(row.amount), 0),
    [historyFiltered],
  );

  // ── Pagination ──
  const totalPages = Math.max(1, Math.ceil(historyFiltered.length / ROWS_PER_PAGE));
  const pagedRows = historyFiltered.slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);
  // Reset page when filters change
  useEffect(() => { setPage(0); }, [filterHandler, filterMonth, historyMonth]);

  // ── Form helpers ──
  function resetForm() {
    setEditId(null);
    setProofFile(null);
    if (proofInputRef.current) proofInputRef.current.value = "";
    setForm({
      reference: filterHandler !== "all" ? filterHandler : "",
      amount: "",
      category: "commission",
      note: "",
      date: todayInputValue(),
    });
  }

  function hasUnsavedExpense() {
    return Boolean(
      editId ||
      String(form.amount || "").trim() ||
      String(form.note || "").trim() ||
      proofFile
    );
  }

  async function handleReferrerChange(value) {
    const selected = referrerOptions.find((row) => row.id === value);
    const nextName = selected?.name || "";
    if (!nextName || nextName === filterHandler) return;

    if (filterHandler !== "all" && hasUnsavedExpense()) {
      const confirmationApi = window.__TA_CONFIRM_VALUE__?.confirm;
      const confirmed = confirmationApi
        ? await confirmationApi({
          title: "Switch referrer?",
          message: "Changing the referrer will clear the unsaved expense details. Continue?",
          confirmLabel: "Switch referrer",
        })
        : window.confirm("Changing the referrer will clear the unsaved expense details. Continue?");
      if (!confirmed) return;
    }

    setFilterHandler(nextName);
    setEditId(null);
    setProofFile(null);
    if (proofInputRef.current) proofInputRef.current.value = "";
    setForm({
      reference: nextName,
      amount: "",
      category: "commission",
      note: "",
      date: todayInputValue(),
    });
    setError("");
    setSuccess("");
  }

  function startEdit(row) {
    setEditId(row.id);
    setFilterHandler(row.reference || "all");
    setForm({
      reference: row.reference || "",
      amount: String(row.amount || ""),
      category: row.category || "other",
      note: row.note || "",
      date: row.date || todayInputValue(),
    });
  }

  async function handleSubmit(ev) {
    ev?.preventDefault?.();
    if (saving) return;
    setSuccess("");
    if (filterHandler === "all") {
      setError("Select one referrer before saving an expense.");
      return;
    }
    const selectedReferrer = referrerOptions.find(
      (row) => row.name.toLowerCase() === filterHandler.toLowerCase(),
    );
    if (!selectedReferrer) {
      setError("Selected referrer is not present in the current registry.");
      return;
    }
    const handlerRef = selectedReferrer.name;
    const amt = Number(form.amount);
    const previousAmount = editId
      ? Number(entries.find((row) => row.id === editId)?.amount) || 0
      : 0;
    if (!Number.isFinite(amt) || amt <= 0) { setError("Amount must be greater than zero"); return; }
    const editableLimit = Math.max(0, balance) + (
      editId ? Number(entries.find((row) => row.id === editId)?.amount) || 0 : 0
    );
    if (amt > editableLimit) {
      setError(`Expense amount cannot exceed the current outstanding amount of ${Jc(editableLimit)}.`);
      return;
    }
    if (!editId && !proofFile) { setError("Payment screenshot is required"); return; }
    const confirmationMessage = `${Jc(amt)} will be deducted from ${handlerRef}’s outstanding amount. Continue?`;
    const confirmationApi = window.__TA_CONFIRM_VALUE__?.confirm;
    const confirmed = confirmationApi
      ? await confirmationApi({
        title: editId ? "Save expense changes?" : "Save expense?",
        message: confirmationMessage,
        confirmLabel: editId ? "Save changes" : "Save expense",
      })
      : window.confirm(confirmationMessage);
    if (!confirmed) return;
    setSaving(true);
    if (proofFile) setAiState("processing");
    setError("");
    try {
      let res;
      if (editId) {
        // Step 1: PATCH text fields as JSON
        res = await (await fetch(`${ve}/handler-expenses/${editId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ reference: handlerRef, amount: amt, category: form.category, note: form.note.trim(), date: form.date }),
        })).json();
        if (res.status !== "ok") { setError(res.message || "Save failed"); return; }
        // Step 2: If new proof file, upload separately
        if (proofFile) {
          const fd = new FormData();
          fd.append("file", proofFile);
          const proofRes = await (await fetch(`${ve}/handler-expenses/${editId}/proofs`, { method: "POST", body: fd })).json();
          if (proofRes.status !== "ok") { setError(proofRes.message || "Proof upload failed, but fields saved"); }
        }
      } else {
        const fd = new FormData();
        fd.append("reference", handlerRef);
        fd.append("amount", String(amt));
        fd.append("category", form.category);
        fd.append("note", form.note.trim());
        fd.append("date", form.date);
        fd.append("file", proofFile);
        res = await (await fetch(`${ve}/handler-expenses`, { method: "POST", body: fd })).json();
      }
      if (res.status !== "ok") { setError(res.message || "Save failed"); return; }
      const deductionDelta = amt - previousAmount;
      setHandlerStats((current) => current
        ? { ...current, net_payable: (Number(current.net_payable) || 0) - deductionDelta }
        : current);
      resetForm();
      await fetchData();
      setHandlerStatsRevision((revision) => revision + 1);
      setSuccess(`Expense added successfully. ${Jc(amt)} was deducted from the amount owed.`);
      onChanged?.();
      if (proofFile) setAiState("success");
    } catch (err) {
      setError(err.message || "Network error");
      if (proofFile) setAiState(/timed out|timeout/i.test(String(err.message || "")) ? "timeout" : "error");
    }
    finally { setSaving(false); }
  }

  async function handleDelete(row) {
    const ok = await window.__TA_CONFIRM_VALUE__?.confirm?.({
      title: 'Delete payout?',
      message: `Remove ₹${row.amount.toLocaleString("en-IN")} payout for ${row.reference}?`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    try {
      const res = await (await fetch(`${ve}/handler-expenses/${row.id}`, { method: "DELETE" })).json();
      if (res.status === "ok") {
        setHandlerStats((current) => current
          ? { ...current, net_payable: (Number(current.net_payable) || 0) + (Number(row.amount) || 0) }
          : current);
        fetchData();
        setHandlerStatsRevision((revision) => revision + 1);
        onChanged?.();
      }
      else setError(res.message || "Delete failed");
    } catch (err) { setError(err.message || "Network error"); }
  }

  function clearFilters() { setFilterHandler("all"); setFilterMonth("all"); }
  const filtersActive = filterHandler !== "all" || filterMonth !== "all";

  const selectedName = filterHandler === "all" ? "" : filterHandler;
  const currentOutstanding = Math.max(0, balance);

  return <Fragment>
    <div className="cand-modal-backdrop" onClick={ev => ev.target === ev.currentTarget && onClose?.()}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="payout-modal-title"
        className="payout-modal payout-modal--expense"
      >
        <header className="payout-modal__header payout-modal__header--expense">
          <div className="payout-modal__heading">
            <h3 className="payout-modal__title" id="payout-modal-title">
              {showPaymentAccounts ? "Manage payment accounts" : "Add Referrer Expense"}
            </h3>
            {!showPaymentAccounts && selectedName && (
              <div className="payout-modal__summary">
                <span>Referrer: <strong>{selectedName}</strong></span>
                <span>Currently owed: <strong>{handlerStatsLoading ? "Loading…" : Jc(currentOutstanding)}</strong></span>
              </div>
            )}
          </div>
          <button
            type="button"
            className="cand-btn cand-btn--ghost cand-btn--xs payout-modal__accounts-action"
            onClick={() => setShowPaymentAccounts((value) => !value)}
            disabled={filterHandler === "all"}
            title={filterHandler === "all" ? "Select a referrer first" : undefined}
          >
            {showPaymentAccounts ? "Back to expense" : "Manage payment accounts"}
          </button>
          <button type="button" className="cand-modal-close" onClick={onClose} aria-label="Close">×</button>
        </header>

        {showPaymentAccounts ? (
          <div className="payout-modal__accounts-view">
            <ReferrerPaymentAccounts apiBase={ve} referrerName={filterHandler} />
          </div>
        ) : (
          <>
            <form className="payout-modal__form-section" onSubmit={handleSubmit}>
              <div className="payout-modal__expense-row payout-modal__expense-row--primary">
                <label className="payout-modal__field">
                  <span className="cand-field-label">Referrer *</span>
                  <select
                    className="cand-input payout-modal__input"
                    value={selectedReferrerId}
                    onChange={ev => handleReferrerChange(ev.target.value)}
                    required
                  >
                    <option value="all" disabled>Select referrer</option>
                    {referrerOptions.map(row => <option value={row.id} key={row.id}>{row.name}</option>)}
                  </select>
                </label>
                <label className="payout-modal__field">
                  <span className="cand-field-label">Expense amount (₹) *</span>
                  <input
                    className="cand-input payout-modal__input"
                    type="number"
                    min="1"
                    step="1"
                    max={editId
                      ? currentOutstanding + (Number(entries.find((row) => row.id === editId)?.amount) || 0)
                      : currentOutstanding || undefined}
                    value={form.amount}
                    onChange={ev => setForm(current => ({ ...current, amount: ev.target.value }))}
                    placeholder="5000"
                    disabled={filterHandler === "all"}
                    required
                  />
                </label>
                <label className="payout-modal__field">
                  <span className="cand-field-label">Expense date *</span>
                  <input
                    className="cand-input payout-modal__input"
                    type="date"
                    value={form.date}
                    onChange={ev => setForm(current => ({ ...current, date: ev.target.value }))}
                    disabled={filterHandler === "all"}
                    required
                  />
                </label>
                <label className="payout-modal__field">
                  <span className="cand-field-label">History month</span>
                  <select
                    className="cand-input payout-modal__input"
                    value={historyMonth}
                    onChange={(event) => setHistoryMonth(event.target.value)}
                    aria-label="Filter expense history by month"
                    disabled={filterHandler === "all"}
                  >
                    {historyMonthOptions.map((option) => (
                      <option value={option.value} key={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
              </div>

              <div className="payout-modal__expense-row payout-modal__expense-row--secondary">
                <label className="payout-modal__field payout-modal__note-field">
                  <span className="cand-field-label">Note / reason</span>
                  <input
                    className="cand-input payout-modal__input"
                    value={form.note}
                    onChange={ev => setForm(current => ({ ...current, note: ev.target.value }))}
                    placeholder="e.g. Interview expense, travel expense, refund or adjustment"
                    disabled={filterHandler === "all"}
                  />
                </label>
                <div
                  className={`cand-payout-attach${proofFile ? " cand-payout-attach--done" : ""}`}
                  onClick={() => {
                    if (filterHandler !== "all") proofInputRef.current?.click();
                  }}
                  onKeyDown={ev => {
                    if (
                      filterHandler !== "all" &&
                      (ev.key === "Enter" || ev.key === " ")
                    ) proofInputRef.current?.click();
                  }}
                  role="button"
                  tabIndex={filterHandler === "all" ? -1 : 0}
                  aria-disabled={filterHandler === "all"}
                  title={proofFile ? "Screenshot attached — click to replace" : editId ? "Attach new screenshot (optional)" : "Attach expense screenshot (required)"}
                >
                  <input
                    ref={proofInputRef}
                    type="file"
                    accept="image/*"
                    disabled={filterHandler === "all"}
                    onChange={ev => {
                      const file = ev.target.files?.[0];
                      if (!file) return;
                      if (!/^image\//.test(file.type || "")) { setError("Only image files allowed"); return; }
                      if (file.size > 8 * 1024 * 1024) { setError("File too large (max 8 MB)"); return; }
                      setProofFile(file);
                      setError("");
                    }}
                    hidden
                  />
                  <span className="cand-payout-attach-icon">{proofFile ? "✓" : "📷"}</span>
                  <span className="cand-payout-attach-text">
                    {/* Described, never named: a phone's file name is a hash or a
                        timestamp, and says nothing about the payment. */}
                    {proofFile ? "Screenshot attached" : editId ? "Replace screenshot" : "Attach screenshot *"}
                  </span>
                </div>
                {editId && <button type="button" className="cand-btn cand-btn--ghost" onClick={resetForm}>Cancel edit</button>}
                <button
                  type="submit"
                  className="cand-btn cand-btn--primary payout-modal__save"
                  disabled={saving || handlerStatsLoading || filterHandler === "all" || (!editId && !proofFile)}
                >
                  {saving ? "Saving…" : editId ? "Save changes" : "Save expense"}
                </button>
                {aiState && (
                  <AiProcessingStatus
                    variant="inline"
                    state={aiState}
                    title="Verifying screenshot"
                    onRetry={
                      aiState === "error" || aiState === "timeout"
                        ? () => { setAiState(null); setError(""); }
                        : undefined
                    }
                    onCancel={
                      aiState === "error" || aiState === "timeout"
                        ? () => setAiState(null)
                        : undefined
                    }
                  />
                )}
              </div>

              {error && <div className="cand-modal-error payout-modal__error" role="alert">{error}</div>}
              {success && <div className="payout-modal__success" role="status">{success}</div>}
            </form>

            <section className="payout-modal__history" aria-labelledby="recent-expenses-title">
              <div className="payout-modal__history-head">
                <h4 id="recent-expenses-title">Recent expense history</h4>
                <div className="payout-modal__history-summary">
                  <span>{historyFiltered.length} entr{historyFiltered.length === 1 ? "y" : "ies"}</span>
                  <strong>Total expenses: {Jc(historyTotal)}</strong>
                </div>
              </div>
              <div className="payout-modal__table-area">
                {filterHandler === "all" ? (
                  <div className="cand-exp-empty">Select a referrer to view recent expenses.</div>
                ) : loading ? <div className="cand-exp-empty">Loading…</div> : historyFiltered.length === 0 ? (
                  <div className="cand-exp-empty">
                    {historyMonth === "all"
                      ? "No expenses found."
                      : `No expenses found for ${formatMonthLabel(historyMonth)}.`}
                  </div>
                ) : (
                  <table className="payout-modal__table">
                    <thead><tr>
                      <th className="payout-col--amount">Amount</th>
                      <th className="payout-col--date">Date</th>
                      <th className="payout-col--note">Note</th>
                      <th className="payout-col--proof">Proof</th>
                      <th className="payout-col--actions">Actions</th>
                    </tr></thead>
                    <tbody>
                      {pagedRows.map(row => (
                        <tr className={`payout-modal__row${editId === row.id ? " payout-modal__row--editing" : ""}`} key={row.id}>
                          <td className="payout-col--amount payout-col--amount-positive">{Jc(row.amount)}</td>
                          <td className="payout-col--date">{rR(row.date)}</td>
                          <td className="payout-col--note">{row.note || <em>—</em>}</td>
                          <td className="payout-col--proof">
                            {(row.proofs?.length > 0)
                              ? <button type="button" className="cand-link" onClick={() => setPreviewProof(row.proofs[0])}>View proof</button>
                              : <em>—</em>}
                          </td>
                          <td className="payout-col--actions">
                            <button type="button" className="cand-btn cand-btn--ghost cand-btn--xs" onClick={() => startEdit(row)} title="Edit">✎</button>
                            <button type="button" className="cand-btn cand-btn--ghost cand-btn--xs cand-btn--danger-ghost" onClick={() => handleDelete(row)} title="Delete">🗑</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              {totalPages > 1 && (
                <div className="payout-modal__pagination">
                  <button type="button" className="cand-btn cand-btn--ghost cand-btn--xs" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Previous</button>
                  <span className="payout-modal__page-info">Page {page + 1} of {totalPages}</span>
                  <button type="button" className="cand-btn cand-btn--ghost cand-btn--xs" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next →</button>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </div>

    {/* Lightbox for proof preview */}
    {previewProof && <div className="cand-proof-lightbox" onClick={() => setPreviewProof(null)}>
      <div className="cand-proof-lightbox-inner" onClick={ev => ev.stopPropagation()}>
        <button type="button" className="cand-proof-lightbox-close" onClick={() => setPreviewProof(null)} aria-label="Close preview">×</button>
        <img src={`${ve}${previewProof.url}`} alt={previewProof.note || "Payment proof"} className="cand-proof-lightbox-img" />
        {previewProof.note && <p className="cand-proof-lightbox-note">{previewProof.note}</p>}
        <p className="cand-proof-lightbox-meta">Payment proof{previewProof.uploaded_at && <span> · {new Date(previewProof.uploaded_at).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</span>}</p>
      </div>
    </div>}
  </Fragment>;
}
