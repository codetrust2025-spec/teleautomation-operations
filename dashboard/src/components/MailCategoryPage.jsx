import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API } from "../config.js";
import { acknowledgeSelectionAlarm } from "../notifications/selectionAlarm.js";
import { SelectionAlarmControls } from "../notifications/SelectionAlarmBanner.jsx";
import { InterviewBellControls } from "../notifications/InterviewBellControls.jsx";
import { fetchMailSummary, publishMailCategoryUnread, publishMailUnread } from "../notifications/mailUnread.js";
import { OverlayLoader } from "../Loader.jsx";
import "./MailCategoryPage.css";
import {
  CLASSIFICATION_GROUPS, MAIL_CATEGORY_PAGES, NotificationDetail, displayLabel, mailAlertCategory,
  mailStatusTone, request, useMailLive,
} from "./MailMonitoringNotifications.jsx";

/**
 * Selection Related and Interview Related, as the approved mockup draws them:
 * three counter tiles that are the tabs, search, company and status filters,
 * then one card per Candidate + Company. A card shows that group's whole mail
 * count, unread and read, and its latest verified status; expanded, it lists
 * every mail of the group newest first -- Selection and Interview alike, each
 * labelled -- unread ones in blue, read ones muted. Opening a mail marks that
 * one mail read and nothing else.
 *
 * Counts and rows come from the server (`/api/mail-monitoring/groups`), so the
 * page shows production data, never mockup numbers.
 */

const PAGE_SIZE = 20;
// The mockup's tiles: Unread, AI Retry, All Alerts. Same filters as the tabs.
export const CATEGORY_TILES = [
  { id: "unread", label: "Unread", icon: "mail", params: { is_read: "false" } },
  { id: "retry", label: "AI Retry", icon: "sparkles", params: { priority: "retry_pending" } },
  { id: "all", label: "All Alerts", icon: "layers", params: {} },
];

const SVG = {
  mail: <><rect x="3.5" y="5.5" width="17" height="13" rx="2" /><path d="m4 7 8 6 8-6" /></>,
  sparkles: <><path d="M12 4.5 13.6 9l4.4 1.5-4.4 1.6L12 16.5l-1.6-4.4L6 10.5 10.4 9z" /><path d="M18.5 4v3M17 5.5h3" /></>,
  layers: <><path d="m12 4 8.5 4.5L12 13 3.5 8.5z" /><path d="m3.5 12.5 8.5 4.5 8.5-4.5" /><path d="m3.5 16.5 8.5 4.5 8.5-4.5" /></>,
  info: <><circle cx="12" cy="12" r="8.5" /><path d="M12 11v5M12 8h.01" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="m16 16 4 4" /></>,
  building: <><path d="M5 20.5V4.5h9v16M14 9.5h5v11M3.5 20.5h17" /><path d="M8 8h.01M11 8h.01M8 11.5h.01M11 11.5h.01M8 15h.01M11 15h.01" /></>,
  tag: <><path d="M3.5 12.2V4.5h7.7l9 9-7.7 7.7z" /><circle cx="8" cy="9" r="1.2" /></>,
  chevron: <path d="m6.5 9.5 5.5 5.5 5.5-5.5" />,
  eye: <><path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></>,
  dots: <><circle cx="12" cy="5.5" r="1.3" /><circle cx="12" cy="12" r="1.3" /><circle cx="12" cy="18.5" r="1.3" /></>,
  check: <><circle cx="12" cy="12" r="8.5" /><path d="m8.5 12.2 2.4 2.4 4.6-4.9" /></>,
};
function Glyph({ name, size = 20 }) {
  return <svg className={`mcp-glyph mcp-glyph--${name}`} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{SVG[name]}</svg>;
}

const IST = "Asia/Kolkata";
const dayKey = (date) => new Intl.DateTimeFormat("en-CA", { timeZone: IST }).format(date);
/** "Today, 09:58 AM", "Yesterday, 06:20 PM", "Mon, 20 Jan, 11:15 AM" (IST). */
export function mailWhen(value, now = new Date()) {
  const date = value ? new Date(value) : null;
  if (!date || Number.isNaN(date.getTime())) return "—";
  const time = new Intl.DateTimeFormat("en-US", { timeZone: IST, hour: "2-digit", minute: "2-digit", hour12: true }).format(date);
  const today = dayKey(now);
  const yesterday = dayKey(new Date(now.getTime() - 86400000));
  const key = dayKey(date);
  if (key === today) return `Today, ${time}`;
  if (key === yesterday) return `Yesterday, ${time}`;
  const part = (options) => new Intl.DateTimeFormat("en-GB", { timeZone: IST, ...options }).format(date);
  const day = `${part({ weekday: "short" })}, ${part({ day: "numeric" })} ${part({ month: "short" })}`;
  const year = new Intl.DateTimeFormat("en-GB", { timeZone: IST, year: "numeric" }).format(date);
  const thisYear = new Intl.DateTimeFormat("en-GB", { timeZone: IST, year: "numeric" }).format(now);
  return `${day}${year === thisYear ? "" : ` ${year}`}, ${time}`;
}

const CATEGORY_NAME = { selection: "Selection", interview: "Interview" };
// The status the alert's own mail states (server: verified_status). A "Joining
// Confirmed" whose mail never says so shows the step it does say.
const statusText = (item) => displayLabel(item.verified_status || item.candidate_status || item.classification);
const unproven = (item) => item.status_evidence === "joining not stated" || item.latest_status_evidence === "joining not stated";
const groupKey = (group) => `${group.candidate_id}::${group.company_key || ""}`;
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value !== null && value !== undefined) search.set(key, String(value));
  }
  return search.toString();
}

function GroupCard({ group, expanded, onToggle, history, onOpen, onMarkAllRead }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const close = (event) => { if (menuRef.current && !menuRef.current.contains(event.target)) setMenuOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [menuOpen]);
  const company = group.company_name || "Company not identified";
  const status = group.latest_status || displayLabel(group.latest_classification || "");
  const tone = mailStatusTone({ candidate_status: group.latest_status, classification: group.latest_classification });
  const listId = `mcp-history-${groupKey(group).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const latest = history?.items?.[0];
  return <article className={`mcp-group${expanded ? " mcp-group--open" : ""}`} aria-label={`${group.candidate_name || "Candidate"}, ${company}`}>
    <div className="mcp-group__head">
      <div className="mcp-logo" aria-hidden="true"><span>{company}</span></div>
      <div className="mcp-group__who">
        <strong className="mcp-group__name">{group.candidate_name || "Candidate"}</strong>
        {group.candidate_email && <span className="mcp-group__email">{group.candidate_email}</span>}
        <span className="mcp-group__company">{company}</span>
      </div>
      <div className="mcp-group__badges">
        <span className="mcp-pill mcp-pill--count">{plural(Number(group.total) || 0, "Email")}</span>
        {status && <span className={`mcp-pill mcp-pill--status mcp-pill--${tone}`} title={unproven(group)
          ? "Latest verified status (detected as Joining Confirmed; the email does not state joining)"
          : "Latest verified status"}><Glyph name="check" size={16} />{status}</span>}
      </div>
      <button type="button" className={`mcp-chevron${expanded ? " mcp-chevron--open" : ""}`} aria-expanded={expanded} aria-controls={listId}
        aria-label={`${expanded ? "Hide" : "Show"} emails for ${group.candidate_name || "candidate"} at ${company}`} onClick={onToggle}>
        <Glyph name="chevron" size={22} />
      </button>
    </div>
    <div className="mcp-group__bar">
      <span className="mcp-group__counts"><span>Unread: <b>{group.unread}</b></span><span className="mcp-sep" aria-hidden="true" /><span>Opened: <b>{group.read}</b></span></span>
      <button type="button" className="mcp-view" onClick={() => (latest ? onOpen(latest) : onToggle(true))}><Glyph name="eye" size={18} />View</button>
      <div className="mcp-menu" ref={menuRef}>
        <button type="button" className="mcp-kebab" aria-label="More actions" aria-expanded={menuOpen} aria-haspopup="menu" onClick={() => setMenuOpen((open) => !open)}><Glyph name="dots" size={18} /></button>
        {menuOpen && <div className="mcp-menu__list" role="menu">
          <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); onToggle(!expanded); }}>{expanded ? "Hide emails" : "Show emails"}</button>
          <button type="button" role="menuitem" disabled={!Number(group.unread)} onClick={() => { setMenuOpen(false); onMarkAllRead(); }}>Mark all as read</button>
        </div>}
      </div>
    </div>
    {expanded && <ol className="mcp-history" id={listId} aria-label={`Emails, newest first`}>
      {history?.loading && <li className="mcp-history__note">Loading emails…</li>}
      {history?.error && <li className="mcp-history__note" role="alert">{history.error}</li>}
      {(history?.items || []).map((item) => {
        const kind = mailAlertCategory(item);
        return <li key={item.id} className={`mcp-mail ${item.is_read ? "mcp-mail--read" : "mcp-mail--unread"}`}>
          <span className="mcp-mail__dot" aria-hidden="true" />
          <button type="button" className="mcp-mail__row" onClick={() => onOpen(item)}
            aria-label={`${item.is_read ? "Opened" : "Unread"}: ${item.email_subject || "no subject"}`}>
            <span className="mcp-mail__icon"><Glyph name="mail" size={20} /></span>
            <span className="mcp-mail__text">
              <span className="mcp-mail__subject">{item.email_subject || "(no subject)"}</span>
              <span className={`mcp-mail__label${kind ? ` mcp-mail__label--${kind}` : ""}`}
                title={unproven(item) ? "Detected as Joining Confirmed, but this email does not state joining" : undefined}>{kind ? `${CATEGORY_NAME[kind]} · ` : ""}{statusText(item)}</span>
            </span>
            <span className={`mcp-state mcp-state--${item.is_read ? "read" : "unread"}`}>{item.is_read ? "Opened" : "Unread"}</span>
            <span className="mcp-mail__when">{mailWhen(item.email_received_at || item.created_at)}</span>
          </button>
        </li>;
      })}
      {history && !history.loading && !history.error && !(history.items || []).length && <li className="mcp-history__note">No emails in this group.</li>}
    </ol>}
  </article>;
}

export function MailCategoryPage({ category }) {
  const page = MAIL_CATEGORY_PAGES[category];
  const [tile, setTile] = useState("all");
  const [search, setSearch] = useState("");
  const [companyKey, setCompanyKey] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState({ groups: [], total: 0, companies: [] });
  const [counts, setCounts] = useState({});
  const [emailCount, setEmailCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [expanded, setExpanded] = useState(() => new Set());
  const [histories, setHistories] = useState({});
  const [selected, setSelected] = useState(null);
  const [downloading, setDownloading] = useState(null);
  const version = useRef(0);
  const statuses = useMemo(
    () => (CLASSIFICATION_GROUPS.find((group) => group.value === category)?.classifications || []),
    [category],
  );
  const filterParams = useMemo(() => ({
    classification_group: category, search: search.trim(), classification: status,
  }), [category, search, status]);
  const tileParams = (id) => (CATEGORY_TILES.find((t) => t.id === id) || CATEGORY_TILES[2]).params;

  const loadHistory = useCallback(async (group) => {
    const key = groupKey(group);
    setHistories((value) => ({ ...value, [key]: { ...(value[key] || {}), loading: !value[key]?.items, error: "" } }));
    try {
      const body = await request(`/api/mail-monitoring/groups/emails?${query({ candidate_id: group.candidate_id, company_key: group.company_key || "" })}`);
      setHistories((value) => ({ ...value, [key]: { items: body.notifications || [], loading: false, error: "" } }));
    } catch (error) {
      setHistories((value) => ({ ...value, [key]: { ...(value[key] || {}), loading: false, error: error?.message || "Emails could not be loaded." } }));
    }
  }, []);

  const load = useCallback(async ({ silent = false } = {}) => {
    const current = ++version.current;
    if (!silent) setLoading(true);
    try {
      const base = filterParams;
      const [groups, summary, ...tileCounts] = await Promise.all([
        request(`/api/mail-monitoring/groups?${query({ ...base, company_key: companyKey, ...tileParams(tile), limit: PAGE_SIZE, offset })}`),
        fetchMailSummary(),
        ...CATEGORY_TILES.map((t) => request(`/api/mail-monitoring/notifications?${query({ ...base, ...t.params, limit: 1, offset: 0, sort: "newest" })}`)),
      ]);
      if (current !== version.current) return;
      setData({ groups: groups.groups || [], total: Number(groups.total) || 0, companies: groups.companies || [] });
      setCounts(Object.fromEntries(CATEGORY_TILES.map((t, index) => [t.id, Number(tileCounts[index]?.total) || 0])));
      setEmailCount(Number(tileCounts[CATEGORY_TILES.findIndex((t) => t.id === tile)]?.total) || 0);
      setLoadError("");
      publishMailUnread(summary.summary?.unread);
      publishMailCategoryUnread(summary.summary);
    } catch (error) {
      if (current === version.current && !silent) {
        setData((value) => ({ ...value, groups: [], total: 0 }));
        setLoadError(error?.message || "Mail alerts could not be loaded.");
      }
    } finally {
      if (!silent && current === version.current) setLoading(false);
    }
  }, [filterParams, companyKey, tile, offset]);

  useEffect(() => { load(); }, [load]);
  useMailLive((event) => {
    if (!["notification_created", "notification_updated", "important_mail_detected", "mail_retry_pending", "connected"].includes(event?.event)) return;
    load({ silent: true });
    for (const group of data.groups) if (expanded.has(groupKey(group))) loadHistory(group);
  });

  const reset = (apply) => (event) => { setOffset(0); apply(event.target.value); };
  const toggle = (group, force) => {
    const key = groupKey(group);
    const open = typeof force === "boolean" ? force : !expanded.has(key);
    setExpanded((value) => { const next = new Set(value); if (open) next.add(key); else next.delete(key); return next; });
    if (open) loadHistory(group);
  };
  // Opening a mail marks that one mail read: the row, its group's counts and
  // the tile, at once, then the server's own figures.
  const openMail = useCallback(async (item) => {
    acknowledgeSelectionAlarm(item.id);
    setSelected({ ...item, is_read: true, detail_loading: true, detail_error: "" });
    if (!item.is_read) {
      setHistories((value) => Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, {
        ...entry, items: (entry.items || []).map((row) => (row.id === item.id ? { ...row, is_read: true } : row)),
      }])));
      setData((value) => ({ ...value, groups: value.groups.map((group) => (
        group.candidate_id === item.candidate_id && histories[groupKey(group)]?.items?.some((row) => row.id === item.id)
          ? { ...group, unread: Math.max(0, Number(group.unread) - 1), read: Number(group.read) + 1 }
          : group)) }));
    }
    try {
      const [detail] = await Promise.all([
        item.ai_recruitment_event_id ? request(`/api/ai-recruitment/events/${item.ai_recruitment_event_id}`) : Promise.resolve({ event: null }),
        item.is_read ? Promise.resolve() : request(`/api/mail-monitoring/notifications/${item.id}/read`, { method: "POST", body: "{}" }),
      ]);
      setSelected((current) => (current?.id === item.id ? { ...current, detail_loading: false, event_detail: detail.event || null } : current));
    } catch (error) {
      setSelected((current) => (current?.id === item.id ? { ...current, detail_loading: false, detail_error: error.message || "Unable to load the original email." } : current));
    } finally {
      load({ silent: true });
    }
  }, [histories, load]);
  const act = async (item, action) => {
    if (action !== "unread") acknowledgeSelectionAlarm(item.id);
    await request(`/api/mail-monitoring/notifications/${item.id}/${action}`, { method: "POST", body: "{}" });
    setSelected(null);
    for (const group of data.groups) if (expanded.has(groupKey(group))) loadHistory(group);
    load({ silent: true });
  };
  const markAllRead = async (group) => {
    const key = groupKey(group);
    const items = histories[key]?.items || (await request(`/api/mail-monitoring/groups/emails?${query({ candidate_id: group.candidate_id, company_key: group.company_key || "" })}`)).notifications || [];
    for (const item of items.filter((row) => !row.is_read)) {
      acknowledgeSelectionAlarm(item.id);
      await request(`/api/mail-monitoring/notifications/${item.id}/read`, { method: "POST", body: "{}" });
    }
    if (expanded.has(key)) loadHistory(group);
    load({ silent: true });
  };
  const download = async (format) => {
    if (downloading) return;
    setDownloading(format);
    try {
      const params = query({ format, ...filterParams, ...tileParams(tile), sort: "newest" });
      const response = await fetch(`${API}/api/mail-monitoring/export?${params}`, { credentials: "include" });
      if (!response.ok) throw new Error("Export failed");
      const blob = await response.blob();
      const match = (response.headers.get("Content-Disposition") || "").match(/filename="?([^"]+)"?/);
      if (typeof window !== "undefined" && window.URL?.createObjectURL) {
        const url = window.URL.createObjectURL(blob);
        const link = Object.assign(window.document.createElement("a"), { href: url, download: match ? match[1] : `mail_alerts.${format}` });
        window.document.body.appendChild(link); link.click(); link.remove(); window.URL.revokeObjectURL(url);
      }
    } catch (error) {
      console.error("Export error:", error);
    } finally {
      setDownloading(null);
    }
  };

  const filtered = Boolean(search.trim() || companyKey || status);
  const tileLabel = CATEGORY_TILES.find((t) => t.id === tile)?.label || "All Alerts";
  return <section className={`mcp mcp--${category}`}>
    <p className="mcp-info"><Glyph name="info" size={22} /><span>{page.description}</span></p>

    <div className="mcp-tiles" role="tablist" aria-label={`${page.title} tabs`}>
      {CATEGORY_TILES.map((t) => <button key={t.id} type="button" role="tab" aria-selected={tile === t.id}
        className={`mcp-tile mcp-tile--${t.id}${tile === t.id ? " mcp-tile--active" : ""}`}
        onClick={() => { setTile(t.id); setOffset(0); }}>
        <span className="mcp-tile__icon"><Glyph name={t.icon} size={26} /></span>
        <span className="mcp-tile__text"><span className="mcp-tile__label">{t.label}</span><span className="mcp-tile__count">{counts[t.id] ?? 0}</span></span>
      </button>)}
    </div>

    <label className="mcp-search">
      <Glyph name="search" size={22} />
      <input aria-label="Search notifications" placeholder="Search by candidate, email or company..." value={search} onChange={reset(setSearch)} />
    </label>
    <div className="mcp-filters">
      <label className="mcp-select"><Glyph name="building" size={22} />
        <select aria-label="Company filter" value={companyKey} onChange={reset(setCompanyKey)}>
          <option value="">All Companies</option>
          {data.companies.map((row) => <option key={row.key} value={row.key}>{row.name}</option>)}
        </select>
        <Glyph name="chevron" size={20} />
      </label>
      <label className="mcp-select"><Glyph name="tag" size={22} />
        <select aria-label="Status filter" value={status} onChange={reset(setStatus)}>
          <option value="">All Status</option>
          {statuses.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
        </select>
        <Glyph name="chevron" size={20} />
      </label>
    </div>

    <div className="mcp-tools">
      <span className="mail-live mail-live--live">Live</span>
      <div className="mail-download-group">
        <button type="button" className="mail-download-btn" disabled={!emailCount || downloading !== null} onClick={() => download("xlsx")}>{downloading === "xlsx" ? "Downloading…" : `Download ${emailCount} Mails`}</button>
        <button type="button" className="mail-download-btn mail-download-btn--json" disabled={!emailCount || downloading !== null} onClick={() => download("json")} aria-label="Download as JSON" title="Download as JSON">JSON</button>
      </div>
      {category === "interview" ? <InterviewBellControls /> : <SelectionAlarmControls showTest />}
    </div>

    <div className="mcp-results">
      <h2>{CATEGORY_NAME[category]} Alerts</h2>
      <span>{plural(data.total, "Result")}</span>
    </div>

    <div className={`mcp-list${loading ? " is-loading" : ""}`}>
      {loading && <OverlayLoader label="Loading alerts…" />}
      {!loading && loadError && <div className="mcp-empty" role="alert"><strong>Mail alerts could not be loaded.</strong><span>{loadError}</span><button type="button" onClick={() => load()}>Retry</button></div>}
      {!loading && !loadError && !data.groups.length && <div className="mcp-empty">
        <strong>{filtered ? "No alerts match these filters." : tile === "unread" ? page.emptyUnread : tile === "retry" ? "No alerts are waiting for AI retry." : page.emptyAll}</strong>
        <span>{filtered ? "Try another search, or clear the filters." : tile === "retry" ? "Mails the AI could not read yet appear here until a retry succeeds." : page.arriving}</span>
        {filtered && <button type="button" onClick={() => { setSearch(""); setCompanyKey(""); setStatus(""); setOffset(0); }}>Clear filters</button>}
      </div>}
      {data.groups.map((group) => <GroupCard key={groupKey(group)} group={group}
        expanded={expanded.has(groupKey(group))} onToggle={(force) => toggle(group, force)}
        history={histories[groupKey(group)]} onOpen={openMail} onMarkAllRead={() => markAllRead(group)} />)}
    </div>
    {data.total > PAGE_SIZE && <footer className="mcp-pages">
      <button type="button" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - PAGE_SIZE))}>Previous</button>
      <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total} · {tileLabel}</span>
      <button type="button" disabled={offset + PAGE_SIZE >= data.total} onClick={() => setOffset((value) => value + PAGE_SIZE)}>Next</button>
    </footer>}

    {selected && <NotificationDetail item={selected} onClose={() => setSelected(null)} actions={<>
      <button type="button" onClick={() => act(selected, "unread")}>Mark as unread</button>
      <button type="button" onClick={() => act(selected, "dismiss")}>Dismiss alert</button>
    </>} />}
  </section>;
}

export default MailCategoryPage;
