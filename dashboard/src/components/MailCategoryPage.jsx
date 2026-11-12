import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API } from "../config.js";
import {
  acknowledgeSelectionAlarm, getSelectionAlarmState, setSelectionAlarmMuted, setSelectionAlarmVolumeLevel,
  stopSelectionAlarmTest, subscribeSelectionAlarm, testSelectionAlarm,
} from "../notifications/selectionAlarm.js";
import { isInterviewBellMuted, setInterviewBellMuted, subscribeInterviewBell, testInterviewBell } from "../notifications/interviewBell.js";
import { unlockNotificationSound } from "../utils/notificationSound.js";
import { fetchMailSummary, publishMailCategoryUnread, publishMailUnread } from "../notifications/mailUnread.js";
import { OverlayLoader } from "../Loader.jsx";
import { formatIstDateTime } from "../utils/istTime.js";
import { OriginalEmail } from "./OriginalEmail.jsx";
import "./MailCategoryPage.css";
import {
  CLASSIFICATION_GROUPS, MAIL_CATEGORY_PAGES, displayLabel, mailAlertCategory,
  mailStatusTone, request, useMailLive,
} from "./MailMonitoringNotifications.jsx";

/**
 * Selection Related and Interview Related, as the approved mockups draw them:
 * one compact row of counters that are the tabs (Unread, Opened, AI Retry, All
 * Alerts), one row of search, company and status filters, a Download menu and
 * the page's alert sound controls, then the cards. Interview Related shows one
 * card per email (`/api/mail-monitoring/emails`). Selection Related shows one card per Candidate + Company. A
 * card shows that group's mail count, unread and opened, and its latest
 * evidence-checked status; expanded, it lists
 * every mail of the group newest first -- Selection and Interview alike, each
 * labelled -- unread ones in blue, read ones muted. Opening a mail marks that
 * one mail read and nothing else.
 *
 * Counts and rows come from the server (`/api/mail-monitoring/groups`), so the
 * page shows production data, never mockup numbers.
 */

const PAGE_SIZE = 20;
// Interview Related lists one compact row per email, so it can show more per
// page; the reader picks 10 or 20 and the choice is kept on this device only.
// 10 is the default (owner, 12 Nov 2026). Selection keeps its fixed page of groups.
export const INTERVIEW_PAGE_SIZES = [10, 20];
const INTERVIEW_PAGE_SIZE_KEY = "mailAlerts.interview.pageSize";
function storedInterviewPageSize() {
  try {
    const value = Number(window.localStorage.getItem(INTERVIEW_PAGE_SIZE_KEY));
    return INTERVIEW_PAGE_SIZES.includes(value) ? value : INTERVIEW_PAGE_SIZES[0];
  } catch { return INTERVIEW_PAGE_SIZES[0]; }
}
function storeInterviewPageSize(value) {
  try { window.localStorage.setItem(INTERVIEW_PAGE_SIZE_KEY, String(value)); } catch { /* private mode: the choice lasts this visit */ }
}
// The mockup's counters, in its order. Each is a tab with its own filter.
export const CATEGORY_TILES = [
  { id: "unread", label: "Unread", icon: "mail", params: { is_read: "false" } },
  { id: "opened", label: "Opened", icon: "eye", params: { is_read: "true" } },
  { id: "retry", label: "AI Retry", icon: "sparkles", params: { priority: "retry_pending" } },
  { id: "booking", label: "Booking failed", icon: "info", params: { priority: "booking_failed" } },
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
  download: <><path d="M12 4v11M7.5 10.5 12 15l4.5-4.5" /><path d="M4.5 16.5v3h15v-3" /></>,
  sheet: <><path d="M6 3.5h8l4 4v13H6z" /><path d="M14 3.5v4h4M9 12h6M9 15h6M9 18h4" /></>,
  braces: <><path d="M9 4.5c-2 0-2.5 1-2.5 3v2c0 1.5-.8 2.5-2 2.5 1.2 0 2 1 2 2.5v2c0 2 .5 3 2.5 3" /><path d="M15 4.5c2 0 2.5 1 2.5 3v2c0 1.5.8 2.5 2 2.5-1.2 0-2 1-2 2.5v2c0 2-.5 3-2.5 3" /></>,
  speaker: <><path d="M4.5 9.5h3l4.5-4v13l-4.5-4h-3z" /><path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11" /></>,
  bell: <><path d="M6.5 16.5V11a5.5 5.5 0 0 1 11 0v5.5l1.5 2h-14z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></>,
  bellOff: <><path d="M6.5 16.5V11a5.5 5.5 0 0 1 9.3-4" /><path d="M17.5 11v5.5l1.5 2H8.5M10 20.5a2 2 0 0 0 4 0M4 4l16 16" /></>,
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
// The status the alert's own mail states (server: verified_status and its
// evidence). Shown as stated; a step other than the label, a rejection or
// withdrawal, or Needs Review when the mail states none.
const statusText = (item) => displayLabel(item.verified_status || item.candidate_status || item.classification);
const EVIDENCE_NOTE = {
  "different step": "The email states this step, not the detected label.",
  "not a selection": "The email states a rejection or a withdrawal, not a selection.",
  "not stated": "The email does not state this status; open it to review.",
};
const evidenceOf = (item) => item.status_evidence || item.latest_status_evidence || "";
function StatusPill({ label, evidence, tone }) {
  const kind = evidence === "not stated" ? "review" : evidence === "not a selection" ? "muted" : tone;
  return <span className={`mcp-pill mcp-pill--status mcp-pill--${kind}`}
    title={EVIDENCE_NOTE[evidence] ? `Latest status. ${EVIDENCE_NOTE[evidence]}` : "Latest verified status"}>
    <Glyph name={evidence === "not stated" || evidence === "not a selection" ? "info" : "check"} size={16} />{label}
  </span>;
}
const groupKey = (group) => `${group.candidate_id}::${group.company_key || ""}`;
// The logo tile's text: the company's first word, without legal suffixes, so
// it fits whole ("Fractal", "VHS", "HCLTech"); the full name is on the card.
const LEGAL = /^(pvt|private|ltd|limited|llp|inc|co|corp|corporation|india|the)$/i;
export function logoText(company) {
  const words = String(company || "").replace(/[^A-Za-z0-9&.' -]+/g, " ").split(/[\s-]+/).filter((w) => w && !LEGAL.test(w.replace(/\./g, "")));
  return (words[0] || String(company || "?").trim()).slice(0, 12);
}
// "Open in Gmail": optional, in a new tab, on the mailbox the mail arrived in.
export function gmailUrl(gmail) {
  const id = gmail?.thread_id || gmail?.message_id;
  if (!id) return null;
  const account = gmail.account ? `?authuser=${encodeURIComponent(gmail.account)}` : "";
  return `https://mail.google.com/mail/u/${account}#all/${encodeURIComponent(id)}`;
}
const readWhen = (value) => formatIstDateTime(value, { day: "numeric", hour: "numeric", second: undefined });
const goTo = (view, detail = {}) => window.dispatchEvent(new CustomEvent("teleautomation:navigate", { detail: { view, ...detail } }));

// One mail, opened inside the card: its content as the sender wrote it, and
// what can be done with it. Gmail is one optional action among them.
function MailContent({ item, content, onAction }) {
  const url = gmailUrl(content?.gmail);
  return <div className="mcp-read" role="region" aria-label={`Email: ${item.email_subject || "no subject"}`}>
    <OriginalEmail
      email={content?.email}
      loading={!content || content.loading}
      error={content?.error}
      fallbackSubject={item.email_subject}
      fallbackRecipient={item.candidate_email}
      fallbackReceivedAt={item.email_received_at}
      formatWhen={readWhen}
    />
    <div className="mcp-read__actions">
      <button type="button" onClick={() => onAction(item, "unread")}>Mark as unread</button>
      <button type="button" onClick={() => onAction(item, "dismiss")}>Dismiss alert</button>
      {item.booking_id && <button type="button" onClick={() => goTo("daily-ops", { bookingId: item.booking_id, candidateId: item.candidate_id })}>View booking</button>}
      {/^https?:\/\//i.test(item.meeting_link || "") && <a href={item.meeting_link} target="_blank" rel="noopener noreferrer">Open meeting link</a>}
      {url && <a className="mcp-read__gmail" href={url} target="_blank" rel="noopener noreferrer">Open in Gmail ↗</a>}
    </div>
  </div>;
}

const logoSize = (text) => (text.length <= 5 ? "lg" : text.length <= 8 ? "md" : "sm");
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value !== null && value !== undefined) search.set(key, String(value));
  }
  return search.toString();
}

function GroupCard({ group, category, expanded, onToggle, history, openMails, contents, onOpenMail, onAction, onMarkAllRead }) {
  const company = group.company_name || "Company not identified";
  const status = group.latest_status || displayLabel(group.latest_classification || "");
  const tone = mailStatusTone({ candidate_status: group.latest_status, classification: group.latest_classification });
  const listId = `mcp-history-${groupKey(group).replace(/[^a-zA-Z0-9_-]/g, "-")}`;
  const name = group.candidate_name || "Candidate";
  const kind = CATEGORY_NAME[category] || "";
  // The card counts this page's category, as the counters above it do; the
  // history it expands to holds every mail with this company, both kinds.
  const total = Number(group.category_total ?? group.total) || 0;
  const unread = Number(group.category_unread ?? group.unread) || 0;
  const opened = Number(group.category_read ?? group.read) || 0;
  const historyUnread = Number(group.unread) || 0;
  const logo = group.company_name ? logoText(company) : "—";
  return <article className={`mcp-group${expanded ? " mcp-group--open" : ""}${unread ? " mcp-group--unread" : ""}`} aria-label={`${name}, ${company}`}>
    {/* Tapping anywhere on the card opens its emails here, in the app; only
        its own buttons and links do something else. */}
    <div className="mcp-group__head" onClick={(event) => { if (!event.target.closest("button, a, input, select")) onToggle(); }}>
      <div className={`mcp-logo mcp-logo--${logoSize(logo)}`} title={company} aria-hidden="true"><span>{logo}</span></div>
      <div className="mcp-group__main">
        <strong className="mcp-group__name" title={name}>{name}</strong>
        <span className="mcp-group__meta">
          {group.candidate_email && <span className="mcp-group__email" title={group.candidate_email}>{group.candidate_email}</span>}
          <span className="mcp-group__company" title={company}>{company}</span>
        </span>
        <span className="mcp-group__badges">
          <span className="mcp-pill mcp-pill--count" title={`${plural(total, `${kind} email`)} with ${company}`} aria-label={plural(total, `${kind} email`)}>
            <Glyph name="mail" size={16} />{total}
          </span>
          {unread > 0 && <span className="mcp-pill mcp-pill--new" title={`Unread ${kind} emails`}>{unread} unread</span>}
          {status && <StatusPill label={status} evidence={group.latest_status_evidence} tone={tone} />}
        </span>
      </div>
      <span className="mcp-group__opened" title={`${opened} ${kind} opened`} aria-label={`${opened} opened`}><Glyph name="eye" size={20} />{opened}</span>
      <button type="button" className={`mcp-chevron${expanded ? " mcp-chevron--open" : ""}`} aria-expanded={expanded} aria-controls={listId}
        aria-label={`${expanded ? "Hide" : "Show"} emails for ${name} at ${company}`} onClick={() => onToggle()}>
        <Glyph name="chevron" size={22} />
      </button>
    </div>
    {expanded && <div className="mcp-history-wrap">
      <div className="mcp-history__head">
        <HistorySummary items={history?.items} total={Number(group.total) || 0} unread={historyUnread} company={company} />
        {historyUnread > 0 && <button type="button" className="mcp-markall" onClick={onMarkAllRead}>Mark all as read</button>}
      </div>
      <ol className="mcp-history" id={listId} aria-label="Emails, newest first">
        {history?.loading && <li className="mcp-history__note">Loading emails…</li>}
        {history?.error && <li className="mcp-history__note" role="alert">{history.error}</li>}
        {(history?.items || []).map((item) => {
          const itemKind = mailAlertCategory(item);
          const evidence = evidenceOf(item);
          const open = openMails.has(item.id);
          return <li key={item.id} className={`mcp-mail ${item.is_read ? "mcp-mail--read" : "mcp-mail--unread"}${open ? " mcp-mail--open" : ""}`}>
            <span className="mcp-mail__dot" aria-hidden="true" />
            <button type="button" className="mcp-mail__row" onClick={() => onOpenMail(item)} aria-expanded={open}
              aria-label={`${item.is_read ? "Opened" : "Unread"}: ${item.email_subject || "no subject"}`}>
              <span className="mcp-mail__icon"><Glyph name="mail" size={18} /></span>
              <span className="mcp-mail__text">
                <span className="mcp-mail__subject">{item.email_subject || "(no subject)"}</span>
                <span className={`mcp-mail__label${itemKind ? ` mcp-mail__label--${itemKind}` : ""}${evidence === "not stated" ? " mcp-mail__label--review" : ""}`}
                  title={EVIDENCE_NOTE[evidence]}>{itemKind ? `${CATEGORY_NAME[itemKind]} · ` : ""}{statusText(item)}</span>
              </span>
              <span className={`mcp-state mcp-state--${item.is_read ? "read" : "unread"}`}>{item.is_read ? "Opened" : "Unread"}</span>
              <span className="mcp-mail__when">{mailWhen(item.email_received_at || item.created_at)}</span>
            </button>
            {open && <MailContent item={item} content={contents[item.id]} onAction={onAction} />}
          </li>;
        })}
        {history && !history.loading && !history.error && !(history.items || []).length && <li className="mcp-history__note">No emails in this group.</li>}
      </ol>
    </div>}
  </article>;
}

// Where a company came from when the alert itself named none (server:
// core/mail_alert_company.py). Shown on hover; the name itself is plain.
const COMPANY_SOURCE = {
  booking: "from this interview's booking",
  thread: "from the same email thread",
  sender: "from the employer's own email domain",
  subject: "stated in the subject",
  "email body": "stated in the email",
};

// Interview Related: one card per email, no grouping. Candidate, company,
// subject, the status the mail states, when it arrived, and Unread / Opened.
// Tapping it opens the mail here and marks that one mail read.
export function EmailCard({ item, open, content, onOpenMail, onAction }) {
  const name = item.candidate_name || "Candidate";
  const company = item.company_name || "Company not identified";
  const evidence = evidenceOf(item);
  const unread = !item.is_read;
  return <article className={`mcp-email${unread ? " mcp-email--unread" : " mcp-email--read"}${open ? " mcp-email--open" : ""}`}
    aria-label={`${unread ? "Unread" : "Opened"}: ${name}, ${company}`}>
    <button type="button" className="mcp-email__row" onClick={() => onOpenMail(item)} aria-expanded={open}
      aria-label={`${unread ? "Unread" : "Opened"}: ${item.email_subject || "no subject"} from ${company} for ${name}`}>
      <span className="mcp-email__top">
        <strong className="mcp-email__name" title={name}>{name}</strong>
        <span className={`mcp-state mcp-state--${unread ? "unread" : "read"}`}>{unread ? "Unread" : "Opened"}</span>
      </span>
      <span className="mcp-email__company" title={COMPANY_SOURCE[item.company_source] ? `${company} — ${COMPANY_SOURCE[item.company_source]}` : company}><Glyph name="building" size={15} />{company}{item.job_role ? <span className="mcp-email__role"> · {item.job_role}</span> : null}</span>
      <span className="mcp-email__subject" title={item.email_subject || undefined}>{item.email_subject || "(no subject)"}</span>
      <span className="mcp-email__foot">
        <StatusPill label={statusText(item)} evidence={evidence} tone={mailStatusTone(item)} />
        <span className="mcp-email__when">{mailWhen(item.email_received_at || item.created_at)}</span>
      </span>
    </button>
    {open && <MailContent item={item} content={content} onAction={onAction} />}
  </article>;
}

// One line over the history: everything with this company, by kind, and how
// much of it is unread -- the card's own counts are this page's kind only.
function HistorySummary({ items, total, unread, company }) {
  const byKind = { selection: 0, interview: 0 };
  for (const item of items || []) {
    const kind = mailAlertCategory(item);
    if (kind in byKind) byKind[kind] += 1;
  }
  const parts = [];
  if (items?.length) {
    if (byKind.selection) parts.push(`${byKind.selection} Selection`);
    if (byKind.interview) parts.push(`${byKind.interview} Interview`);
  }
  return <p className="mcp-history__summary">
    <span>All emails with {company}: <b>{total}</b>{parts.length ? ` (${parts.join(", ")})` : ""}</span>
    <span className="mcp-history__unread">{unread ? `${unread} unread` : "All opened"}</span>
  </p>;
}

// One Download button; Excel and JSON inside it, each with the count it holds.
function DownloadMenu({ count, downloading, onDownload }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (event) => { if (ref.current && !ref.current.contains(event.target)) setOpen(false); };
    const escape = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("mousedown", close); document.removeEventListener("keydown", escape); };
  }, [open]);
  const pick = (format) => { setOpen(false); onDownload(format); };
  return <div className="mcp-download" ref={ref}>
    <button type="button" className="mcp-download__button" aria-haspopup="menu" aria-expanded={open}
      disabled={!count || downloading !== null} onClick={() => setOpen((value) => !value)}>
      <Glyph name="download" size={20} />{downloading ? "Downloading…" : "Download"}<Glyph name="chevron" size={18} />
    </button>
    {open && <div className="mcp-download__menu" role="menu" aria-label="Download format">
      <button type="button" role="menuitem" onClick={() => pick("xlsx")}><Glyph name="sheet" size={20} />Excel ({count})</button>
      <button type="button" role="menuitem" onClick={() => pick("json")}><Glyph name="braces" size={20} />JSON ({count})</button>
    </div>}
  </div>;
}

// The page's alert sound, as icons: Selection has the alarm's volume, mute and
// test; Interview the bell's mute and test. Mute is a pressed/unpressed button.
function SoundControls({ category }) {
  const [alarm, setAlarm] = useState(getSelectionAlarmState);
  const [bellMuted, setBellMuted] = useState(isInterviewBellMuted);
  const [testing, setTesting] = useState(false);
  useEffect(() => subscribeSelectionAlarm(setAlarm), []);
  useEffect(() => subscribeInterviewBell(setBellMuted), []);
  if (category === "interview") {
    return <div className="mcp-sound" role="group" aria-label="Interview alert sound">
      <span className="mcp-sound__label"><Glyph name="speaker" size={22} />Interview bell</span>
      <button type="button" className="mcp-icon-btn" aria-pressed={bellMuted} aria-label={bellMuted ? "Unmute interview bell" : "Mute interview bell"}
        title={bellMuted ? "Unmute" : "Mute"} onClick={() => { unlockNotificationSound(); setInterviewBellMuted(!bellMuted); }}>
        <Glyph name={bellMuted ? "bellOff" : "bell"} size={22} />
      </button>
      <button type="button" className="mcp-icon-btn" aria-label="Test interview bell" title="Test bell"
        onClick={() => { unlockNotificationSound(); testInterviewBell(); }}><Glyph name="speaker" size={22} /></button>
    </div>;
  }
  const percent = Math.round((alarm.volume ?? 1) * 100);
  return <div className="mcp-sound" role="group" aria-label="Selection alarm sound">
    <Glyph name="speaker" size={22} />
    <input type="range" min="0" max="100" step="5" value={percent} aria-label="Selection alarm volume" aria-valuetext={`${percent}%`}
      onChange={(event) => setSelectionAlarmVolumeLevel(Number(event.target.value) / 100)} />
    <output>{percent}%</output>
    <button type="button" className="mcp-icon-btn" aria-pressed={!!alarm.muted} aria-label={alarm.muted ? "Unmute selection alarm" : "Mute selection alarm"}
      title={alarm.muted ? "Unmute" : "Mute"} onClick={() => { unlockNotificationSound(); setSelectionAlarmMuted(!alarm.muted); }}>
      <Glyph name="bellOff" size={22} />
    </button>
    {!(alarm.pending || []).length && <button type="button" className="mcp-icon-btn" aria-pressed={testing}
      aria-label={testing ? "Stop alarm test" : "Test alarm"} title={testing ? "Stop test" : "Test alarm"}
      onClick={() => { unlockNotificationSound(); if (testing) { stopSelectionAlarmTest(); setTesting(false); } else { testSelectionAlarm(); setTesting(true); } }}>
      <Glyph name="bell" size={22} />
    </button>}
  </div>;
}

export function MailCategoryPage({ category }) {
  const page = MAIL_CATEGORY_PAGES[category];
  const [tile, setTile] = useState("all");
  const [search, setSearch] = useState("");
  const [companyKey, setCompanyKey] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  // Interview Related lists every email on its own; Selection keeps its groups.
  const perEmail = category === "interview";
  const [interviewPageSize, setInterviewPageSize] = useState(storedInterviewPageSize);
  const limit = perEmail ? interviewPageSize : PAGE_SIZE;
  const [data, setData] = useState({ groups: [], emails: [], total: 0, companies: [] });
  const [counts, setCounts] = useState({});
  const [emailCount, setEmailCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [expanded, setExpanded] = useState(() => new Set());
  const [histories, setHistories] = useState({});
  const [openMails, setOpenMails] = useState(() => new Set());
  const [contents, setContents] = useState({});
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
        perEmail
          // The company options come with the emails: they include companies
          // resolved from an alert's evidence, so those can be filtered too.
          ? request(`/api/mail-monitoring/emails?${query({ ...base, company_key: companyKey, ...tileParams(tile), limit, offset })}`)
            .then((emails) => ({ emails: emails.emails || [], total: emails.total, companies: emails.companies || [] }))
          : request(`/api/mail-monitoring/groups?${query({ ...base, company_key: companyKey, ...tileParams(tile), limit, offset })}`),
        fetchMailSummary(),
        ...CATEGORY_TILES.map((t) => request(`/api/mail-monitoring/notifications?${query({ ...base, ...t.params, limit: 1, offset: 0, sort: "newest" })}`)),
      ]);
      if (current !== version.current) return;
      setData({ groups: groups.groups || [], emails: groups.emails || [], total: Number(groups.total) || 0, companies: groups.companies || [] });
      setCounts(Object.fromEntries(CATEGORY_TILES.map((t, index) => [t.id, Number(tileCounts[index]?.total) || 0])));
      setEmailCount(Number(tileCounts[CATEGORY_TILES.findIndex((t) => t.id === tile)]?.total) || 0);
      setLoadError("");
      publishMailUnread(summary.summary?.unread);
      publishMailCategoryUnread(summary.summary);
    } catch (error) {
      if (current === version.current && !silent) {
        setData((value) => ({ ...value, groups: [], emails: [], total: 0 }));
        setLoadError(error?.message || "Mail alerts could not be loaded.");
      }
    } finally {
      if (!silent && current === version.current) setLoading(false);
    }
  }, [filterParams, companyKey, tile, offset, limit, perEmail, category]);

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
  // Opening a mail shows its content inside the card and marks that one mail
  // read: the row, its group's counts and the tile at once, then the server's
  // own figures. Opening it again closes it.
  const loadContent = useCallback(async (item) => {
    setContents((value) => ({ ...value, [item.id]: { ...(value[item.id] || {}), loading: !value[item.id]?.email, error: "" } }));
    try {
      const body = await request(`/api/mail-monitoring/notifications/${item.id}/email`);
      setContents((value) => ({ ...value, [item.id]: { loading: false, email: body.email || null, gmail: body.gmail || null,
        error: body.email ? "" : "The original email body is unavailable." } }));
    } catch (error) {
      setContents((value) => ({ ...value, [item.id]: { loading: false, email: null, error: error?.message || "Unable to load the email." } }));
    }
  }, []);
  const openMail = useCallback(async (item) => {
    const closing = openMails.has(item.id);
    setOpenMails((value) => { const next = new Set(value); if (closing) next.delete(item.id); else next.add(item.id); return next; });
    if (closing) return;
    acknowledgeSelectionAlarm(item.id);
    if (!contents[item.id]?.email) loadContent(item);
    if (item.is_read) return;
    setHistories((value) => Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, {
      ...entry, items: (entry.items || []).map((row) => (row.id === item.id ? { ...row, is_read: true } : row)),
    }])));
    setData((value) => ({ ...value, emails: value.emails.map((row) => (row.id === item.id ? { ...row, is_read: true } : row)), groups: value.groups.map((group) => (
      histories[groupKey(group)]?.items?.some((row) => row.id === item.id)
        ? {
          ...group, unread: Math.max(0, Number(group.unread) - 1), read: Number(group.read) + 1,
          ...(mailAlertCategory(item) === category && group.category_unread !== undefined ? {
            category_unread: Math.max(0, Number(group.category_unread) - 1), category_read: Number(group.category_read) + 1,
          } : {}),
        }
        : group)) }));
    try {
      await request(`/api/mail-monitoring/notifications/${item.id}/read`, { method: "POST", body: "{}" });
    } finally {
      load({ silent: true });
    }
  }, [openMails, contents, histories, load, loadContent, category]);
  const act = async (item, action) => {
    if (action !== "unread") acknowledgeSelectionAlarm(item.id);
    await request(`/api/mail-monitoring/notifications/${item.id}/${action}`, { method: "POST", body: "{}" });
    setOpenMails((value) => { const next = new Set(value); next.delete(item.id); return next; });
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
    <div className="mcp-tiles" role="tablist" aria-label={`${page.title} tabs`}>
      {CATEGORY_TILES.map((t) => <button key={t.id} type="button" role="tab" aria-selected={tile === t.id}
        title={t.id === "retry" ? `${CATEGORY_NAME[category]} alerts waiting for AI retry` : `${t.label} ${CATEGORY_NAME[category]} alerts`}
        className={`mcp-tile mcp-tile--${t.id}${tile === t.id ? " mcp-tile--active" : ""}`}
        onClick={() => { setTile(t.id); setOffset(0); }}>
        <span className="mcp-tile__icon"><Glyph name={t.icon} size={22} /></span>
        <span className="mcp-tile__text"><span className="mcp-tile__label">{t.label}</span><span className="mcp-tile__count">{counts[t.id] ?? 0}</span></span>
      </button>)}
    </div>

    <div className="mcp-filters">
      <label className="mcp-search">
        <Glyph name="search" size={20} />
        <input aria-label="Search notifications" placeholder="Search" value={search} onChange={reset(setSearch)} />
      </label>
      <label className="mcp-select"><Glyph name="building" size={20} />
        <select aria-label="Company filter" value={companyKey} onChange={reset(setCompanyKey)}>
          <option value="">All Companies</option>
          {data.companies.map((row) => <option key={row.key} value={row.key}>{row.name}</option>)}
        </select>
        <Glyph name="chevron" size={18} />
      </label>
      <label className="mcp-select"><Glyph name="tag" size={20} />
        <select aria-label="Status filter" value={status} onChange={reset(setStatus)}>
          <option value="">All Status</option>
          {statuses.map((value) => <option key={value} value={value}>{displayLabel(value)}</option>)}
        </select>
        <Glyph name="chevron" size={18} />
      </label>
    </div>

    <div className="mcp-tools">
      <DownloadMenu count={emailCount} downloading={downloading} onDownload={download} />
      <SoundControls category={category} />
    </div>

    <div className="mcp-results">
      <h2><Glyph name="mail" size={24} />{CATEGORY_NAME[category]} Alerts</h2>
      <span>{plural(data.total, perEmail ? "Email" : "Result")}</span>
    </div>

    <div className={`mcp-list${loading ? " is-loading" : ""}`}>
      {loading && <OverlayLoader label="Loading alerts…" />}
      {!loading && loadError && <div className="mcp-empty" role="alert"><strong>Mail alerts could not be loaded.</strong><span>{loadError}</span><button type="button" onClick={() => load()}>Retry</button></div>}
      {!loading && !loadError && !(perEmail ? data.emails : data.groups).length && <div className="mcp-empty">
        <strong>{filtered ? "No alerts match these filters." : tile === "unread" ? page.emptyUnread : tile === "retry" ? "No alerts are waiting for AI retry." : tile === "booking" ? "No automatic booking has failed." : page.emptyAll}</strong>
        <span>{filtered ? "Try another search, or clear the filters." : tile === "retry" ? "Mails the AI could not read yet appear here until a retry succeeds." : tile === "booking" ? "Mails read correctly whose interview could not be booked, cancelled or moved appear here, with the reason." : page.arriving}</span>
        {filtered && <button type="button" onClick={() => { setSearch(""); setCompanyKey(""); setStatus(""); setOffset(0); }}>Clear filters</button>}
      </div>}
      {perEmail && data.emails.map((item) => <EmailCard key={item.id} item={item} open={openMails.has(item.id)}
        content={contents[item.id]} onOpenMail={openMail} onAction={act} />)}
      {!perEmail && data.groups.map((group) => <GroupCard key={groupKey(group)} group={group}
        category={category} expanded={expanded.has(groupKey(group))} onToggle={(force) => toggle(group, force)}
        history={histories[groupKey(group)]} openMails={openMails} contents={contents} onOpenMail={openMail}
        onAction={act} onMarkAllRead={() => markAllRead(group)} />)}
    </div>
    {(data.total > limit || (perEmail && data.total > INTERVIEW_PAGE_SIZES[0])) && <footer className="mcp-pages">
      <button type="button" disabled={offset === 0} onClick={() => setOffset((value) => Math.max(0, value - limit))}>Previous</button>
      <span>{offset + 1}–{Math.min(offset + limit, data.total)} of {data.total} · {tileLabel}</span>
      <button type="button" disabled={offset + limit >= data.total} onClick={() => setOffset((value) => value + limit)}>Next</button>
      {perEmail && <label className="mcp-pages__size">Per page
        <select value={interviewPageSize} aria-label="Emails per page"
          onChange={(event) => { const value = Number(event.target.value); setInterviewPageSize(value); storeInterviewPageSize(value); setOffset(0); }}>
          {INTERVIEW_PAGE_SIZES.map((size) => <option key={size} value={size}>{size}</option>)}
        </select>
      </label>}
    </footer>}
  </section>;
}

export default MailCategoryPage;
