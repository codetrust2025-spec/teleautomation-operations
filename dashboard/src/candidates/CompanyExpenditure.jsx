import { useState, useEffect, useMemo, useCallback } from "react";
import "./CompanyExpenditure.css";
import { CandidateSelectedBadge } from "./SelectedBadge.jsx";

const CATEGORIES = [
  { value: "rent", label: "Rent / space" },
  { value: "tools", label: "Tools / equipment" },
  { value: "marketing", label: "Marketing / ads" },
  { value: "salary_staff", label: "Staff salary" },
  { value: "travel", label: "Travel / fuel" },
  { value: "internet", label: "Internet / telecom" },
  { value: "office", label: "Office supplies" },
  { value: "subscription", label: "Subscriptions / SaaS" },
  { value: "other", label: "Other" },
];

const ROWS_PER_PAGE = 8;

function fmt(v) {
  const n = Number(v) || 0;
  if (n === 0) return "₹0";
  return `₹${n.toLocaleString("en-IN")}`;
}

function fmtDate(d) {
  if (!d) return "—";
  try { return new Date(d).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }); }
  catch { return d; }
}

export default function CompanyExpenditure({ onClose, apiBase = "" }) {
  const [expenses, setExpenses] = useState([]);
  const [months, setMonths] = useState([]);
  const [totals, setTotals] = useState(null);
  const [statsData, setStatsData] = useState(null);
  const [handlerExpenses, setHandlerExpenses] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filterMonth, setFilterMonth] = useState("all");
  const [filterCat, setFilterCat] = useState("all");
  const [editId, setEditId] = useState(null);
  const [form, setForm] = useState({
    title: "", amount: "", category: "other", note: "",
    date: new Date().toISOString().slice(0, 10),
  });
  const [saving, setSaving] = useState(false);
  const [page, setPage] = useState(0);
  // Tabs: revenue | expenditure | profit | handler | company
  const [activeTab, setActiveTab] = useState("revenue");

  // Expanded handler detail in revenue/handler views
  const [expandedHandler, setExpandedHandler] = useState(null);
  const [handlerCandidates, setHandlerCandidates] = useState({});
  const [loadingHandler, setLoadingHandler] = useState(null);

  async function toggleHandler(name) {
    if (expandedHandler === name) { setExpandedHandler(null); return; }
    setExpandedHandler(name);
    if (handlerCandidates[name]) return;
    setLoadingHandler(name);
    try {
      const params = new URLSearchParams();
      if (filterMonth !== "all") params.set("month", filterMonth);
      params.set("reference", name);
      const res = await (await fetch(`${apiBase}/candidates?${params.toString()}`, { credentials: "include" })).json();
      if (res.status === "ok") {
        setHandlerCandidates(prev => ({ ...prev, [name]: res.candidates || [] }));
      }
    } catch {} finally { setLoadingHandler(null); }
  }

  // Reset expanded when month changes
  useEffect(() => { setExpandedHandler(null); setHandlerCandidates({}); }, [filterMonth]);

  // Fetch all data
  const fetchData = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const params = new URLSearchParams();
      if (filterMonth !== "all") params.set("month", filterMonth);
      const [expRes, totalRes, statsRes, handlerRes] = await Promise.all([
        fetch(`${apiBase}/company-expenses?${params.toString()}`, { credentials: "include" }).then(r => r.json()),
        fetch(`${apiBase}/company-expenses/total?${params.toString()}`, { credentials: "include" }).then(r => r.json()),
        fetch(`${apiBase}/candidates/stats?${filterMonth !== "all" ? `month=${filterMonth}` : ""}`, { credentials: "include" }).then(r => r.json()).catch(() => null),
        fetch(`${apiBase}/handler-expenses?${filterMonth !== "all" ? `month=${filterMonth}` : ""}`, { credentials: "include" }).then(r => r.json()).catch(() => null),
      ]);
      if (expRes.status === "ok") {
        setExpenses(expRes.expenses || []);
        setMonths(expRes.available_months || []);
      }
      if (totalRes.status === "ok") {
        const revenue = Number(statsRes?.stats?.revenue_total ?? statsRes?.revenue_total) || 0;
        const companyRevenue = Number(statsRes?.stats?.company_revenue_total ?? statsRes?.company_revenue_total) || 0;
        setTotals({ ...totalRes, revenue, company_revenue: companyRevenue });
      }
      setStatsData(statsRes?.stats || statsRes || null);
      setHandlerExpenses(handlerRes?.expenses || []);
    } catch (e) { setError(e.message || "Failed to load"); }
    finally { setLoading(false); }
  }, [filterMonth, apiBase]);

  useEffect(() => { fetchData(); }, [fetchData]);
  useEffect(() => { const h = ev => { if (ev.key === "Escape") onClose?.(); }; document.addEventListener("keydown", h); return () => document.removeEventListener("keydown", h); }, [onClose]);

  // Filtered company expenses
  const filtered = useMemo(() => {
    let list = expenses;
    if (filterCat !== "all") list = list.filter(r => r.category === filterCat);
    return list;
  }, [expenses, filterCat]);
  const totalPages = Math.max(1, Math.ceil(filtered.length / ROWS_PER_PAGE));
  const pagedRows = filtered.slice(page * ROWS_PER_PAGE, (page + 1) * ROWS_PER_PAGE);
  useEffect(() => { setPage(0); }, [filterMonth, filterCat]);

  const monthOptions = useMemo(() => [
    { value: "all", label: "All time" },
    ...months.map(m => ({ value: m.value, label: m.label })),
  ], [months]);

  // Revenue breakdown from stats (top_performers)
  const revenueBreakdown = useMemo(() => {
    if (!statsData) return [];
    const performers = statsData.top_performers || [];
    return performers
      .filter(p => (Number(p.revenue_total) || 0) > 0)
      .map(p => ({ name: p.name, amount: Number(p.revenue_total) || 0, count: p.count || 0, completed: p.completed || 0, selected: p.selected || 0 }))
      .sort((a, b) => b.amount - a.amount);
  }, [statsData]);

  // Handler payouts breakdown
  const handlerBreakdown = useMemo(() => {
    if (!handlerExpenses.length) return [];
    const byHandler = {};
    for (const exp of handlerExpenses) {
      const ref = (exp.reference || "").trim();
      if (!ref) continue;
      const key = ref.toLowerCase();
      if (!byHandler[key]) byHandler[key] = { name: ref, total: 0, count: 0, items: [] };
      byHandler[key].total += Number(exp.amount) || 0;
      byHandler[key].count += 1;
      byHandler[key].items.push(exp);
    }
    return Object.values(byHandler).sort((a, b) => b.total - a.total);
  }, [handlerExpenses]);

  // Form handlers
  function resetForm() { setEditId(null); setForm({ title: "", amount: "", category: "other", note: "", date: new Date().toISOString().slice(0, 10) }); }
  function startEdit(row) { setEditId(row.id); setForm({ title: row.title || "", amount: String(row.amount || ""), category: row.category || "other", note: row.note || "", date: row.date || new Date().toISOString().slice(0, 10) }); }

  async function handleSubmit(ev) {
    ev?.preventDefault?.();
    if (!form.title.trim()) { setError("Title is required"); return; }
    const amt = Number(form.amount);
    if (!Number.isFinite(amt) || amt <= 0) { setError("Amount must be > 0"); return; }
    setSaving(true); setError("");
    try {
      const payload = { title: form.title.trim(), amount: amt, category: form.category, note: form.note.trim(), date: form.date };
      let res;
      if (editId) { res = await (await fetch(`${apiBase}/company-expenses/${editId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), credentials: "include" })).json(); }
      else { res = await (await fetch(`${apiBase}/company-expenses`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload), credentials: "include" })).json(); }
      if (res.status !== "ok") { setError(res.message || "Save failed"); return; }
      resetForm(); fetchData();
    } catch (e) { setError(e.message || "Network error"); }
    finally { setSaving(false); }
  }

  async function handleDelete(row) {
    const ok = await window.__TA_CONFIRM_VALUE__?.confirm?.({
      title: 'Delete expense?',
      message: `Delete "${row.title}" (${fmt(row.amount)})?`,
      confirmLabel: 'Delete',
      variant: 'danger',
    });
    if (!ok) return;
    try { const res = await (await fetch(`${apiBase}/company-expenses/${row.id}`, { method: "DELETE", credentials: "include" })).json(); if (res.status === "ok") fetchData(); else setError(res.message || "Delete failed"); }
    catch (e) { setError(e.message || "Network error"); }
  }

  return (
    <div className="cand-modal-backdrop" onClick={ev => ev.target === ev.currentTarget && onClose?.()}>
      <div className="compexp-modal">
        {/* Header */}
        <header className="compexp-header">
          <div className="compexp-header-left">
            <h3 className="compexp-title">Total Expenditure</h3>
            <p className="compexp-sub">Click any card to see its breakdown</p>
          </div>
          <select className="cand-input cand-input--compact" value={filterMonth} onChange={ev => setFilterMonth(ev.target.value)}>
            {monthOptions.map(m => <option value={m.value} key={m.value}>{m.label}</option>)}
          </select>
          <button type="button" className="cand-modal-close" onClick={onClose} aria-label="Close">×</button>
        </header>

        {/* Clickable summary cards */}
        {totals && (
          <div className="compexp-summary">
            <button type="button" className={`compexp-card compexp-card--revenue${activeTab === "revenue" ? " compexp-card--selected" : ""}`} onClick={() => setActiveTab("revenue")}>
              <span className="compexp-card-label">Revenue</span>
              <span className="compexp-card-value">{fmt(totals.revenue)}</span>
            </button>
            <button type="button" className={`compexp-card compexp-card--grand${activeTab === "expenditure" ? " compexp-card--selected" : ""}`} onClick={() => setActiveTab("expenditure")}>
              <span className="compexp-card-label">Total Spent</span>
              <span className="compexp-card-value">{fmt(totals.grand_total)}</span>
            </button>
            <button type="button" className={`compexp-card compexp-card--profit${activeTab === "profit" ? " compexp-card--selected" : ""}`} onClick={() => setActiveTab("profit")}>
              <span className="compexp-card-label">Net Profit</span>
              <span className="compexp-card-value">{fmt((totals.company_revenue || 0) - (totals.company_expenses?.total || 0))}</span>
            </button>
            <button type="button" className={`compexp-card compexp-card--handler${activeTab === "handler" ? " compexp-card--selected" : ""}`} onClick={() => setActiveTab("handler")}>
              <span className="compexp-card-label">Handler Payouts</span>
              <span className="compexp-card-value">{fmt(totals.handler_payouts?.total)}</span>
              <span className="compexp-card-count">{totals.handler_payouts?.count || 0} entries</span>
            </button>
            <button type="button" className={`compexp-card compexp-card--company${activeTab === "company" ? " compexp-card--selected" : ""}`} onClick={() => setActiveTab("company")}>
              <span className="compexp-card-label">Company Ops</span>
              <span className="compexp-card-value">{fmt(totals.company_expenses?.total)}</span>
              <span className="compexp-card-count">{totals.company_expenses?.count || 0} entries</span>
            </button>
          </div>
        )}

        {/* Breakdown content area */}
        <div className="compexp-content">
          {/* Revenue breakdown */}
          {activeTab === "revenue" && (
            <div className="compexp-breakdown-section">
              <h4 className="compexp-section-title">Revenue breakdown by handler</h4>
              {revenueBreakdown.length === 0 ? <p className="compexp-empty">No revenue data.</p> : (
                <div className="compexp-list">
                  {revenueBreakdown.map(item => (
                    <div key={item.name}>
                      <div className={`compexp-list-row compexp-list-row--clickable${expandedHandler === item.name ? " compexp-list-row--open" : ""}`} onClick={() => toggleHandler(item.name)}>
                        <span className="compexp-list-name"><span className="compexp-expand-icon">{expandedHandler === item.name ? "▾" : "▸"}</span> {item.name}</span>
                        <span className="compexp-list-meta">{item.count} leads · {item.completed} done{item.selected > 0 && <> · <span className="compexp-selected">★ {item.selected} selected</span></>}</span>
                        <strong className="compexp-list-amount compexp-list-amount--revenue">{fmt(item.amount)}</strong>
                      </div>
                      {expandedHandler === item.name && (
                        <div className="compexp-sublist">
                          {loadingHandler === item.name && <p className="compexp-sublist-loading">Loading…</p>}
                          {handlerCandidates[item.name] && (() => {
                            const rows = handlerCandidates[item.name].filter(c => Number(c.payment) > 0);
                            if (!rows.length) return <p className="compexp-sublist-loading">No payments recorded.</p>;
                            return rows.map(c => (
                              <div className="compexp-sublist-row" key={c.id}>
                                <span className="compexp-sublist-name">{c.name}<CandidateSelectedBadge row={c} /></span>
                                <span className="compexp-sublist-date">{fmtDate(c.logged_date || c.date)}</span>
                                <strong className="compexp-sublist-amount">{fmt(c.payment)}</strong>
                              </div>
                            ));
                          })()}
                        </div>
                      )}
                    </div>
                  ))}
                  <div className="compexp-list-row compexp-list-row--total">
                    <span className="compexp-list-name"><strong>Total</strong></span>
                    <span className="compexp-list-meta">{revenueBreakdown.reduce((s, i) => s + i.count, 0)} leads</span>
                    <strong className="compexp-list-amount">{fmt(totals?.revenue)}</strong>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Expenditure breakdown (handler + company combined) */}
          {activeTab === "expenditure" && (
            <div className="compexp-breakdown-section">
              <h4 className="compexp-section-title">Expenditure split</h4>
              <div className="compexp-list">
                <div className="compexp-list-row">
                  <span className="compexp-list-name">Handler payouts (commission + salary)</span>
                  <span className="compexp-list-meta">{totals?.handler_payouts?.count || 0} entries</span>
                  <strong className="compexp-list-amount compexp-list-amount--expense">{fmt(totals?.handler_payouts?.total)}</strong>
                </div>
                <div className="compexp-list-row">
                  <span className="compexp-list-name">Company operational expenses</span>
                  <span className="compexp-list-meta">{totals?.company_expenses?.count || 0} entries</span>
                  <strong className="compexp-list-amount compexp-list-amount--expense">{fmt(totals?.company_expenses?.total)}</strong>
                </div>
                <div className="compexp-list-row compexp-list-row--total">
                  <span className="compexp-list-name"><strong>Grand Total</strong></span>
                  <span className="compexp-list-meta"></span>
                  <strong className="compexp-list-amount">{fmt(totals?.grand_total)}</strong>
                </div>
              </div>
              {totals?.grand_total > 0 && (
                <div className="compexp-split-bars" style={{ marginTop: 16 }}>
                  <div className="compexp-split-item">
                    <span className="compexp-split-label">Handlers</span>
                    <div className="compexp-split-track"><div className="compexp-split-fill compexp-split-fill--handler" style={{ width: `${((totals.handler_payouts?.total || 0) / totals.grand_total) * 100}%` }} /></div>
                    <span className="compexp-split-value">{Math.round(((totals.handler_payouts?.total || 0) / totals.grand_total) * 100)}%</span>
                  </div>
                  <div className="compexp-split-item">
                    <span className="compexp-split-label">Company</span>
                    <div className="compexp-split-track"><div className="compexp-split-fill compexp-split-fill--company" style={{ width: `${((totals.company_expenses?.total || 0) / totals.grand_total) * 100}%` }} /></div>
                    <span className="compexp-split-value">{Math.round(((totals.company_expenses?.total || 0) / totals.grand_total) * 100)}%</span>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Net Profit breakdown */}
          {activeTab === "profit" && (
            <div className="compexp-breakdown-section">
              <h4 className="compexp-section-title">Profit calculation</h4>
              <div className="compexp-list">
                <div className="compexp-list-row">
                  <span className="compexp-list-name">Total client collections</span>
                  <span className="compexp-list-meta"></span>
                  <strong className="compexp-list-amount compexp-list-amount--revenue">{fmt(totals?.revenue)}</strong>
                </div>
                <div className="compexp-list-row">
                  <span className="compexp-list-name">− Handler earnings (commission + complimentary)</span>
                  <span className="compexp-list-meta"></span>
                  <strong className="compexp-list-amount compexp-list-amount--expense">−{fmt((totals?.revenue || 0) - (totals?.company_revenue || 0))}</strong>
                </div>
                <div className="compexp-list-row">
                  <span className="compexp-list-name">= Company revenue</span>
                  <span className="compexp-list-meta"></span>
                  <strong className="compexp-list-amount">{fmt(totals?.company_revenue)}</strong>
                </div>
                <div className="compexp-list-row">
                  <span className="compexp-list-name">− Company operational costs</span>
                  <span className="compexp-list-meta">{totals?.company_expenses?.count || 0} entries</span>
                  <strong className="compexp-list-amount compexp-list-amount--expense">−{fmt(totals?.company_expenses?.total)}</strong>
                </div>
                <div className="compexp-list-row compexp-list-row--total">
                  <span className="compexp-list-name"><strong>Net Profit</strong></span>
                  <span className="compexp-list-meta"></span>
                  <strong className="compexp-list-amount compexp-list-amount--profit">{fmt((totals?.company_revenue || 0) - (totals?.company_expenses?.total || 0))}</strong>
                </div>
              </div>
            </div>
          )}

          {/* Handler payouts breakdown */}
          {activeTab === "handler" && (
            <div className="compexp-breakdown-section">
              <h4 className="compexp-section-title">Handler payouts breakdown</h4>
              {handlerBreakdown.length === 0 ? <p className="compexp-empty">No handler payouts logged.</p> : (
                <div className="compexp-list">
                  {handlerBreakdown.map(h => (
                    <div key={h.name}>
                      <div className={`compexp-list-row compexp-list-row--clickable${expandedHandler === h.name ? " compexp-list-row--open" : ""}`} onClick={() => setExpandedHandler(expandedHandler === h.name ? null : h.name)}>
                        <span className="compexp-list-name"><span className="compexp-expand-icon">{expandedHandler === h.name ? "▾" : "▸"}</span> {h.name}</span>
                        <span className="compexp-list-meta">{h.count} payout{h.count !== 1 ? "s" : ""}</span>
                        <strong className="compexp-list-amount compexp-list-amount--expense">{fmt(h.total)}</strong>
                      </div>
                      {expandedHandler === h.name && (
                        <div className="compexp-sublist">
                          {h.items.map(exp => (
                            <div className="compexp-sublist-row" key={exp.id}>
                              <span className="compexp-sublist-name">{exp.note || exp.category || "Payout"}</span>
                              <span className="compexp-sublist-date">{fmtDate(exp.date)}</span>
                              <strong className="compexp-sublist-amount compexp-sublist-amount--expense">{fmt(exp.amount)}</strong>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                  <div className="compexp-list-row compexp-list-row--total">
                    <span className="compexp-list-name"><strong>Total</strong></span>
                    <span className="compexp-list-meta">{handlerBreakdown.reduce((s, h) => s + h.count, 0)} payouts</span>
                    <strong className="compexp-list-amount">{fmt(totals?.handler_payouts?.total)}</strong>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Company expenses tab (with add form) */}
          {activeTab === "company" && (
            <div className="compexp-breakdown-section">
              <h4 className="compexp-section-title">Company operational expenses</h4>
              {/* Add/edit form */}
              <form className="compexp-form" onSubmit={handleSubmit}>
                <div className="compexp-form-row">
                  <label className="compexp-field compexp-field--title"><span className="cand-field-label">Title *</span><input className="cand-input" value={form.title} onChange={ev => setForm(f => ({ ...f, title: ev.target.value }))} placeholder="e.g. Office rent July" required /></label>
                  <label className="compexp-field compexp-field--amount"><span className="cand-field-label">Amount (₹) *</span><input className="cand-input" type="number" min="0" step="100" value={form.amount} onChange={ev => setForm(f => ({ ...f, amount: ev.target.value }))} placeholder="5000" required /></label>
                  <label className="compexp-field compexp-field--cat"><span className="cand-field-label">Category</span><select className="cand-input" value={form.category} onChange={ev => setForm(f => ({ ...f, category: ev.target.value }))}>{CATEGORIES.map(c => <option value={c.value} key={c.value}>{c.label}</option>)}</select></label>
                  <label className="compexp-field compexp-field--date"><span className="cand-field-label">Date</span><input className="cand-input" type="date" value={form.date} onChange={ev => setForm(f => ({ ...f, date: ev.target.value }))} /></label>
                </div>
                <div className="compexp-form-row2">
                  <label className="compexp-field compexp-field--note"><span className="cand-field-label">Note</span><input className="cand-input" value={form.note} onChange={ev => setForm(f => ({ ...f, note: ev.target.value }))} placeholder="Optional description" /></label>
                  <div className="compexp-form-actions">{editId && <button type="button" className="cand-btn cand-btn--ghost" onClick={resetForm}>Cancel</button>}<button type="submit" className="cand-btn cand-btn--primary" disabled={saving}>{saving ? "Saving…" : editId ? "Update" : "+ Add"}</button></div>
                </div>
              </form>
              {error && <div className="cand-modal-error">{error}</div>}
              {/* Filter + table */}
              <div className="compexp-filter-row">
                <select className="cand-input cand-input--compact" value={filterCat} onChange={ev => setFilterCat(ev.target.value)}><option value="all">All categories</option>{CATEGORIES.map(c => <option value={c.value} key={c.value}>{c.label}</option>)}</select>
                <span className="compexp-count">{filtered.length} expense{filtered.length !== 1 ? "s" : ""} · {fmt(filtered.reduce((s, r) => s + (Number(r.amount) || 0), 0))}</span>
              </div>
              {loading ? <p className="compexp-empty">Loading…</p> : filtered.length === 0 ? (
                <p className="compexp-empty">No company expenses logged{filterMonth !== "all" ? " for this period" : ""}.</p>
              ) : (
                <div className="compexp-table-wrap"><table className="compexp-table"><thead><tr><th>Title</th><th>Amount</th><th>Category</th><th>Date</th><th>Note</th><th>Actions</th></tr></thead><tbody>
                  {pagedRows.map(row => (<tr className={editId === row.id ? "compexp-row--editing" : ""} key={row.id}><td className="compexp-td--title">{row.title || "—"}</td><td className="compexp-td--amount">{fmt(row.amount)}</td><td><span className={`compexp-cat compexp-cat--${row.category}`}>{CATEGORIES.find(c => c.value === row.category)?.label || row.category}</span></td><td className="compexp-td--date">{fmtDate(row.date)}</td><td className="compexp-td--note">{row.note || "—"}</td><td className="compexp-td--actions"><button type="button" className="cand-btn cand-btn--ghost cand-btn--xs" onClick={() => startEdit(row)} title="Edit">✎</button><button type="button" className="cand-btn cand-btn--ghost cand-btn--xs cand-btn--danger-ghost" onClick={() => handleDelete(row)} title="Delete">🗑</button></td></tr>))}
                </tbody></table></div>
              )}
              {totalPages > 1 && (<div className="compexp-pagination"><button type="button" className="cand-btn cand-btn--ghost cand-btn--xs" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Prev</button><span>Page {page + 1} of {totalPages}</span><button type="button" className="cand-btn cand-btn--ghost cand-btn--xs" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next →</button></div>)}
            </div>
          )}
        </div>

        <footer className="compexp-footer"><button type="button" className="cand-btn cand-btn--ghost" onClick={onClose}>Close</button></footer>
      </div>
    </div>
  );
}
