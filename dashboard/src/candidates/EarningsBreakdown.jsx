import { useState, useMemo, useCallback, useEffect, Fragment } from "react";
import "./EarningsBreakdown.css";
import { normalizePaymentProofs } from "./paymentProofs.js";
import { Icon } from "../components/ui/Icon.jsx";

/** "2026-07" → "Jul 2026". Returns "" for anything that is not a real month. */
function monthLabel(value) {
  const match = /^(\d{4})-(\d{2})$/.exec(String(value || "").trim());
  if (!match) return "";
  const date = new Date(Number(match[1]), Number(match[2]) - 1, 1);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("en-IN", { month: "short", year: "numeric" });
}

/**
 * Name the months a carried-forward balance came from: one month as-is, a run
 * as "Apr – Jun 2026". Gaps are not spelled out — the tooltip carries the
 * amounts, and a span reads better than a list of six months.
 */
function monthSpanLabel(months) {
  const valid = (months || []).filter(m => /^\d{4}-\d{2}$/.test(String(m || "")));
  if (valid.length === 0) return "";
  const sorted = [...valid].sort();
  const first = monthLabel(sorted[0]);
  const last = monthLabel(sorted[sorted.length - 1]);
  if (!first) return "";
  return first === last ? first : `${first} – ${last}`;
}

/**
 * Earnings Breakdown — replaces "Top Performers" tab.
 * Shows per-handler earnings detail with expandable per-candidate rows.
 */
export default function EarningsBreakdown({
  stats,
  allStats = null,
  month,
  // Read-only here: used to render the month's friendly name, not to change it.
  monthOptions,
  onAddExpense,
  handlerView = false,
  handlerName = null,
  formatCurrency,
  apiBase = "",
  onViewPaymentProofs,
}) {
  const fmt = formatCurrency || (v => {
    const n = Number(v) || 0;
    return n === 0 ? "₹0" : n < 100000 ? `₹${n.toLocaleString("en-IN")}` : `₹${(n / 100000).toFixed(n % 100000 === 0 ? 0 : 1)}L`;
  });

  const performers = useMemo(() => {
    const src = (stats?.top_performers || []);
    if (handlerView && handlerName) {
      const lc = handlerName.trim().toLowerCase();
      return src.filter(p => (p.name || "").trim().toLowerCase() === lc);
    }
    return src;
  }, [stats, handlerView, handlerName]);

  const [expanded, setExpanded] = useState(null);
  const [sortBy, setSortBy] = useState("net_payable");
  const [handlerCandidates, setHandlerCandidates] = useState({});
  const [loadingCandidates, setLoadingCandidates] = useState(null);

  const candidateCacheKey = useCallback(
    (name, selectedMonth = month) => `${selectedMonth || "all"}::${name}`,
    [month],
  );

  // Fetch candidates for the expanded handler and selected month. The
  // month-scoped key prevents records from a previous month being reused.
  const loadHandlerCandidates = useCallback(async (name, selectedMonth = month) => {
    const cacheKey = `${selectedMonth || "all"}::${name}`;
    setLoadingCandidates(cacheKey);
    try {
      const params = new URLSearchParams();
      if (selectedMonth && selectedMonth !== "all") params.set("month", selectedMonth);
      params.set("reference", name);
      const res = await (await fetch(`${apiBase}/candidates?${params.toString()}`, { credentials: "include" })).json();
      if (res.status === "ok") {
        setHandlerCandidates(prev => ({ ...prev, [cacheKey]: res.candidates || [] }));
      }
    } catch (e) { /* silent */ }
    finally {
      setLoadingCandidates(current => current === cacheKey ? null : current);
    }
  }, [apiBase, month]);

  // Fetch candidates for a specific handler when expanded.
  function toggleExpand(name) {
    if (expanded === name) { setExpanded(null); return; }
    setExpanded(name);
  }

  // Re-fetch the open handler whenever the selected month changes. Parent
  // totals come from `stats`; candidate detail rows use this matching request.
  useEffect(() => {
    if (expanded) loadHandlerCandidates(expanded, month);
  }, [expanded, month, loadHandlerCandidates]);

  const sorted = useMemo(() => {
    const list = [...performers];
    list.sort((a, b) => {
      const av = Math.abs(Number(a[sortBy]) || 0);
      const bv = Math.abs(Number(b[sortBy]) || 0);
      if (bv !== av) return bv - av;
      return (Number(b.revenue_total) || 0) - (Number(a.revenue_total) || 0);
    });
    return list;
  }, [performers, sortBy]);

  // Totals
  const totals = useMemo(() => {
    let commission = 0, salary = 0, owed = 0, paid = 0, opening = 0, net = 0;
    for (const p of performers) {
      commission += Number(p.commission_total ?? p.auto_earnings_total) || 0;
      salary += Number(p.salary_total) || 0;
      owed += Number(p.auto_earnings_total) || 0;
      paid += Number(p.paid_out_total) || 0;
      opening += Number(p.prior_balance) || 0;
      // Sum the per-handler closing balances rather than recomputing from the
      // column totals. `owed - paid` silently dropped every opening balance and
      // every recovery, so the footer disagreed with the column above it.
      net += Number(p.net_payable) || 0;
    }
    return { commission, salary, owed, paid, opening, net };
  }, [performers]);

  // "Owe" never said who owed whom. The closing balance is money the company
  // still has to hand over, so the label states the direction outright.
  function getStatus(net) {
    if (net > 0) return { label: "To pay", cls: "earn-status--owe" };
    if (net < 0) return { label: "Overpaid", cls: "earn-status--over" };
    return { label: "Settled", cls: "earn-status--settled" };
  }

  /** Plain-English outcome for one handler's closing balance. */
  function settlementSentence(name, net) {
    const who = name || "this handler";
    if (net > 0) return `Company needs to pay ${who} ${fmt(net)}.`;
    if (net < 0) return `${who} has been paid ${fmt(Math.abs(net))} more than earned.`;
    return `${who} is fully settled — nothing to pay.`;
  }

  const CLOSING_BALANCE_FORMULA =
    "Opening balance + earnings + salary − paid out";

  const scopeLabel = useMemo(() => {
    if (!month || month === "all") return null;
    const opt = (monthOptions || []).find(m => m.value === month);
    return opt ? opt.label.replace(" · this month", "") : month;
  }, [month, monthOptions]);

  // A balance computed while one of its sources was unreadable still adds up,
  // so nothing on screen would otherwise look wrong. Say it plainly instead.
  const unreconciledSources = useMemo(() => {
    const found = new Set();
    for (const p of performers) {
      for (const s of p.unreconciled_sources || []) found.add(s);
    }
    return [...found];
  }, [performers]);
  const SOURCE_LABELS = {
    handler_expenses: "handler payouts",
    handler_salaries: "handler salaries",
    payment_verification_ledger: "payment ledger",
  };
  const unreconciledNotice = unreconciledSources.length ? (
    <p className="earn-unreconciled" role="alert">
      <strong>Balances are unreconciled.</strong>{" "}
      These figures were calculated without{" "}
      {unreconciledSources.map(s => SOURCE_LABELS[s] || s).join(", ")}, so amounts
      shown as payable are incomplete and must not be paid against.
    </p>
  ) : null;

  if (!performers.length) {
    return (
      <section className="earn-section">
        <header className="earn-header">
          <div className="earn-header-left">
            <h3 className="earn-title">Earnings breakdown</h3>
            {scopeLabel && <span className="earn-scope">{scopeLabel}</span>}
          </div>
          {onAddExpense && (
            <button
              type="button"
              className="cand-btn cand-btn--primary cand-btn--sm earn-add-expense"
              onClick={onAddExpense}
            >
              Add expense
            </button>
          )}
        </header>
        {unreconciledNotice}
        <p className="earn-empty">No handler data for this period.</p>
      </section>
    );
  }

  return (
    <section className="earn-section">
      {/* Header */}
      <header className="earn-header">
        <div className="earn-header-left">
          <h3 className="earn-title">Earnings breakdown</h3>
          {scopeLabel && <span className="earn-scope">{scopeLabel}</span>}
          <p className="earn-sub">
            {performers.length} handler{performers.length !== 1 ? "s" : ""} ·
            Current payable <strong className="earn-green">{fmt(totals.owed)}</strong> ·
            Paid out <strong className="earn-red">{fmt(totals.paid)}</strong> ·
            Closing balance <strong className={totals.net > 0 ? "earn-green" : totals.net < 0 ? "earn-red" : "earn-settled"}>{totals.net > 0 ? "To pay " : totals.net < 0 ? "Overpaid " : ""}{fmt(Math.abs(totals.net))}</strong>
          </p>
        </div>
        <div className="earn-header-right">
          {/* The month is chosen once, in the page's filter bar. A second
              selector here drove the same state from the same options, so two
              controls appeared to filter independently when they never did.
              `scopeLabel` above still names the month this table is showing. */}
          <label className="earn-filter">
            <span className="earn-filter-label">Sort by</span>
            <select className="cand-input cand-input--compact" value={sortBy} onChange={ev => setSortBy(ev.target.value)}>
              <option value="net_payable">Balance owed</option>
              <option value="auto_earnings_total">Total owed</option>
              <option value="revenue_completed">Revenue</option>
              <option value="commission_total">Commission</option>
              <option value="count">Lead count</option>
            </select>
          </label>
          {onAddExpense && (
            <button
              type="button"
              className="cand-btn cand-btn--primary cand-btn--sm earn-add-expense"
              onClick={onAddExpense}
            >
              Add expense
            </button>
          )}
        </div>
      </header>

      {unreconciledNotice}

      {/* Table */}
      <div className="earn-table-wrap">
        <table className="earn-table">
          <thead>
            <tr>
              <th className="earn-th--name">Handler</th>
              <th className="earn-th--num">Leads</th>
              <th className="earn-th--num">Done</th>
              <th className="earn-th--money">Revenue</th>
              <th className="earn-th--money">Earnings</th>
              <th className="earn-th--money">Salary</th>
              <th className="earn-th--money">Current payable</th>
              <th className="earn-th--money">Paid out</th>
              <th className="earn-th--money" title={CLOSING_BALANCE_FORMULA}>
                Closing balance
                <span className="earn-info" aria-hidden="true">i</span>
              </th>
              <th className="earn-th--status">Status</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(p => {
              const commission = Number(p.commission_total ?? p.auto_earnings_total) || 0;
              const complimentary = Number(p.complimentary_total) || 0;
              const adminComplimentary = Number(p.admin_complimentary_total) || 0;
              const adminComplimentaryCount = Number(p.admin_complimentary_count) || 0;
              const salary = Number(p.salary_total) || 0;
              const owed = Number(p.auto_earnings_total) || 0;
              const recoveries = Number(p.recoveries_total) || 0;
              const paid = Number(p.paid_out_total) || 0;
              const net = Number(p.net_payable) || 0;
              const priorBalance = Number(p.prior_balance) || 0;
              const status = getStatus(net);
              const isExpanded = expanded === p.name;
              const cacheKey = candidateCacheKey(p.name);
              const currentCandidates = handlerCandidates[cacheKey];
              const isLoadingCandidates = loadingCandidates === cacheKey;
              const showOpeningBalance = priorBalance !== 0 && month && month !== "all";
              const signedCurrency = value => {
                const numeric = Number(value) || 0;
                if (numeric > 0) return `+${fmt(numeric)}`;
                if (numeric < 0) return `−${fmt(Math.abs(numeric))}`;
                return fmt(0);
              };
              // Why the opening balance exists. A bare figure invites the
              // question "where did this come from?", so name the months it
              // came from and show the earned/paid/recovered split behind it.
              const priorOwed = Number(p.prior_owed) || 0;
              const priorPaid = Number(p.prior_paid) || 0;
              const priorRecoveries = Number(p.prior_recoveries) || 0;
              // Running subtotals, derived from the figures already on this row.
              // `owed` is what the Current payable column shows, so "Total earned"
              // and that column can never disagree.
              const totalEarned = owed;
              const grossPayable = priorBalance + totalEarned;
              const priorMonths = Array.isArray(p.prior_months) ? p.prior_months : [];
              const priorComplimentary = Number(p.prior_complimentary) || 0;
              const priorComplimentaryCount = Number(p.prior_complimentary_count) || 0;
              const priorSpan = monthSpanLabel(priorMonths);
              const whenSuffix = priorSpan ? ` from ${priorSpan}` : " from earlier months";
              // Profile-closure complimentary is granted on a candidate closing,
              // sometimes on another handler's candidate, so "unpaid commission"
              // is the wrong story for it. Name it whenever it is what is owed.
              const balanceIsComplimentary =
                priorBalance > 0 && priorComplimentary > 0 && priorBalance <= priorComplimentary;
              const openingReason = balanceIsComplimentary
                ? `unpaid profile-closure complimentary${whenSuffix}`
                : priorBalance > 0
                  ? `unpaid${whenSuffix}`
                  : `overpaid${priorSpan ? ` in ${priorSpan}` : " in earlier months"}`;
              // A referrer's complimentary arrives alongside their commission, so
              // it is never the whole balance and used to be named only in a
              // tooltip — which read as though the bonus had gone to the closure
              // admin alone. Say it on the row whenever there is one, unless the
              // line above already says the balance is nothing else.
              const complimentaryNote =
                priorComplimentary > 0 && !balanceIsComplimentary
                  ? `Includes ${fmt(priorComplimentary)} profile-closure complimentary`
                    + (priorComplimentaryCount > 1 ? ` (${priorComplimentaryCount} closures)` : "")
                  : null;
              const openingDetail = [
                `Earned ${fmt(priorOwed)}`,
                priorComplimentary > 0
                  ? `incl. ${fmt(priorComplimentary)} profile-closure complimentary`
                    + (priorComplimentaryCount > 1 ? ` (${priorComplimentaryCount} closures)` : "")
                  : null,
                `paid ${fmt(priorPaid)}`,
                priorRecoveries > 0 ? `recovered ${fmt(priorRecoveries)}` : null,
              ]
                .filter(Boolean)
                .join(" · ")
                + (priorSpan ? ` before ${monthLabel(month)}` : "");

              return (
                <Fragment key={p.ref_key || p.name}>
                  <tr
                    className={`earn-row${isExpanded ? " earn-row--open" : ""}`}
                    aria-expanded={isExpanded}
                    onClick={() => toggleExpand(p.name)}
                  >
                    <td className="earn-td--name">
                      {/* One chevron, turned when open, in a small hit target. */}
                      <span className="earn-expand-icon" aria-hidden="true">▸</span>
                      <strong>{p.name}</strong>
                    </td>
                    <td className="earn-td--num">{p.count || 0}</td>
                    <td className="earn-td--num earn-green">{p.completed || 0}</td>
                    <td className="earn-td--money">{fmt(p.revenue_total || 0)}</td>
                    <td className="earn-td--money earn-green">
                      {fmt(commission)}
                      {complimentary > 0 && (
                        <span className="earn-carry-fwd earn-carry-fwd--comp" title="Included completed-profile complimentary amounts">
                          incl. {fmt(complimentary)} complimentary
                        </span>
                      )}
                    </td>
                    <td className="earn-td--money earn-blue">{salary > 0 ? fmt(salary) : "—"}</td>
                    <td className="earn-td--money"><strong>{fmt(owed)}</strong></td>
                    <td className="earn-td--money earn-red">{paid > 0 ? fmt(paid) : "₹0"}</td>
                    <td className={`earn-td--money ${net > 0 ? "earn-green" : net < 0 ? "earn-red" : "earn-settled"}`}>
                      <strong title={CLOSING_BALANCE_FORMULA}>{net > 0 ? "+" : ""}{fmt(net)}</strong>
                      {/* "c/f" was an abbreviation nobody had to know. Spell the
                          opening balance out under the closing figure instead. */}
                      {/* Amount only. The reason belongs to the expanded
                          calculation, where there is room to read it. */}
                      {showOpeningBalance && (
                        <span className="earn-carry-fwd">
                          Opening balance: {signedCurrency(priorBalance)}
                        </span>
                      )}
                    </td>
                    <td className="earn-td--status">
                      <span className={`earn-status ${status.cls}`}>{status.label}</span>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr className="earn-detail-row">
                      <td colSpan={10}>
                        <div className="earn-detail">
                          {isLoadingCandidates && <p className="earn-detail-loading">Loading…</p>}
                          {currentCandidates && (() => {
                            const rows = currentCandidates.filter(c => Number(c.payment) > 0);
                            const pct = (p.commission_pct || 50) / 100;
                            return (
                              <ul className="earn-breakdown-list candidate-list">
                                {rows.length === 0 && (
                                  <li className="earn-breakdown-item earn-breakdown-empty">
                                    No candidate payments received in this period.
                                  </li>
                                )}
                                {rows.map(c => {
                                  const received = Number(c.payment) || 0;
                                  const referral = Number(c.handler_commission) || Math.round(received * pct);
                                  const date = c.logged_date || c.date || "";
                                  const dateStr = date ? new Date(date).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "";
                                  const proofs = normalizePaymentProofs(c);
                                  const reportedProofCount = Number(c.proof_count) || 0;
                                  const availableProofCount = proofs.length || reportedProofCount;
                                  return (
                                    <li className="earn-breakdown-item" key={c.id}>
                                      <span className="earn-breakdown-desc">
                                        {c.name} · {fmt(received)} received – {fmt(referral)} referral
                                        {dateStr && <span className="earn-breakdown-date"> · {dateStr}</span>}
                                        {availableProofCount > 0 && onViewPaymentProofs && (
                                          <button
                                            type="button"
                                            className="earn-breakdown-proof-btn"
                                            onClick={(event) => {
                                              event.preventDefault();
                                              event.stopPropagation();
                                              onViewPaymentProofs({
                                                ...c,
                                                id: c.id || c.candidate_id || c.candidateId,
                                                name: c.name || c.candidate_name || "Candidate",
                                                payment_proofs: proofs,
                                              });
                                            }}
                                            title={
                                              availableProofCount === 1
                                                ? "View payment proof"
                                                : `View ${availableProofCount} payment proofs`
                                            }
                                            aria-label={`View payment proofs for ${c.name || c.candidate_name || "candidate"}`}
                                          >
                                            <Icon name="camera" size={13} />
                                          </button>
                                        )}
                                      </span>
                                      <strong className="earn-breakdown-amount">{fmt(referral)}</strong>
                                    </li>
                                  );
                                })}
                                {adminComplimentary > 0 && (
                                  <li className="earn-breakdown-item">
                                    <span className="earn-breakdown-desc">
                                      Admin complimentary · {adminComplimentaryCount} completed profile{adminComplimentaryCount === 1 ? "" : "s"}
                                    </span>
                                    <strong className="earn-breakdown-amount">{fmt(adminComplimentary)}</strong>
                                  </li>
                                )}
                                <li className="earn-breakdown-item earn-breakdown-total candidate-total">
                                  <span className="earn-breakdown-total-title">
                                    <strong>Total ({Number(p.count) || rows.length} candidates)</strong>
                                  </span>
                                  <span className="earn-breakdown-total-earnings">
                                    Referral earnings <strong>{fmt(commission)}</strong>
                                  </span>
                                </li>
                              </ul>
                            );
                          })()}
                          {/* Full width, in normal flow, immediately after the Total
                              row. Each term of the sum is one item, so the closing
                              balance can be checked across the band rather than
                              inferred from figures scattered around the row. */}
                          <div
                            className="earn-ledger earnings-calculation-summary"
                            role="table"
                            aria-label={`Payment calculation for ${p.name}`}
                          >
                            <div className="earn-ledger-rows">
                              <div className="earn-ledger-row" role="row">
                                <span className="earn-ledger-label" role="rowheader">Opening balance</span>
                                <span className="earn-ledger-value" role="cell">{signedCurrency(priorBalance)}</span>
                                {showOpeningBalance && (
                                  <span
                                    className={`earn-ledger-note${balanceIsComplimentary ? " earn-summary-note--complimentary" : ""}`}
                                    title={openingDetail}
                                  >
                                    {openingReason}
                                  </span>
                                )}
                                {showOpeningBalance && complimentaryNote && (
                                  <span
                                    className="earn-ledger-note earn-summary-note--complimentary"
                                    title={openingDetail}
                                  >
                                    {complimentaryNote}
                                  </span>
                                )}
                              </div>
                              <div className="earn-ledger-row" role="row">
                                <span className="earn-ledger-label" role="rowheader">Referral earnings</span>
                                <span className="earn-ledger-value earn-green" role="cell">{fmt(commission)}</span>
                              </div>
                              <div className="earn-ledger-row" role="row">
                                <span className="earn-ledger-label" role="rowheader">Salary</span>
                                <span className="earn-ledger-value" role="cell">{fmt(salary)}</span>
                              </div>
                              {/* Two running subtotals. Both are derived here from
                                  figures already on the row — nothing new is fetched
                                  or computed server-side. "Total earned" is the same
                                  number as the Current payable column above. */}
                              <div className="earn-ledger-row earn-ledger-row--subtotal" role="row">
                                <span className="earn-ledger-label" role="rowheader" title="Referral earnings + salary">
                                  Total earned
                                </span>
                                <span className="earn-ledger-value" role="cell">{fmt(totalEarned)}</span>
                              </div>
                              <div className="earn-ledger-row earn-ledger-row--subtotal" role="row">
                                <span className="earn-ledger-label" role="rowheader" title="Opening balance + total earned">
                                  Gross payable
                                </span>
                                <span className="earn-ledger-value" role="cell">{signedCurrency(grossPayable)}</span>
                              </div>
                              <div className="earn-ledger-row" role="row">
                                <span className="earn-ledger-label" role="rowheader">Paid out</span>
                                <span className="earn-ledger-value earn-red" role="cell">
                                  {paid > 0 ? `−${fmt(paid)}` : fmt(0)}
                                </span>
                              </div>
                              {/* Only shown when non-zero, but never omitted when it
                                  exists — otherwise the band would not add up. */}
                              {recoveries > 0 && (
                                <div className="earn-ledger-row" role="row">
                                  <span className="earn-ledger-label" role="rowheader">Recoveries</span>
                                  <span className="earn-ledger-value earn-red" role="cell">−{fmt(recoveries)}</span>
                                </div>
                              )}
                              <div className="earn-ledger-row earn-ledger-row--total" role="row">
                                <span className="earn-ledger-label" role="rowheader" title={CLOSING_BALANCE_FORMULA}>
                                  Closing balance
                                  <span className="earn-info" aria-hidden="true">i</span>
                                </span>
                                <span
                                  className={`earn-ledger-value ${net > 0 ? "earn-green" : net < 0 ? "earn-red" : "earn-settled"}`}
                                  role="cell"
                                >
                                  {signedCurrency(net)}
                                  <span className={`earn-status ${status.cls}`}>{status.label}</span>
                                </span>
                              </div>
                            </div>
                            <p className="earn-ledger-outcome">{settlementSentence(p.name, net)}</p>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
          <tfoot>
            <tr className="earn-foot">
              <td className="earn-td--name"><strong>Totals</strong></td>
              <td className="earn-td--num">{performers.reduce((s, p) => s + (p.count || 0), 0)}</td>
              <td className="earn-td--num">{performers.reduce((s, p) => s + (p.completed || 0), 0)}</td>
              <td className="earn-td--money">{fmt(performers.reduce((s, p) => s + (Number(p.revenue_total) || 0), 0))}</td>
              <td className="earn-td--money">{fmt(totals.commission)}</td>
              <td className="earn-td--money">{fmt(totals.salary)}</td>
              <td className="earn-td--money"><strong>{fmt(totals.owed)}</strong></td>
              <td className="earn-td--money">{fmt(totals.paid)}</td>
              <td className={`earn-td--money ${totals.net > 0 ? "earn-green" : totals.net < 0 ? "earn-red" : "earn-settled"}`}>
                <strong title={CLOSING_BALANCE_FORMULA}>{fmt(totals.net)}</strong>
                {totals.opening !== 0 && month && month !== "all" && (
                  <span className="earn-carry-fwd">
                    Opening balance: {totals.opening > 0 ? "+" : "−"}{fmt(Math.abs(totals.opening))}
                  </span>
                )}
              </td>
              <td></td>
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  );
}
