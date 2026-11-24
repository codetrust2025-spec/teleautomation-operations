// Candidates page toolbar (owner, 24 Nov): three controls at most.
// Search, one Filters button that opens a popup holding every filter (type,
// referrer, month, stage, pending only) with Apply and Reset and a count of
// the filters in use, and Sort. The page's actions sit on their own line.
import React, { useEffect, useRef, useState } from "react";
import { Icon } from "../components/ui/Icon.jsx";

export const SORT_OPTIONS = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "name", label: "Name A–Z" },
  { value: "balance", label: "Balance due" },
  { value: "paid", label: "Amount paid" },
];

const rowDate = (row) => String(row.logged_date || row.date || "");
const num = (value) => Number(value) || 0;

// The table's order. Newest first is the order the page always had.
export function sortCandidates(rows, sort = "newest") {
  const list = (rows || []).slice();
  const newest = (a, b) => rowDate(b).localeCompare(rowDate(a));
  const byName = (a, b) => String(a.name || "").localeCompare(String(b.name || ""), "en", { sensitivity: "base" });
  if (sort === "oldest") return list.sort((a, b) => rowDate(a).localeCompare(rowDate(b)) || byName(a, b));
  if (sort === "name") return list.sort((a, b) => byName(a, b) || newest(a, b));
  if (sort === "balance") return list.sort((a, b) => num(b.balance_due) - num(a.balance_due) || newest(a, b));
  if (sort === "paid") return list.sort((a, b) => num(b.payment) - num(a.payment) || newest(a, b));
  return list.sort(newest);
}

// How many filters differ from the page's defaults. The referrer counts only
// where it can be changed (an admin's view; a handler is held to their own).
export function countActiveFilters(filters, defaults, { showReference = true } = {}) {
  let count = 0;
  if (filters.service !== defaults.service) count += 1;
  if (showReference && filters.reference !== defaults.reference) count += 1;
  if (filters.month !== defaults.month) count += 1;
  if (filters.stage !== defaults.stage) count += 1;
  if (Boolean(filters.pending) !== Boolean(defaults.pending)) count += 1;
  return count;
}

const SERVICE_OPTIONS = [
  { value: "all", label: "All types" },
  { value: "profile_service", label: "Profile" },
  { value: "round_wise", label: "Round-wise" },
];

export function CandidatesFilterBar({
  search,
  onSearch,
  filters,
  defaults,
  onApply,
  sort,
  onSort,
  monthOptions,
  stageOptions,
  referenceOptions,
  showReference,
  pendingCount = 0,
  summary,
  children,
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(filters);
  const wrapRef = useRef(null);
  const firstRef = useRef(null);
  const buttonRef = useRef(null);
  const active = countActiveFilters(filters, defaults, { showReference });
  const draftActive = countActiveFilters(draft, defaults, { showReference });

  const openPopup = () => {
    setDraft(filters);
    setOpen(true);
  };
  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) buttonRef.current?.focus();
  };
  const apply = () => {
    onApply(draft);
    close();
  };
  const reset = () => {
    setDraft(defaults);
    onApply(defaults);
  };

  useEffect(() => {
    if (!open) return undefined;
    firstRef.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") close();
    };
    const onDown = (event) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target)) close(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown, { passive: true });
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
    };
  }, [open]);

  const set = (key) => (event) => {
    const value = event.target.type === "checkbox" ? event.target.checked : event.target.value;
    setDraft((current) => ({ ...current, [key]: value }));
  };
  const field = (key, label, aria, options, ref) => (
    <label className="cand-filter-field">
      <span className="cand-filter-field__label">{label}</span>
      <select
        ref={ref}
        className={`cand-input${draft[key] !== defaults[key] ? " cand-input--active" : ""}`}
        value={draft[key]}
        onChange={set(key)}
        aria-label={aria}
      >
        {options.map((option) => (
          <option value={option.value} key={option.value}>{option.label}</option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="cand-bar" role="region" aria-label="Candidate filters">
      <div className="cand-bar__controls" ref={wrapRef}>
        <label className="cand-bar__search">
          <Icon name="search" size={16} />
          <input
            className="cand-input cand-input--search"
            type="search"
            placeholder="Search name, tech, reference, phone…"
            aria-label="Search candidates"
            value={search}
            onChange={(event) => onSearch(event.target.value)}
          />
        </label>
        <button
          ref={buttonRef}
          type="button"
          className={`cand-bar__filters${active ? " cand-bar__filters--active" : ""}`}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-label={active ? `Filters, ${active} in use` : "Filters"}
          onClick={() => (open ? close() : openPopup())}
        >
          <Icon name="filter" size={16} />
          <span>Filters</span>
          {active > 0 && <span className="cand-bar__count" aria-hidden="true">{active}</span>}
        </button>
        <label className="cand-bar__sort">
          <Icon name="sort" size={16} />
          <select
            className="cand-input"
            value={sort}
            onChange={(event) => onSort(event.target.value)}
            aria-label="Sort candidates"
          >
            {SORT_OPTIONS.map((option) => (
              <option value={option.value} key={option.value}>{option.label}</option>
            ))}
          </select>
        </label>
        {open && (
          <>
            <div className="cand-filter-backdrop" aria-hidden="true" onClick={() => close(false)} />
            <div className="cand-filter-pop" role="dialog" aria-label="Filters">
              <div className="cand-filter-pop__head">
                <strong>Filters</strong>
                {draftActive > 0 && <span className="cand-bar__count">{draftActive}</span>}
                <button type="button" className="cand-filter-pop__close" aria-label="Close filters" onClick={() => close()}>
                  <Icon name="x" size={18} />
                </button>
              </div>
              <div className="cand-filter-pop__body">
                {field("service", "Type", "Filter by service type", SERVICE_OPTIONS, firstRef)}
                {showReference && field("reference", "Referrer", "Filter by handler / reference", referenceOptions)}
                {field("month", "Month", "Filter by month", monthOptions)}
                {field("stage", "Stage", "Filter by stage", stageOptions)}
                <label className={`cand-filter-check${draft.pending ? " cand-filter-check--on" : ""}`}>
                  <input type="checkbox" checked={Boolean(draft.pending)} onChange={set("pending")} />
                  <span>Pending payments only</span>
                  {pendingCount > 0 && <span className="cand-toggle-badge">{pendingCount}</span>}
                </label>
              </div>
              <div className="cand-filter-pop__foot">
                <button type="button" className="cand-btn cand-btn--ghost" onClick={reset}>Reset</button>
                <button type="button" className="cand-btn cand-btn--primary" onClick={apply}>
                  {draftActive ? `Apply (${draftActive})` : "Apply"}
                </button>
              </div>
            </div>
          </>
        )}
      </div>
      {children && <div className="cand-bar__actions">{children}</div>}
      {summary && <p className="cand-bar__summary" aria-live="polite">{summary}</p>}
    </div>
  );
}
