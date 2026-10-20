import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API } from "../config.js";
import { subscribeMailEvents, subscribeMailStatus, traceMailAlert } from "../notifications/mailEventStream.js";
import { fetchMailSummary, publishMailUnread } from "../notifications/mailUnread.js";
import { useDialogA11y } from "../hooks/useDialogA11y.js";
import { formatIstDateTime, formatScheduleDateTime, formatScheduleIstDateTime } from "../utils/istTime.js";
import { useConfirm } from "../context/ConfirmContext.jsx";
import { OverlayLoader } from "../Loader.jsx";
import { OriginalEmail } from "./OriginalEmail.jsx";
import { Icon } from "./ui/Icon.jsx";

// Important candidate employment outcomes and actionable interview activity.
export const TRACKED_CLASSIFICATIONS = [
  "job_selection_confirmed", "offer_received", "final_round_cleared",
  "hr_confirmation", "offer_accepted",
  "offer_declined", "offer_revoked", "joining_confirmed",
  "joining_date_updated", "onboarding_started", "background_verification",
  "document_verification", "compensation_confirmation",
  "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
  "interview_cancelled", "candidate_rejected", "interview_needs_reading",
];
// Tracked categories for the compact notification-type filter.
const JOB_CONFIRMED_CLASSIFICATIONS = [
  "job_selection_confirmed", "offer_received", "final_round_cleared",
  "hr_confirmation", "offer_accepted",
  "offer_declined", "offer_revoked", "joining_confirmed",
  "joining_date_updated", "onboarding_started", "background_verification",
  "document_verification", "compensation_confirmation", "candidate_rejected",
];
const AUTO_BOOKING_CLASSIFICATIONS = [
  "interview_shortlisted", "interview_confirmed", "interview_rescheduled",
  // An interview mail the reader could not read is interview work too, and
  // the server groups it here (INTERVIEW_RELATED_CLASSIFICATIONS).
  "interview_cancelled", "interview_needs_reading",
];

// The Selection view is the grouped one. Named rather than spelled inline so
// the filter option, the request and the render cannot drift apart.
export const SELECTION_GROUP = "selection";
// The filter offers two groups instead of eighteen classifications. Built from
// the lists above rather than restating them, so the screen and the server
// cannot drift apart; the server derives "selection" the same way, as
// everything tracked that is not interview-related.
export const CLASSIFICATION_GROUPS = [
  { value: SELECTION_GROUP, label: "Selection Related", classifications: JOB_CONFIRMED_CLASSIFICATIONS },
  { value: "interview", label: "Interview Related", classifications: AUTO_BOOKING_CLASSIFICATIONS },
];
// What automatic booking made of an alert. The server decides which rows
// are which (`booking_result_sql`), so the list, its paging and its total
// all agree; alerts that are not booking outcomes are in neither.
export const BOOKING_RESULTS = [
  { value: "booked", label: "Successfully booked" },
  { value: "blocked", label: "Blocked" },
];
// Every filter at rest. The "All" card resets to exactly this: it used to
// rebuild the object by hand, drop the candidate and alert-type keys, and send
// both as the string "undefined" -- a candidate filter no row can match, so
// "All" emptied the table.
export const EMPTY_FILTERS = Object.freeze({
  search: "", classificationGroup: "", candidateId: "", bookingResult: "", priority: "", read: "",
});
// Above this many options a native select stops being browsable and the filter
// needs real type-ahead.
const CANDIDATE_TYPEAHEAD_THRESHOLD = 20;
const candidateLabel = (row) => row.candidate_name || row.candidate_id;
const human = (value) => String(value || "").replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const when = (value) => formatIstDateTime(value, {
  day: "numeric",
  hour: "numeric",
  second: undefined,
});
const confidence = (value) => `${Math.round(Number(value || 0) * 100)}%`;

// A status or automation label as the table shows it. The server's wording is
// kept; only its case is evened out, because 37 alerts said "Needs reading"
// beside "Interview Automatically Booked" and 50 said "AUTOMATED" beside
// "Auto Booked": every word starts with a capital, a word in capitals of four
// letters or more is written as a word, and a short acronym (BGV, HR, AI)
// stays as it is.
const SMALL_WORDS = new Set(["a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "via"]);
export function displayLabel(value) {
  return String(value || "").replaceAll("_", " ").trim().split(/\s+/).filter(Boolean)
    .map((word, index) => {
      if (/^[A-Z]{4,}$/.test(word)) return word[0] + word.slice(1).toLowerCase();
      if (/^[a-z]/.test(word) && !(index > 0 && SMALL_WORDS.has(word))) return word[0].toUpperCase() + word.slice(1);
      return word;
    })
    .join(" ");
}
/** What automatic booking did with the alert; "Automated" when it changed no booking. */
export const automationLabel = (item = {}) => displayLabel(item.booking_status || item.automation_state || "Automated");
/** The same tones as the status badge, so "Auto Booked" and "Blocked" read the same in both columns. */
export const automationTone = (item = {}) => mailStatusTone({ booking_status: automationLabel(item) });
const statusLabel = (item = {}) => displayLabel(item.candidate_status || human(item.classification));

// Which of the two filter groups an alert belongs to, for colour.
//
// Derived from CLASSIFICATION_GROUPS rather than a second list of its own, so
// the colour on a row always agrees with the filter that would select it. A
// third group added later gets a colour by adding one CSS rule, not by editing
// a mapping here that nothing would fail if you forgot.
export function mailAlertCategory(item = {}) {
  const classification = String(item?.classification || "").trim();
  if (!classification) return "";
  const group = CLASSIFICATION_GROUPS.find((entry) =>
    entry.classifications.includes(classification),
  );
  return group ? group.value : "";
}

export function mailStatusTone(item = {}) {
  const status = [item.candidate_status, item.booking_status, item.classification]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  // A booking someone took off the roster after the mail made it: nothing is
  // wrong with the mail, but a person should see what became of the interview.
  // First, because its classification still says rescheduled or confirmed.
  if (/booking removed/.test(status)) return "warning";
  if (/automatically booked|auto booked|already booked|approved.*booked|joining confirmed|selection confirmed|selected|offer accepted|offer received/.test(status)) return "success";
  if (/final round cleared|interview confirmed|rescheduled/.test(status)) return "info";
  if (/hr confirmation|document verification|documents requested|compensation confirmation|booking blocked|blocked/.test(status)) return "warning";
  if (/processing failed|cancelled|rejected|failed/.test(status)) return "danger";
  if (/ai.retry.pending|automatic retry/.test(status)) return "warning";
  return "neutral";
}

// Why a booking was blocked, as the backend decided and explained it: a title,
// a reason and what to do, in plain words. Never reconstructed here from the
// status text or the codes -- only the backend knows which of several blocks
// applied, and how the same cause reads for an email that creates, changes or
// cancels a booking. The codes travel along for Technical details only.
const FALLBACK_TITLES = {
  interview_cancelled: "Cancellation not applied",
  interview_rescheduled: "Booking not updated",
};
export function blockingReason(item = {}) {
  if (item.booking_block?.reason) return item.booking_block;
  if (!item.booking_block_reason && !item.booking_block_reason_code) return null;
  // Without an explanation there is nothing precise to say about why, so the
  // reason stays general and whatever was stored goes to Technical details: an
  // older alert's stored sentence can be exactly the wording this panel hides.
  // A code still never becomes the reason a person reads.
  return {
    title: /assessment/i.test(item.candidate_status || "")
      ? "Assessment not booked"
      : FALLBACK_TITLES[item.classification] || "Booking not created",
    reason: "We couldn't finish this automatically.",
    action: "Please review the email and update Daily Ops if needed.",
    needs_action: true,
    technical: {
      reason_code: item.booking_block_reason_code || null,
      internal_code: item.booking_failure_code || null,
      booking_status: item.booking_status || null,
      message: item.booking_block_reason || null,
    },
  };
}

// The codes behind a block, for whoever is debugging it. Closed by default:
// nobody needs them to understand what happened or what to do.
function TechnicalDetails({ technical = {} }) {
  const rows = [
    ["Reason code", technical.reason_code, true],
    ["Internal code", technical.internal_code, true],
    ["Booking status", technical.booking_status, false],
    ["System message", technical.message, false],
  ].filter(([, value]) => value);
  if (!rows.length) return null;
  return <details className="mail-block__technical">
    <summary>Technical details</summary>
    <dl className="mail-block__codes">{rows.map(([label, value, isCode]) => <div key={label}><dt>{label}</dt><dd>{isCode ? <code>{value}</code> : value}</dd></div>)}</dl>
  </details>;
}

async function request(path, options = {}) {
  const response = await fetch(`${API}${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || "Request failed");
  return body;
}

function navigate(view, detail = {}) {
  window.dispatchEvent(new CustomEvent("teleautomation:navigate", { detail: { view, ...detail } }));
}

/**
 * Live mail events for UI.
 *
 * The socket itself now lives in notifications/mailEventStream.js and is shared
 * by every subscriber, so this page and the header bell no longer open one
 * each. Sound is not triggered here: GlobalNotificationSounds subscribes to the
 * same stream and is the only place that makes a noise, which is what stops one
 * event from being heard twice.
 */
function useMailLive(onUpdate) {
  const [status, setStatus] = useState("Offline");
  const callback = useRef(onUpdate);
  callback.current = onUpdate;

  useEffect(() => subscribeMailEvents((payload) => callback.current?.(payload)), []);
  useEffect(() => subscribeMailStatus(setStatus), []);

  return status;
}

export function MailNotificationBell({ compact = false }) {
  const [open, setOpen] = useState(false);
  const [summary, setSummary] = useState({ unread: 0 });
  const [items, setItems] = useState([]);
  const [toast, setToast] = useState(null);
  const wrap = useRef(null);
  const loadVersion = useRef(0);
  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    try {
      const [summaryBody, listBody] = await Promise.all([
        fetchMailSummary(),
        request("/api/mail-monitoring/notifications?limit=6&offset=0"),
      ]);
      if (version !== loadVersion.current) return;
      setSummary(summaryBody.summary || {}); setItems(listBody.notifications || []);
      publishMailUnread(summaryBody.summary?.unread);
    } catch { /* API fallback will retry */ }
  }, []);
  const live = useMailLive((event) => {
    if (["notification_created", "notification_updated", "important_mail_detected", "mail_retry_pending", "connected"].includes(event?.event)) load();
    if (event?.event === "notification_created") {
      setToast(event); window.setTimeout(() => setToast(null), 6000);
    }
  });
  useEffect(() => {
    load(); const id = window.setInterval(load, 30000); return () => window.clearInterval(id);
  }, [load]);
  useEffect(() => {
    // Ask up front so the first tracked mail doesn't spend its alert on a prompt.
    if (typeof Notification !== "undefined" && Notification.permission === "default") {
      Notification.requestPermission().catch(() => {});
    }
  }, []);
  useEffect(() => {
    const close = (event) => wrap.current && !wrap.current.contains(event.target) && setOpen(false);
    document.addEventListener("mousedown", close); return () => document.removeEventListener("mousedown", close);
  }, []);
  const action = async (id, name) => {
    await request(`/api/mail-monitoring/notifications/${id}/${name}`, { method: "POST", body: "{}" });
    await load();
  };
  const unread = Number(summary.unread || 0);
  return <div className={`mail-bell${compact ? " mail-bell--compact" : ""}`} ref={wrap}>
    <button type="button" className="mail-bell__button" aria-label={`${unread} unread mail monitoring notifications`} title="Mail monitoring alerts" onClick={() => setOpen((value) => !value)}>
      <span aria-hidden>📧</span>{unread > 0 && <span className="mail-bell__count">{unread > 99 ? "99+" : unread}</span>}
    </button>
    {open && <div className="mail-bell__popover">
      <header><div><strong>Mail monitoring</strong><span className={`mail-live mail-live--${live.toLowerCase()}`}>{live}</span></div><button type="button" onClick={() => { setOpen(false); navigate("mail-notifications"); }}>View all</button></header>
      <div className="mail-bell__list">{items.length ? items.map((item) => <article className={item.is_read ? "" : "is-unread"} key={item.id}>
        <button type="button" className="mail-bell__main" onClick={() => { if (!item.is_read) action(item.id, "read"); setOpen(false); navigate("mail-notifications", { notificationId: item.id }); }}>
          <strong>{item.candidate_name || "Candidate"} · {item.company_name || "Company pending"}</strong>
          <span>{item.candidate_status || human(item.classification)}</span><small>{when(item.email_received_at || item.created_at)}</small>
        </button>
        <button type="button" className="mail-bell__toggle" onClick={() => action(item.id, item.is_read ? "unread" : "read")}>{item.is_read ? "Unread" : "Read"}</button>
      </article>) : <p className="mail-empty">No mail alerts yet.</p>}</div>
    </div>}
    {toast && <button type="button" className="mail-alert-toast" onClick={() => { setToast(null); navigate("mail-notifications", { notificationId: toast.notification_id }); }}><strong>{toast.status || human(toast.classification)}</strong><span>{toast.candidate_name || "Candidate"}{toast.company_name ? ` · ${toast.company_name}` : ""}</span></button>}
  </div>;
}

// Three compact tabs that sit above the filter row and instantly narrow results.
// Using tab state rather than hard-coding filter values keeps the existing
// server-side filter parameters as the single source of truth: the tab merely
// pre-sets them; the advanced dropdowns still work on top.
export const MAIL_TABS = [
  { id: "unread",  label: "Unread Invitations", filterKey: "read",     filterValue: "false"         },
  { id: "retry",   label: "AI Retry Pending",   filterKey: "priority", filterValue: "retry_pending" },
  { id: "all",     label: "All Alerts",          filterKey: null,       filterValue: null            },
];
// Which filter fields a tab owns. Switching tabs clears exactly these keys
// and sets the new tab's key, while leaving candidate / alert-type / search
// / booking-result untouched so users can filter within a tab.
const TAB_OWNED_KEYS = new Set(["read", "priority"]);

// Read-only. The panel reports what the pipeline decided and offers the two
// ways of acting on it that live outside this screen -- opening the booking and
// joining the meeting. It writes nothing: the correction, re-run, dismiss and
// confirm controls are retired. Legacy decision-writing endpoints return 410;
// read/unread tracking remains separate from interview state.
function NotificationDetail({ item, onClose }) {
  // Mounted only while open, so the dialog is open for its whole life.
  const dialogRef = useDialogA11y(true, onClose);
  const originalEmail = item.event_detail?.received_email;
  const block = blockingReason(item);
  // Empty when the invite is already IST, so the extra line appears only when
  // the reader actually has to convert something.
  const istInterviewTime = formatScheduleIstDateTime(
    item.interview_date,
    item.interview_time,
    item.interview_timezone,
  );
  return <div className="mail-detail-backdrop" role="presentation" onClick={(event) => event.target === event.currentTarget && onClose()}>
    <section ref={dialogRef} className="mail-detail" role="dialog" aria-modal="true" aria-label="Mail monitoring notification">
      <header><div><h3>{block ? block.title : (item.candidate_status || human(item.classification))}</h3><p>{item.candidate_name || "Candidate"} · {item.company_name || "Company unavailable"}</p></div><button type="button" onClick={onClose} aria-label="Close">×</button></header>
      <OriginalEmail
        email={originalEmail}
        loading={item.detail_loading}
        error={item.detail_error}
        fallbackSubject={item.email_subject}
        fallbackRecipient={item.candidate_email}
        fallbackReceivedAt={item.email_received_at}
        formatWhen={when}
      />
      {block && <section className={`mail-block${block.needs_action ? "" : " mail-block--info"}`} aria-label="Booking decision">
        <p className="mail-block__line"><span className="mail-block__label">Reason</span>{block.reason}</p>
        {block.action && <p className="mail-block__line"><span className="mail-block__label">What to do</span>{block.action}</p>}
        <TechnicalDetails technical={block.technical} />
      </section>}
      <dl><div><dt>Email</dt><dd>{item.email_subject || "No subject"}</dd></div><div><dt>From</dt><dd>{item.sender_name || item.sender_email || "Unknown"}</dd></div><div><dt>Mail received</dt><dd>{when(item.email_received_at)}</dd></div><div><dt>Tool detected</dt><dd>{when(item.created_at)}</dd></div><div><dt>AI confidence</dt><dd>{confidence(item.ai_confidence)}</dd></div>{item.interview_date && <div><dt>Interview</dt><dd>{formatScheduleDateTime(item.interview_date, item.interview_time, item.interview_timezone)}</dd></div>}{istInterviewTime && <div><dt>IST Time</dt><dd>{istInterviewTime}</dd></div>}{item.interview_round && <div><dt>Round</dt><dd>{item.interview_round}</dd></div>}</dl>
      <div className="mail-detail__copy">
        <strong>Summary</strong>
        <p>{item.ai_summary || "No summary available."}</p>
        <details className="mail-detail__aside" open>
          <summary>Detection reason</summary>
          <p>{item.ai_reason || "Contextual classification"}</p>
          {/* The detector's own wording, kept for whoever is debugging; the
              line above is what a person needs. */}
          {item.ai_reason_technical && <p className="mail-detail__technical">Technical: {item.ai_reason_technical}</p>}
        </details>
        {!block && <details className="mail-detail__aside" open>
          <summary>Recommended action</summary>
          <p>{item.recommended_action || "Processed automatically; no operator decision is required."}</p>
        </details>}
      </div>
      <footer>
        {item.booking_id && <button type="button" onClick={() => { onClose(); navigate("daily-ops", { bookingId: item.booking_id, candidateId: item.candidate_id }); }}>View booking</button>}
        {/^https?:\/\//i.test(item.meeting_link || "") && <button type="button" onClick={() => window.open(item.meeting_link, "_blank", "noopener,noreferrer")}>Open meeting link</button>}
      </footer>
    </section>
  </div>;
}

export function MailMonitoringNotifications() {
  const { confirm } = useConfirm();
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState({ new_offers: 0, selections: 0, joining_confirmations: 0, auto_booked_interviews: 0, ai_retry_pending: 0, unread: 0 });
  const [selected, setSelected] = useState(null);
  const [clearing, setClearing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  // activeTab defaults to "unread" so the panel opens on actionable alerts.
  const [activeTab, setActiveTab] = useState("unread");
  // Candidates that actually have alerts. Fetched once; the filter matches on
  // candidate_id, so the options have to be ids this table really holds.
  const [candidates, setCandidates] = useState([]);
  const [candidateQuery, setCandidateQuery] = useState("");
  const loadVersion = useRef(0);
  const pendingRenders = useRef(new Map());
  // Merge user-supplied filters with the active tab's owned filter values.
  // Tab-owned keys (read, priority) are overridden by the tab; all others
  // pass through unchanged so candidate / alert-type / search / booking-result
  // keep working inside any tab.
  const effectiveFilters = useMemo(() => {
    const tab = MAIL_TABS.find((t) => t.id === activeTab) || MAIL_TABS[0];
    const merged = { ...filters };
    for (const key of TAB_OWNED_KEYS) merged[key] = "";
    if (tab.filterKey) merged[tab.filterKey] = tab.filterValue;
    return merged;
  }, [filters, activeTab]);
  const query = useMemo(() => {
    const params = new URLSearchParams({ limit: "20", offset: String(page * 20), sort: "newest" });
    for (const [key, value] of Object.entries({ search:effectiveFilters.search,classification_group:effectiveFilters.classificationGroup,candidate_id:effectiveFilters.candidateId,booking_result:effectiveFilters.bookingResult,priority:effectiveFilters.priority,is_read:effectiveFilters.read,group_by:effectiveFilters.classificationGroup === SELECTION_GROUP ? "candidate" : "" })) if (value !== "") params.set(key, String(value));
    return params.toString();
  }, [effectiveFilters, page]);
  const load = useCallback(async ({ silent = false } = {}) => {
    const version = ++loadVersion.current;
    if (!silent) setLoading(true);
    try {
      const [list, counts] = await Promise.all([request(`/api/mail-monitoring/notifications?${query}`),fetchMailSummary()]);
      if (version !== loadVersion.current) return;
      setItems(list.notifications || []);setTotal(list.total || 0);setSummary(counts.summary || {});
      publishMailUnread(counts.summary?.unread);
    } catch { /* retain last good state */ }
    finally { if (!silent && version === loadVersion.current) setLoading(false); }
  }, [query]);
  useMailLive((event) => {
    if (["notification_created", "notification_updated"].includes(event?.event) && event?.notification_id) {
      pendingRenders.current.set(String(event.notification_id), event);
    }
    if (["notification_created","notification_updated","important_mail_detected","mail_retry_pending","connected"].includes(event?.event)) load({ silent:true });
  });
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    for (const [notificationId, event] of pendingRenders.current) {
      const visible = items.some((item) => String(item.id) === notificationId);
      traceMailAlert(visible ? "ui_row_rendered" : "ui_row_not_visible", event, { query });
      pendingRenders.current.delete(notificationId);
    }
  }, [items, query]);
  // Loaded once, not per filter change: the option list is the set of
  // candidates with alerts, which does not depend on what is filtered. A
  // failure here leaves the dropdown empty rather than blocking the table.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const data = await request("/api/mail-monitoring/candidates");
        if (!cancelled) setCandidates(data.candidates || []);
      } catch { /* filter degrades to All candidates */ }
    })();
    return () => { cancelled = true; };
  }, []);
  // The Selection view is grouped; the Interview view and the unfiltered view
  // stay flat. Interview mails cannot appear here at all - `classification_group
  // =selection` is applied on the server, and the two groups are disjoint by
  // construction, so nothing interview-related reaches this list to be grouped.
  const grouped = effectiveFilters.classificationGroup === SELECTION_GROUP;
  // One parent per candidate, in the order the server returned them, holding
  // every one of that candidate's mails as its own row. Nothing is merged or
  // deduplicated: two mails from the same company on the same day are two
  // alerts and must both stay readable and actionable.
  const candidateGroups = useMemo(() => {
    const order = [];
    const byCandidate = new Map();
    for (const item of items) {
      // candidate_id is NOT NULL in the table; the fallbacks only stop a row
      // with a missing id from being merged into one nameless parent.
      const key = item.candidate_id || item.candidate_email || item.id;
      if (!byCandidate.has(key)) {
        byCandidate.set(key, { key, name: item.candidate_name || "Candidate", email: item.candidate_email || "", items: [] });
        order.push(key);
      }
      byCandidate.get(key).items.push(item);
    }
    return order.map((key) => byCandidate.get(key));
  }, [items]);
  const set = (key) => (event) => { setPage(0); setFilters((value) => ({ ...value, [key]: event.target.value })); };
  const act = async (item, action) => { await request(`/api/mail-monitoring/notifications/${item.id}/${action}`, { method:"POST", body:"{}" }); load({ silent:true }); };
  // Switching tabs resets page only — user's candidate/alert-type/search/booking
  // filters are preserved so they keep working within the new tab view.
  const switchTab = (tabId) => { setActiveTab(tabId); setPage(0); };
  // The filters a person set, as opposed to the tab's own.
  const filtersActive = Boolean(filters.search || filters.candidateId || filters.classificationGroup || filters.bookingResult);
  const clearFilters = () => { setPage(0); setCandidateQuery(""); setFilters(EMPTY_FILTERS); };
  // The table scrolls inside its own box on a desktop, so another page, tab or
  // filter starts that box at the top rather than wherever the last one was.
  const wrapRef = useRef(null);
  useEffect(() => {
    const box = wrapRef.current;
    if (box && typeof box.scrollTo === "function") box.scrollTo({ top: 0 });
  }, [query]);
  const openNotification = useCallback(async (item) => {
    setSelected({ ...item, is_read: true, detail_loading: true, detail_error: "" });
    if (!item.is_read) {
      setItems((rows) => rows.map((row) => row.id === item.id ? { ...row, is_read: true } : row));
      setSummary((value) => {
        const next = { ...value, unread: Math.max(0, Number(value.unread || 0) - 1) };
        publishMailUnread(next.unread);
        return next;
      });
    }
    const detailRequest = item.ai_recruitment_event_id
      ? request(`/api/ai-recruitment/events/${item.ai_recruitment_event_id}`)
      : Promise.resolve({ event: null });
    try {
      const [detail] = await Promise.all([
        detailRequest,
        item.is_read
          ? Promise.resolve()
          : request(`/api/mail-monitoring/notifications/${item.id}/read`, { method:"POST", body:"{}" }),
      ]);
      setSelected((current) => current?.id === item.id
        ? { ...current, detail_loading: false, event_detail: detail.event || null }
        : current);
    } catch (error) {
      setSelected((current) => current?.id === item.id
        ? { ...current, detail_loading: false, detail_error: error.message || "Unable to load the original email." }
        : current);
    } finally {
      load({ silent:true });
    }
  }, [load]);
  const clearAll = async () => {
    // `total` counts candidates while the Selection view is grouped, so it is
    // not a usable fallback here: this dialog deletes notifications across
    // every filter, and understating how many would be a destructive prompt
    // with the wrong number on it. Better to omit the count than to invent it.
    const count=summary.visible_total ?? (grouped ? null : total);
    const confirmed = await confirm({
      title: "Clear all mail notifications?",
      message: count === null
        ? "Remove every notification from this list across every filter?"
        : `Remove all ${count} notifications from this list across every filter?`,
      confirmLabel: "Clear notifications",
      cancelLabel: "Keep notifications",
      variant: "danger",
      kept: ["Email evidence", "Booking audits", "Candidate history"],
    });
    if (!confirmed) return;
    setClearing(true);
    try {
      await request("/api/mail-monitoring/notifications/clear-all", { method:"POST", body:"{}" });
      setSelected(null);setPage(0);await load();
    } finally { setClearing(false); }
  };
  // Live counts shown on each tab badge, pulled from the summary endpoint so
  // they reflect the full dataset regardless of what is currently filtered.
  const tabCounts = {
    unread: summary.unread || 0,
    retry:  summary.ai_retry_pending || 0,
    all:    summary.visible_total || 0,
  };
  return <section className="mail-monitoring-page">
    {/* The tabs and the page's two controls share one row: the page is named
        once, in the shell header, so the controls needed no row of their own. */}
    <div className="mail-topbar">
    <nav className="mail-tabs" role="tablist" aria-label="Mail alerts filter tabs">
      {MAIL_TABS.map((tab) => {
        const count = tabCounts[tab.id] ?? 0;
        const isActive = activeTab === tab.id;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            className={`mail-tab${isActive ? " mail-tab--active" : ""}`}
            onClick={() => switchTab(tab.id)}
          >
            <span className="mail-tab__label">{tab.label}</span>
            <span className={`mail-tab__count${count === 0 ? " mail-tab__count--zero" : ""}`}>{count > 99 ? "99+" : count}</span>
          </button>
        );
      })}
    </nav>
    <header className="mail-monitoring-page__head"><div className="mail-monitoring-page__actions"><button type="button" className="mail-clear-all" disabled={!(summary.visible_total ?? total) || clearing} onClick={clearAll}>{clearing ? "Clearing…" : "Clear all notifications"}</button><span className="mail-live mail-live--live">Live</span></div></header>
    </div>
    <div className="mail-filters mail-filters--compact">
      <input aria-label="Search notifications" placeholder="Search candidate, email, company or subject" value={filters.search} onChange={set("search")} />
      {/* Beyond this many options a native select is unusable, so switch to a
          datalist, which gives substring type-ahead while staying a plain
          input the existing control styling already covers. */}
      {candidates.length > CANDIDATE_TYPEAHEAD_THRESHOLD ? (
        <>
          <input
            aria-label="Candidate filter"
            list="mail-candidate-options"
            placeholder="All candidates"
            value={candidateQuery}
            onChange={(event) => {
              const text = event.target.value;
              setCandidateQuery(text);
              const match = candidates.find((row) => candidateLabel(row) === text);
              setPage(0);
              setFilters((value) => ({ ...value, candidateId: match ? match.candidate_id : "" }));
            }}
          />
          <datalist id="mail-candidate-options">
            {candidates.map((row) => <option value={candidateLabel(row)} key={row.candidate_id} />)}
          </datalist>
        </>
      ) : (
        <select aria-label="Candidate filter" value={filters.candidateId} onChange={set("candidateId")}>
          <option value="">All candidates</option>
          {candidates.map((row) => <option value={row.candidate_id} key={row.candidate_id}>{candidateLabel(row)}</option>)}
        </select>
      )}
      <select aria-label="Alert type filter" value={filters.classificationGroup} onChange={set("classificationGroup")}>
        <option value="">All alert types</option>
        {CLASSIFICATION_GROUPS.map((group) => <option value={group.value} key={group.value}>{group.label}</option>)}
      </select>
      <select aria-label="Booking result filter" value={filters.bookingResult} onChange={set("bookingResult")}>
        <option value="">All booking results</option>
        {BOOKING_RESULTS.map((result) => <option value={result.value} key={result.value}>{result.label}</option>)}
      </select>
    </div>
    <div ref={wrapRef} className={`mail-table-wrap${loading ? " is-loading" : ""}`}>{loading && <OverlayLoader label="Loading notifications…" />}<table className={`mail-table${grouped ? " mail-table--grouped" : ""}`}><thead><tr><th className="mail-col mail-col--candidate">Candidate</th><th className="mail-col mail-col--company">Company</th><th className="mail-col mail-col--status">Detected status</th><th className="mail-col mail-col--subject">Email subject</th><th className="mail-col mail-col--confidence">Confidence</th><th className="mail-col mail-col--received">Mail received</th><th className="mail-col mail-col--detected">Tool detected</th><th className="mail-col mail-col--automation">Automation</th><th className="mail-col mail-col--actions">Action</th></tr></thead>
      {/* One <tbody> per candidate when grouped, so the parent is a real table
          section rather than a row pretending to be a heading. The row itself is
          the same in both modes - only the candidate cell differs, because in
          grouped mode the name is already on the parent above it. */}
      {(grouped ? candidateGroups : [{ key: "all", items }]).map((group) => <tbody key={group.key} className={grouped ? "mail-group" : undefined}>
      {grouped && <tr className="mail-group__head"><th colSpan={9} scope="colgroup">
        <strong>{group.name}</strong>
        {group.email && <small>{group.email}</small>}
        <span className="mail-group__count">{group.items.length} selection {group.items.length === 1 ? "mail" : "mails"}</span>
      </th></tr>}
      {group.items.map((item) => <tr
        key={item.id}
        className={`${item.is_read ? "" : "is-unread"} mail-notification-row${mailAlertCategory(item) ? ` mail-notification-row--${mailAlertCategory(item)}` : ""}`}
        tabIndex={0}
        aria-label={`Open email notification: ${item.email_subject || "no subject"}`}
        onClick={() => openNotification(item)}
        onKeyDown={(event) => {
          if (event.target !== event.currentTarget || !["Enter", " "].includes(event.key)) return;
          event.preventDefault();
          openNotification(item);
        }}
      >{/* Every long value is cut to one line on a desktop and carries its whole
          text as a tooltip; the alert itself shows everything. */}
      <td className={`mail-cell mail-cell--candidate${grouped ? " mail-row__nested" : ""}`}>{grouped
        ? <span className="mail-row__thread" aria-hidden="true" />
        : <><strong title={[item.candidate_name, item.candidate_email].filter(Boolean).join(" · ") || undefined}>{item.candidate_name || "Candidate"}</strong><small title={item.candidate_email || undefined}>{item.candidate_email || ""}</small></>}</td><td className="mail-cell mail-cell--company"><span className="mail-cell__text" title={item.company_name || undefined}>{item.company_name || "—"}</span><small title={item.job_role || undefined}>{item.job_role || ""}</small></td><td className="mail-cell mail-cell--status"><span title={statusLabel(item)} className={`mail-status mail-status--${mailStatusTone(item)}${mailAlertCategory(item) ? ` mail-status--${mailAlertCategory(item)}` : ""}`}>{statusLabel(item)}</span>{(() => {
        const reason = blockingReason(item);
        if (!reason) return null;
        // Shown in the row itself: a blocked booking is unusable information
        // until you know why, and making that a click away hides it.
        return <span
          className="mail-status__reason"
          title={reason.action ? `What to do: ${reason.action}` : undefined}
        >Reason: {reason.reason}</span>;
      })()}</td><td className="mail-cell mail-cell--subject" title={item.email_subject || undefined}>{item.email_subject || "(no subject)"}</td><td className="mail-cell mail-cell--confidence">{confidence(item.ai_confidence)}</td><td className="mail-cell mail-cell--received">{when(item.email_received_at)}</td><td className="mail-cell mail-cell--detected">{when(item.created_at)}</td><td className="mail-cell mail-cell--automation"><span className={`mail-automation mail-automation--${automationTone(item)}`} title={automationLabel(item)}>{automationLabel(item)}</span></td><td className="mail-cell mail-cell--actions" onClick={(event) => event.stopPropagation()}>
        <button type="button" className="mail-action" aria-label="Open alert" title="Open alert" onClick={() => openNotification(item)}><Icon name="eye" size={15} /></button>
        <button type="button" className="mail-action" aria-label={item.is_read ? "Mark as unread" : "Mark as read"} title={item.is_read ? "Mark as unread" : "Mark as read"} onClick={() => act(item, item.is_read ? "unread" : "read")}><Icon name={item.is_read ? "mail" : "check"} size={15} /></button>
        <button type="button" className="mail-action mail-action--dismiss" aria-label="Dismiss alert" title="Dismiss alert" onClick={() => act(item, "dismiss")}><Icon name="x" size={15} /></button>
      </td></tr>)}
      </tbody>)}
      {!loading && !items.length && <tbody><tr className="mail-empty-row"><td colSpan={9} className="mail-empty">
        <div className="mail-empty-state">
          <strong>{
            // When advanced filters (search, candidate, alert-type, booking) are set, the
            // generic message is always correct regardless of tab. Tab-specific friendly
            // messages only fire when the tab itself is the sole filter applied.
            filtersActive
              ? "No notifications match these filters."
              : activeTab === "unread" ? "No unread invitations. All caught up!"
              : activeTab === "retry"  ? "No alerts are waiting for AI retry."
              : "No notifications match these filters."
          }</strong>
          <span>{filtersActive
            ? "Try another search, or clear the filters to see every alert in this tab."
            : activeTab === "unread" ? "New invitations appear here as soon as they arrive."
            : activeTab === "retry" ? "Mails the AI could not read yet appear here until a retry succeeds."
            : "Mail alerts appear here as they arrive."}</span>
          {filtersActive && <button type="button" className="mail-empty-state__clear" onClick={clearFilters}>Clear filters</button>}
        </div>
      </td></tr></tbody>}
    </table></div>
    {total > 20 && <footer className="mail-pagination"><span>{total} {grouped ? "candidates" : "notifications"}</span><button disabled={page===0} onClick={() => setPage((value) => value-1)}>Previous</button><span>Page {page+1}</span><button disabled={(page+1)*20>=total} onClick={() => setPage((value) => value+1)}>Next</button></footer>}
    {selected && <NotificationDetail item={selected} onClose={() => setSelected(null)} />}
  </section>;
}
