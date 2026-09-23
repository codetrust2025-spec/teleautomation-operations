import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API } from "../config.js";
import { useConfirm } from "../context/ConfirmContext.jsx";
import { mailboxUiStatus, reconnectWorklist } from "../utils/mailboxStatus.js";
import { publishGmailExpired } from "../notifications/gmailExpired.js";
import { subscribeMailStatus, subscribeNodeActivity } from "../notifications/mailEventStream.js";
import { ButtonContent, InlineLoader, OverlayLoader } from "../Loader.jsx";
import { OcrToggle } from "./OcrToggle.jsx";
import { PureOllamaToggle } from "./PureOllamaToggle.jsx";
import { ReconnectWorklist } from "./ReconnectWorklist.jsx";

const request = async (path, options = {}) => {
  const isGet = !options.method || options.method === "GET";
  const join = path.includes("?") ? "&" : "?";
  const response = await fetch(
    `${API}${isGet ? `${path}${join}_offerReview=offer_review_cleanup_v1` : path}`,
    {
      credentials: "include",
      cache: isGet ? "no-store" : undefined,
      headers: {
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    },
  );
  const body = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(body.detail || body.message || "Request failed");
  return body;
};

const trackedStatuses = new Set([
  "SELECTED",
  "FINAL_SELECTION_CONFIRMED",
  "OFFER_INDICATION",
  "OFFER_IN_PROGRESS",
  "OFFER_APPROVED",
  "OFFER_LETTER_RECEIVED",
  "APPOINTMENT_LETTER_RECEIVED",
  "OFFER_ACCEPTED",
  "JOINING_CONFIRMED",
  "JOINED",
  "POST_SELECTION_ONBOARDING",
  "INTERVIEW_CONFIRMED",
  "INTERVIEW_SHORTLISTED",
  "MANUAL_REVIEW_REQUIRED",
  "AI_RETRY_PENDING",
]);
const hiddenReviews = new Set(["IGNORED", "FALSE_POSITIVE", "DUPLICATE"]);
// Mirrors the automation groups exposed by
// core/recruitment_mail_store.py::summarize_selection_tracking_events.
const STATUS_GROUP_STATUSES = {
  automation_pending: ["AI_RETRY_PENDING", "MANUAL_REVIEW_REQUIRED", "IGNORED_LOW_CONFIDENCE"],
  selected: ["SELECTED", "FINAL_SELECTION_CONFIRMED"],
  offers_received: [
    "OFFER_INDICATION",
    "OFFER_IN_PROGRESS",
    "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED",
    "APPOINTMENT_LETTER_RECEIVED",
  ],
  offers_accepted: ["OFFER_ACCEPTED"],
  joining_confirmed: ["JOINING_CONFIRMED"],
  joined: ["JOINED"],
  offers: [
    "OFFER_INDICATION",
    "OFFER_IN_PROGRESS",
    "OFFER_APPROVED",
    "OFFER_LETTER_RECEIVED",
    "APPOINTMENT_LETTER_RECEIVED",
    "OFFER_ACCEPTED",
  ],
  joining: ["JOINING_CONFIRMED", "JOINED"],
};
const human = (value) =>
  String(value || "")
    .replaceAll("_", " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());

/** Epoch seconds from the node breaker into something readable at a glance. */
const whenever = (epochSeconds) => {
  if (!epochSeconds) return "—";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - epochSeconds));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
};

const AI_FAILURE_REASONS = {
  OLLAMA_CONNECTION_FAILED: "Ollama connection failed",
  OLLAMA_REQUEST_TIMEOUT: "Ollama request timed out",
  OLLAMA_MODEL_NOT_FOUND: "Configured AI model is not installed",
  OLLAMA_MODEL_LOAD_FAILED: "AI model failed to load",
  OLLAMA_INVALID_JSON: "AI returned invalid JSON",
  OLLAMA_SCHEMA_VALIDATION_FAILED: "AI response failed schema validation",
  OLLAMA_INTERNAL_ERROR: "AI service returned an internal error",
  REVERSE_SSH_TUNNEL_UNAVAILABLE: "AI tunnel is unreachable",
};
const aiFailureCode = (event) => {
  const model = String(event?.ai_model || "");
  return model.startsWith("unavailable:")
    ? model.slice("unavailable:".length).toUpperCase()
    : "";
};
const aiFailureReason = (event) => {
  const code = aiFailureCode(event);
  if (!code) return "AI validation is temporarily unavailable.";
  return AI_FAILURE_REASONS[code] || human(code);
};
const isManualAuditKeep = (event) =>
  event?.cleanup_version === "manual_content_audit_keep_v1";
const isManuallyApproved = (event) =>
  String(event?.review_status || "").toUpperCase() === "APPROVED" &&
  String(event?.validation_status || "").toUpperCase() === "APPROVED";
const aiNeverRan = (event) => {
  const aiStatus = String(
    event?.ai_status || event?.structured_result?.ai_status || "",
  ).toUpperCase();
  const validation = String(
    event?.validation_status ||
      event?.structured_result?.validation_status ||
      "",
  ).toUpperCase();
  return (
    Boolean(aiFailureCode(event)) ||
    aiStatus === "RETRY_PENDING" ||
    aiStatus === "NOT_REQUIRED" ||
    validation === "RETRY_PENDING" ||
    validation === "NOT_REQUIRED"
  );
};
// Single source of truth for the "AI / Validation" label shown in both the
// automation activity table and the Detection Evidence drawer.
const describeAiStatus = (event) => {
  const aiStatus = String(
    event?.ai_status || event?.structured_result?.ai_status || "",
  ).toUpperCase();
  const validation = String(
    event?.validation_status ||
      event?.structured_result?.validation_status ||
      "",
  ).toUpperCase();
  if (isManualAuditKeep(event)) {
    return {
      status: "Manual audit",
      reason: "Content verified; automatic AI retry disabled",
    };
  }
  if (isManuallyApproved(event)) {
    return {
      status: "Legacy approval",
      reason: aiFailureCode(event)
        ? `Legacy approval recorded after ${aiFailureReason(event).toLowerCase()}`
        : "Legacy approval recorded",
    };
  }
  if (aiFailureCode(event) || aiStatus === "RETRY_PENDING") {
    return { status: "Retry Pending", reason: aiFailureReason(event) };
  }
  if (aiStatus === "NOT_REQUIRED" || validation === "NOT_REQUIRED") {
    return {
      status: "Not required",
      reason:
        event?.evidence_summary ||
        event?.structured_result?.evidence_summary ||
        "Deterministic noise filter classified this email as non-actionable.",
    };
  }
  const statusLabel = human(aiStatus || "Unknown");
  const validationLabel = human(validation || event?.review_status || "");
  return {
    status: statusLabel,
    reason:
      validationLabel && validationLabel !== statusLabel ? validationLabel : "",
  };
};
const formatTime = (value) => {
  if (!value) return "Never";
  const date = new Date(value);
  const today = new Date();
  const label =
    date.toDateString() === today.toDateString()
      ? "Today"
      : date.toLocaleDateString();
  return `${label}, ${date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`;
};
const formatEmailDate = (value) => {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Never";
  const day = String(date.getDate()).padStart(2, "0");
  const month = String(date.getMonth() + 1).padStart(2, "0");
  return `${day}-${month}-${date.getFullYear()}`;
};
const formatEmailBody = (value) => {
  let body = String(value || "")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+>[ \t]+/g, "\n")
    .replace(/^\s*>+\s?/gm, "")
    .replace(/(?:^|\s)\*\s+(?=[A-Z])/g, "\n• ")
    .replace(/\*([^*\n]+)\*/g, "$1")
    .replace(/\s+_{12,}\s+/g, "\n\n")
    .replace(/[ \t]+\n/g, "\n")
    .trim();
  body = body
    .replace(/\bInvitation\s+(Dear\s+)/i, "Invitation\n\n$1")
    .replace(/,\s+(Greetings from\s+)/i, ",\n$1")
    .replace(/!\s+(We are pleased\s+)/i, "!\n\n$1")
    .replace(/\.\s+(Please find the interview details below:)/i, ".\n\n$1")
    .replace(
      /Skill\s+(.+?)\s+Date\s+(.+?)\s+Time\s+(.+?)\s+Duration\s+(.+?)\s+Mode\s+(.+?)\s+Meeting Link\b/i,
      "Skill\n$1\n\nDate\n$2\n\nTime\n$3\n\nDuration\n$4\n\nMode\n$5\n\nMeeting Link",
    )
    .replace(
      /Microsoft Teams meeting\s+Join:/i,
      "Microsoft Teams meeting\nJoin:",
    )
    .replace(/(https?:\/\/\S+)\s+(Meeting ID:)/i, "$1\n$2")
    .replace(/(Meeting ID:\s+.+?)\s+(Passcode:)/i, "$1\n$2")
    .replace(/(Passcode:\s+\S+)\s+(Important Instructions:)/i, "$1\n\n$2")
    .replace(/(scheduled time\.)\s+(We wish)/i, "$1\n\n$2")
    .replace(/(discussion\.)\s+(Regards,)/i, "$1\n\n$2")
    .replace(/(TAG Team)\s+(This is an automated)/i, "$1\n\n$2")
    .replace(/(invitation email\.)\s+(Information contained)/i, "$1\n\n$2")
    .replace(/\n{3,}/g, "\n\n");
  return body.trim();
};
const formatEmailAddress = (name, email) => {
  const safeName = String(name || "").trim();
  const safeEmail = String(email || "").trim();
  if (safeName && safeEmail) return `${safeName} <${safeEmail}>`;
  return safeEmail || safeName || "Unknown";
};
const initials = (name) =>
  String(name || "?")
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
const isVisibleEvent = (event) =>
  (trackedStatuses.has(event.primary_status) ||
    String(event.automation_state || "").toUpperCase() === "AI_RETRY_PENDING") &&
  !hiddenReviews.has(event.review_status) &&
  event.visible_in_offer_review !== false &&
  (String(event.automation_state || "").toUpperCase() === "AI_RETRY_PENDING" ||
    String(event.primary_status || "").toUpperCase() === "AI_RETRY_PENDING" ||
    (event.primary_status === "MANUAL_REVIEW_REQUIRED" &&
    ((event.validation_status || event.structured_result?.validation_status) ===
      "RETRY_PENDING" ||
      event.cleanup_version === "manual_content_audit_keep_v1")) ||
    (Number(event.confidence || 0) >= 0.8 &&
      Boolean(event.structured_result?.evidence?.length)));

const isActionRequiredEvent = (event) =>
  isVisibleEvent(event) &&
  String(event.review_status || "").toUpperCase() === "PENDING";

export function SummaryCard({
  tone,
  icon,
  value,
  title,
  subtitle,
  onClick,
  active,
}) {
  return (
    <article
      className={`sot-summary-card is-${tone}${active ? " is-active" : ""}${onClick ? " is-clickable" : ""}`}
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-pressed={onClick ? active : undefined}
      onKeyDown={
        onClick
          ? (event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onClick();
              }
            }
          : undefined
      }
    >
      <span className="sot-summary-icon" aria-hidden="true">
        {icon}
      </span>
      <div>
        <strong>{value ?? 0}</strong>
        <h3>{title}</h3>
        <p>{subtitle}</p>
      </div>
    </article>
  );
}

export function MailboxMetric({ icon, label, value, tone = "blue" }) {
  return (
    <article className={`sot-mailbox-metric is-${tone}`}>
      <span>{icon}</span>
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
      </div>
    </article>
  );
}

export function StatusBadge({ status }) {
  const normalized =
    {
      RECONNECT_REQUIRED: "Reconnect Required",
      PAUSED: "Monitoring Paused",
      SYNC_QUEUED: "Sync Queued",
      SYNCING: "Syncing Emails",
      CONNECTED: "Monitoring Active",
      // A closed, rejected or dropped candidate. The row and its history
      // stay; it simply is not active work and is not counted as any.
      MONITORING_ENDED: "Candidate Closed",
    }[status] || human(status);
  return (
    <span className={`sot-status-badge is-${status.toLowerCase()}`}>
      <i />
      {normalized}
    </span>
  );
}

export function FilterButton({ active, children, onClick }) {
  return (
    <button
      className={`sot-filter-button ${active ? "active" : ""}`}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function SearchInput({ value, onChange }) {
  return (
    <label className="sot-search">
      <span aria-hidden="true">⌕</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search candidate or Gmail account"
      />
    </label>
  );
}

export function ActionMenu({ row, busy, onAction }) {
  // Reconnect is not offered here: a row that needs it shows the warning
  // row directly beneath, with the same button on it. Two clicks apart,
  // same action, same condition -- the visible one is the one to keep.
  const selectAction = (event, action) => {
    event.currentTarget.closest("details")?.removeAttribute("open");
    onAction(action, row);
  };
  return (
    <details className="sot-action-menu">
      <summary aria-label={`More actions for ${row.candidate.name}`}>
        •••
      </summary>
      <div>
        <button
          disabled={busy}
          onClick={(event) => selectAction(event, "verify")}
        >
          Verify Connection
        </button>
        <button
          disabled={busy}
          onClick={(event) => selectAction(event, "sync")}
        >
          Sync Now
        </button>
        <button
          disabled={busy}
          onClick={(event) =>
            selectAction(
              event,
              row.mailbox.monitoring_enabled ? "pause" : "resume",
            )
          }
        >
          {row.mailbox.monitoring_enabled
            ? "Pause Monitoring"
            : "Resume Monitoring"}
        </button>
        <button
          disabled={busy}
          className="danger"
          onClick={(event) => selectAction(event, "disconnect")}
        >
          Disconnect Gmail
        </button>
      </div>
    </details>
  );
}

export function MailboxRow({ row, busy, onAction }) {
  const syncStatus = String(row.stats.latest_sync_status || "").toUpperCase();
  const syncActive = ["QUEUED", "RUNNING"].includes(syncStatus);
  return (
    <>
      <tr className="sot-mailbox-row">
        <td data-label="Candidate">
          <div className="sot-candidate">
            <span className="sot-avatar">{initials(row.candidate.name)}</span>
            <div>
              <strong>{row.candidate.name}</strong>
              {/* Phone and candidate id are shown on separate labelled lines.
                  This table is used to diagnose duplicate identities, so two
                  candidate rows that share a phone number must stay visually
                  distinguishable by their real candidate id. */}
              <small>Phone: {row.candidate.phone || "Not added"}</small>
              <small>Candidate ID: {row.candidate.id}</small>
            </div>
          </div>
        </td>
        <td data-label="Gmail Account">{row.mailbox.email_address}</td>
        <td data-label="Status">
          <StatusBadge status={row.uiStatus} />
        </td>
        <td data-label="Last Sync">
          {syncActive ? (
            <span className="sot-sync-progress" role="status">
              <i />
              {syncStatus === "RUNNING"
                ? "Processing emails…"
                : "Waiting to start…"}
            </span>
          ) : (
            formatTime(row.mailbox.last_successful_sync_at)
          )}
        </td>
        <td data-label="Actions">
          <div className="sot-row-actions">
            <ActionMenu row={row} busy={busy} onAction={onAction} />
          </div>
        </td>
      </tr>
      {row.uiStatus === "RECONNECT_REQUIRED" && (
        <tr className="sot-reconnect-row">
          <td colSpan={5}>
            <span>
              ⚠ Gmail connection expired. Reconnect to continue monitoring.
            </span>
            <button disabled={busy} onClick={() => onAction("reconnect", row)}>
              Reconnect Gmail
            </button>
          </td>
        </tr>
      )}
    </>
  );
}

export function MailboxTable({ rows, busy, onAction }) {
  return (
    <div className="sot-table-wrap">
      <table className="sot-mailbox-table">
        <thead>
          <tr>
            <th>Candidate</th>
            <th>Gmail Account</th>
            <th>Status</th>
            <th>Last Sync</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row) => (
              <MailboxRow
                key={row.mailbox.id}
                row={row}
                busy={busy}
                onAction={onAction}
              />
            ))
          ) : (
            <tr>
              <td colSpan={5} className="sot-empty">
                No mailboxes match this view.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function PendingMailboxTable({ candidates, busy, onConnect }) {
  return (
    <div className="sot-table-wrap">
      <table className="sot-mailbox-table sot-pending-mailbox-table">
        <thead>
          <tr>
            <th>Candidate</th>
            <th>Phone</th>
            <th>Technology</th>
            <th>Email on profile</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {candidates.length ? (
            candidates.map((candidate) => (
              <tr key={candidate.id}>
                <td data-label="Candidate">
                  <div className="sot-candidate">
                    <span className="sot-avatar">
                      {initials(candidate.name)}
                    </span>
                    <div>
                      <strong>{candidate.name}</strong>
                      <small>Profile in progress</small>
                    </div>
                  </div>
                </td>
                <td data-label="Phone">{candidate.phone || "Not added"}</td>
                <td data-label="Technology">
                  {candidate.technology || "Not specified"}
                </td>
                <td data-label="Email on profile">
                  {candidate.email ||
                    candidate.email_address ||
                    candidate.gmail_address ||
                    candidate.candidate_email ||
                    "Not added"}
                </td>
                <td data-label="Action">
                  <button
                    type="button"
                    className="sot-link-gmail-button"
                    disabled={busy}
                    onClick={() => onConnect(candidate)}
                  >
                    Link Gmail
                  </button>
                </td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={5} className="sot-empty">
                Every in-progress profile candidate has a linked Gmail account.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function AdvancedToolsAccordion({
  rows,
  candidateId,
  onCandidate,
  range,
  onRange,
  onRescan,
  busy,
}) {
  const selected = rows.find((row) => row.candidate.id === candidateId);
  return (
    <details className="sot-advanced-tools">
      <summary>
        <span className="sot-tool-icon">⌕</span>
        <div>
          <strong>Advanced Mailbox Tools</strong>
          <small>Technical tools for email recovery and historical sync</small>
        </div>
        <b>⌄</b>
      </summary>
      <div className="sot-advanced-content">
        <label>
          Mailbox
          <select
            value={candidateId}
            onChange={(e) => onCandidate(e.target.value)}
          >
            <option value="">Select mailbox</option>
            {rows.map((row) => (
              <option key={row.mailbox.id} value={row.candidate.id}>
                {row.candidate.name} · {row.mailbox.email_address}
              </option>
            ))}
          </select>
        </label>
        <label>
          From date
          <input
            type="date"
            value={range.range_start}
            max={range.range_end}
            onChange={(e) => onRange({ ...range, range_start: e.target.value })}
          />
        </label>
        <label>
          To date
          <input
            type="date"
            value={range.range_end}
            min={range.range_start}
            onChange={(e) => onRange({ ...range, range_end: e.target.value })}
          />
        </label>
        <button
          className="sot-primary-button"
          disabled={!selected || busy}
          onClick={onRescan}
        >
          Reprocess Stored Emails
        </button>
        {selected && (
          <dl>
            <div>
              <dt>Last successful synchronization</dt>
              <dd>{formatTime(selected.mailbox.last_successful_sync_at)}</dd>
            </div>
            <div>
              <dt>Latest synchronization error</dt>
              <dd>{selected.mailbox.last_error_message || "None"}</dd>
            </div>
          </dl>
        )}
      </div>
    </details>
  );
}

function AddMailboxForm({
  candidates,
  candidateId,
  email,
  busy,
  onCandidate,
  onEmail,
  onSubmit,
}) {
  return (
    <form
      className="sot-add-mailbox-form sot-main-add-mailbox-form"
      onSubmit={onSubmit}
    >
      <div className="sot-add-mailbox-copy">
        <h3>Connect a candidate Gmail</h3>
        <span>
          Select a candidate and authorize Gmail securely with Google. You can
          add multiple Gmail accounts without leaving this screen.
        </span>
      </div>
      <label>
        Candidate
        <select
          aria-label="Candidate Gmail owner"
          value={candidateId}
          onChange={(event) => onCandidate(event.target.value)}
          required
        >
          <option value="">Select candidate</option>
          {candidates.map((candidate) => (
            <option key={candidate.id} value={candidate.id}>
              {candidate.name} · {candidate.phone || "no phone"}
            </option>
          ))}
        </select>
      </label>
      <label>
        Gmail address
        <input
          type="email"
          value={email}
          onChange={(event) => onEmail(event.target.value)}
          placeholder="candidate@gmail.com (or 2nd Gmail)"
          autoComplete="email"
          required
        />
      </label>
      <button
        type="submit"
        className="sot-primary-button"
        disabled={busy || !candidateId || !email.trim()}
      >
        {busy ? "Starting…" : "Connect Gmail"}
      </button>
    </form>
  );
}

function AutomationActivity({
  events,
  names,
  candidateId,
  onClearCandidate,
  statusFilterLabel,
  onClearStatusFilter,
  onEvidence,
  onAddMailbox,
  addMailboxOpen,
}) {
  const pendingEvents = events.filter((event) =>
    String(event.automation_state || event.primary_status || "").toUpperCase() ===
      "AI_RETRY_PENDING",
  );
  const interviewEvents = events.filter((event) =>
    String(event.primary_status || "").startsWith("INTERVIEW_"),
  );
  const selectionEvents = events.length - interviewEvents.length;
  return (
    <section className="sot-content-card sot-priority-review">
      <header>
        <div>
          <span className="sot-workspace-eyebrow">AUTOMATED FLOW</span>
          <h2>Mail Automation</h2>
          <p>
            {statusFilterLabel
              ? `Showing only "${statusFilterLabel}" emails.`
              : "Every retained mail is being processed automatically; evidence remains available for audit."}
          </p>
        </div>
        <div className="sot-review-header-actions">
          <button
            type="button"
            className="sot-add-mailbox-button"
            onClick={onAddMailbox}
            aria-expanded={addMailboxOpen}
          >
            {addMailboxOpen ? "Close Gmail form" : "+ Add candidate Gmail"}
          </button>
          {candidateId && (
            <button onClick={onClearCandidate}>Show all candidates</button>
          )}
          {statusFilterLabel && (
            <button onClick={onClearStatusFilter}>
              Clear "{statusFilterLabel}" filter
            </button>
          )}
          <span>{events.length} records</span>
        </div>
      </header>
      <div
        className="sot-priority-review-summary"
        aria-label="Priority review summary"
      >
        <article className={pendingEvents.length ? "is-urgent" : ""}>
          <small>Automatic retries</small>
          <strong>{pendingEvents.length}</strong>
          <span>Awaiting AI recovery</span>
        </article>
        <article>
          <small>Selection &amp; offers</small>
          <strong>{selectionEvents}</strong>
          <span>Career outcome emails</span>
        </article>
        <article>
          <small>Interviews</small>
          <strong>{interviewEvents.length}</strong>
          <span>Schedule and booking emails</span>
        </article>
        <div className="sot-priority-order-note">
          <strong>No approval queue</strong>
          <span>Booking, cancellation, and reschedule changes apply automatically.</span>
        </div>
      </div>
      <div className="sot-table-wrap">
        <table className="sot-review-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Email Subject</th>
              <th>Intent / Lifecycle</th>
              <th>Company</th>
              <th>Confidence</th>
              <th>AI / Validation</th>
              <th>Evidence Summary</th>
              <th>Evidence</th>
            </tr>
          </thead>
          <tbody>
            {events.length ? (
              events.map((event) => {
                const fallback =
                  event.structured_result?.classification_source ===
                    "FALLBACK" ||
                  String(event.ai_model || "").includes("fallback:") ||
                  String(event.ai_model || "").includes("ai-unavailable");
                return (
                  <tr key={event.id}>
                    <td>
                      <strong>
                        {event.candidate_name ||
                          names[event.canonical_candidate_id] ||
                          names[event.candidate_id] ||
                          event.candidate_id}
                      </strong>
                      <small title="Email received time">
                        {formatEmailDate(
                          event.email_sent_at || event.created_at,
                        )}
                      </small>
                    </td>
                    <td>{event.subject || "No subject"}</td>
                    <td>
                      <small>
                        {human(
                          event.email_intent ||
                            event.structured_result?.email_intent ||
                            "Unknown",
                        )}
                      </small>
                      <span className="sot-outcome-badge">
                        {human(event.primary_status)}
                      </span>
                      <span
                        className={`sot-mail-domain ${String(event.primary_status || "").startsWith("INTERVIEW_") ? "is-interview" : "is-selection"}`}
                      >
                        {String(event.primary_status || "").startsWith(
                          "INTERVIEW_",
                        )
                          ? "Interview"
                          : "Selection / Offer"}
                      </span>
                    </td>
                    <td>
                      <strong>{event.company_name || "Unknown company"}</strong>
                      <small>{event.job_title || "Unknown role"}</small>
                    </td>
                    <td>
                      {isManualAuditKeep(event) ? (
                        <span className="sot-fallback-confidence">
                          Manual audit
                        </span>
                      ) : fallback ? (
                        <span
                          className="sot-fallback-confidence"
                          title={
                            event.structured_result?.fallback_reason ||
                            "AI validation unavailable"
                          }
                        >
                          Fallback evidence
                        </span>
                      ) : aiNeverRan(event) ? (
                        <span
                          className="sot-fallback-confidence"
                          title="AI has not analyzed this email; 0% would be misleading"
                        >
                          {describeAiStatus(event).status === "Not required"
                            ? "Not analyzed"
                            : "Pending AI"}
                        </span>
                      ) : (
                        `${Math.round(Number(event.confidence) * 100)}%`
                      )}
                    </td>
                    <td>
                      <strong>
                        {isManualAuditKeep(event)
                          ? "Legacy audit"
                          : aiFailureCode(event)
                            ? "AI unavailable"
                            : event.ai_model || "Not analyzed"}
                      </strong>
                      <small>
                        {describeAiStatus(event).status}
                        {describeAiStatus(event).reason && (
                          <> · {describeAiStatus(event).reason}</>
                        )}
                      </small>
                    </td>
                    <td>
                      {event.evidence_summary ||
                        event.structured_result?.evidence_summary ||
                        event.summary ||
                        "No summary"}
                    </td>
                    <td>
                      <div className="sot-review-actions">
                        <button onClick={() => onEvidence(event.id)}>
                          Evidence
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })
            ) : (
              <tr>
                <td colSpan={8} className="sot-empty">
                  No mail is awaiting automated processing.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// Daily Ops opens on a booking the same way Mail Alerts sends it there.
function openBooking(bookingId, candidateId) {
  window.dispatchEvent(new CustomEvent("teleautomation:navigate", {
    detail: { view: "daily-ops", bookingId, candidateId },
  }));
}

// One entry per happening, from /api/candidates/{id}/timeline: bookings, moves,
// replacements, attendance marks, payments and the candidate's mail, newest
// first. An alert and the recruitment event it was raised for arrive as one
// entry, so a mail is never listed twice.
export function CandidateOutcomes({
  offers,
  selectedId,
  timeline,
  onEvidence,
}) {
  return (
    <section className="sot-content-card">
      <header>
        <div>
          <h2>Candidate history</h2>
          <p>Bookings, moves, attendance, payments and mail, newest first.</p>
        </div>
      </header>
      <div className="sot-candidate-details">
        <div>
          <h3>Candidate timeline</h3>
          {timeline.length ? (
            <ol className="sot-timeline">
              {timeline.map((entry) => (
                <li
                  key={`${entry.kind}:${entry.booking_id || entry.mail_id || entry.event_id || ""}:${entry.at}`}
                  data-kind={entry.kind}
                >
                  <time>{formatTime(entry.at)}</time>
                  <strong>{entry.title}</strong>
                  {entry.detail && <span>{entry.detail}</span>}
                  <small>{entry.source}</small>
                  {(entry.event_id || entry.booking_id) && (
                    <div className="sot-timeline__actions">
                      {entry.event_id && (
                        <button type="button" onClick={() => onEvidence(entry.event_id)}>
                          View evidence
                        </button>
                      )}
                      {entry.booking_id && (
                        <button type="button" onClick={() => openBooking(entry.booking_id, selectedId)}>
                          View booking
                        </button>
                      )}
                    </div>
                  )}
                </li>
              ))}
            </ol>
          ) : (
            <p className="sot-empty">Nothing recorded for this candidate yet.</p>
          )}
        </div>
        <div>
          <h3>Offer cases</h3>
          {offers
            .filter((offer) => offer.candidate_id === selectedId)
            .map((offer) => (
              <article className="sot-offer-case" key={offer.id}>
                <strong>{offer.company_name || "Unknown company"}</strong>
                <span>{offer.job_title || "Unknown role"}</span>
                <small>
                  {human(offer.verification_status)} ·{" "}
                  {Math.round(Number(offer.confidence) * 100)}%
                </small>
                {offer.verification_status === "PENDING_REVIEW" && (
                  <small>Automatic verification pending</small>
                )}
              </article>
            ))}
        </div>
      </div>
    </section>
  );
}

function Analytics({
  charts,
  flags,
  names,
  aiStatus,
  onConnectionTest,
  onModelTest,
  busy,
}) {
  return (
    <section className="sot-analytics">
      {[
        ["Events by day", charts.events_by_day || [], "day"],
        ["Status distribution", charts.status_distribution || [], "status"],
      ].map(([title, rows, key]) => (
        <article className="sot-content-card" key={title}>
          <h2>{title}</h2>
          {rows.length ? (
            rows.map((row) => (
              <div className="sot-bar" key={row[key]}>
                <span>{human(row[key])}</span>
                <i
                  style={{ width: `${Math.min(100, Number(row.count) * 10)}%` }}
                />
                <strong>{row.count}</strong>
              </div>
            ))
          ) : (
            <p className="sot-empty">No data yet.</p>
          )}
        </article>
      ))}
      <article className="sot-content-card">
        <h2>Conflicts and duplicates</h2>
        {flags.length ? (
          flags.map((flag) => (
            <p key={flag.id}>
              <strong>{human(flag.flag_type)}</strong> ·{" "}
              {names[flag.candidate_id] || flag.candidate_id}
            </p>
          ))
        ) : (
          <p className="sot-empty">No pending risk flags.</p>
        )}
      </article>
      <article className="sot-content-card sot-ai-diagnostics">
        <h2>AI Diagnostics</h2>
        <p>Local Ollama health for selection and offer validation.</p>
        <dl>
          <div>
            <dt>Status</dt>
            <dd>{human(aiStatus?.status || "not checked")}</dd>
          </div>
          <div>
            <dt>Configured model</dt>
            <dd>{aiStatus?.configured_model || "Not configured"}</dd>
          </div>
          <div>
            <dt>Model available</dt>
            <dd>{aiStatus?.model_available ? "Yes" : "No"}</dd>
          </div>
          <div>
            <dt>Last check</dt>
            <dd>{formatTime(aiStatus?.last_checked_at)}</dd>
          </div>
          <div>
            <dt>Response time</dt>
            <dd>
              {aiStatus?.response_time_ms == null
                ? "Unknown"
                : `${aiStatus.response_time_ms} ms`}
            </dd>
          </div>
          <div>
            <dt>Last success</dt>
            <dd>{formatTime(aiStatus?.last_successful_request_at)}</dd>
          </div>
          <div>
            <dt>Average response</dt>
            <dd>
              {aiStatus?.average_response_time_ms == null
                ? "Unknown"
                : `${aiStatus.average_response_time_ms} ms`}
            </dd>
          </div>
          <div>
            <dt>Last error</dt>
            <dd>{aiStatus?.error_code || "None"}</dd>
          </div>
        </dl>
        {aiStatus?.error_message && (
          <p className="sot-ai-error">{aiStatus.error_message}</p>
        )}
        <div className="sot-diagnostic-actions">
          <button disabled={busy} onClick={onConnectionTest}>
            Test Connection
          </button>
          <button disabled={busy} onClick={onModelTest}>
            Test Model Response
          </button>
        </div>
      </article>
    </section>
  );
}

const EVIDENCE_LOAD_TIMEOUT_MS = 15000;

function EvidenceDrawer({ id, onClose, onChanged }) {
  // LOADING | SUCCESS | NO_EVIDENCE | AI_RETRY_PENDING | ERROR
  const [status, setStatus] = useState("LOADING");
  const [event, setEvent] = useState(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setStatus("LOADING");
    setEvent(null);
    setError("");
    const timeoutId = window.setTimeout(() => {
      if (!active) return;
      active = false;
      setStatus("ERROR");
      setError("The request took too long to respond.");
    }, EVIDENCE_LOAD_TIMEOUT_MS);
    request(`/api/ai-recruitment/events/${id}`)
      .then((body) => {
        if (!active) return;
        const row = body.event;
        if (!row) {
          setStatus("NO_EVIDENCE");
          return;
        }
        setEvent(row);
        if (describeAiStatus(row).status === "Retry Pending") {
          setStatus("AI_RETRY_PENDING");
        } else if (
          !row.evidence_summary &&
          !row.structured_result?.evidence_summary &&
          !(row.structured_result?.evidence || []).length
        ) {
          setStatus("NO_EVIDENCE");
        } else {
          setStatus("SUCCESS");
        }
      })
      .catch((err) => {
        if (!active) return;
        setStatus("ERROR");
        setError(err.message || "Unable to load detection evidence.");
      })
      .finally(() => {
        active = false;
        window.clearTimeout(timeoutId);
      });
    return () => {
      active = false;
      window.clearTimeout(timeoutId);
    };
  }, [id, attempt]);

  return (
    <aside className="sot-evidence">
      <header>
        <h2>Detection Evidence</h2>
        <button onClick={onClose}>Close</button>
      </header>
      {status === "LOADING" && (
        <div className="sot-evidence-status">
          <InlineLoader label="Loading detection evidence…" />
        </div>
      )}
      {status === "ERROR" && (
        <div className="sot-evidence-status">
          <p>Unable to load detection evidence.</p>
          {error && <small>{error}</small>}
          <button
            className="sot-primary-button"
            onClick={() => setAttempt((value) => value + 1)}
          >
            Retry
          </button>
        </div>
      )}
      {status === "NO_EVIDENCE" && (
        <p className="sot-evidence-status">
          No detection evidence is available for this record.
        </p>
      )}
      {status === "AI_RETRY_PENDING" && event && (
        <>
          <h3>{event.received_email?.subject || event.subject}</h3>
          <p>
            AI analysis is pending because the AI service was unavailable. The
            source email and deterministic fallback evidence are retained while
            the worker retries automatically.
          </p>
          <dl>
            <div>
              <dt>Classification</dt>
              <dd>{human(event.primary_status)} (fallback)</dd>
            </div>
            <div>
              <dt>AI Status</dt>
              <dd>Retry Pending</dd>
            </div>
            <div>
              <dt>Reason</dt>
              <dd>{aiFailureReason(event)}</dd>
            </div>
            <div>
              <dt>Candidate Status</dt>
              <dd>Unchanged</dd>
            </div>
            <div>
              <dt>Interview Schedule</dt>
              <dd>
                {[
                  event.structured_result?.interview?.date,
                  event.structured_result?.interview?.time,
                  event.structured_result?.interview?.timezone,
                ]
                  .filter(Boolean)
                  .join(" · ") || "Not extracted"}
              </dd>
            </div>
          </dl>
          <p>
            {event.evidence_summary ||
              event.structured_result?.evidence_summary ||
              "Fallback evidence will be re-evaluated automatically."}
          </p>
          <ul>
            {(event.structured_result?.evidence || []).map((item, index) => (
              <li key={index}>
                <strong>{human(item.meaning)}</strong>
                <span>{item.text}</span>
              </li>
            ))}
          </ul>
          <section className="sot-full-email">
            <h4>Complete email</h4>
            <dl>
              <div>
                <dt>From</dt>
                <dd>
                  {formatEmailAddress(
                    event.received_email?.sender_name || event.sender_name,
                    event.received_email?.sender_email || event.sender_email,
                  )}
                </dd>
              </div>
              <div>
                <dt>To</dt>
                <dd>
                  {event.received_email?.recipient_email ||
                    event.recipient_email ||
                    "Unknown"}
                </dd>
              </div>
              <div>
                <dt>Received</dt>
                <dd>
                  {formatEmailDate(
                    event.received_email?.sent_at ||
                      event.email_sent_at ||
                      event.created_at,
                  )}
                </dd>
              </div>
            </dl>
            <pre>
              {formatEmailBody(
                event.received_email?.body || event.email_body,
              ) || "Email body is not available."}
            </pre>
          </section>
          <button className="sot-primary-button" onClick={onChanged}>
            Refresh record
          </button>
        </>
      )}
      {status === "SUCCESS" && event && (
        <>
          <h3>{event.received_email?.subject || event.subject}</h3>
          <p>
            {isManuallyApproved(event) && aiFailureCode(event)
              ? "This record was manually approved from the complete source email. The earlier AI timeout is retained only in audit history."
              : event.summary}
          </p>
          <dl>
            <div>
              <dt>Email intent</dt>
              <dd>
                {human(
                  event.email_intent ||
                    event.structured_result?.email_intent ||
                    "Unknown",
                )}
              </dd>
            </div>
            <div>
              <dt>Document</dt>
              <dd>
                {human(
                  event.document_type ||
                    event.structured_result?.document_type ||
                    "None",
                )}
              </dd>
            </div>
            <div>
              <dt>Lifecycle event</dt>
              <dd>
                {human(
                  (String(
                    event.structured_result?.lifecycle_event || "",
                  ).toUpperCase() === "NONE"
                    ? ""
                    : event.structured_result?.lifecycle_event) ||
                    event.primary_status,
                )}
              </dd>
            </div>
            <div>
              <dt>Validation</dt>
              <dd>
                {describeAiStatus(event).status}
                {describeAiStatus(event).reason
                  ? ` · ${describeAiStatus(event).reason}`
                  : ""}
              </dd>
            </div>
            <div>
              <dt>Sender</dt>
              <dd>
                {formatEmailAddress(
                  event.received_email?.sender_name || event.sender_name,
                  event.received_email?.sender_email || event.sender_email,
                )}
              </dd>
            </div>
            <div>
              <dt>{isManuallyApproved(event) ? "Legacy method" : "Model"}</dt>
              <dd>
                {isManuallyApproved(event)
                  ? "Legacy approval"
                  : isManualAuditKeep(event)
                    ? "Manual operator audit"
                    : event.ai_model}
              </dd>
            </div>
          </dl>
          <p>
            {isManuallyApproved(event) && aiFailureCode(event)
              ? "Source email evidence was reviewed and approved."
              : event.evidence_summary ||
                event.structured_result?.evidence_summary}
          </p>
          <ul>
            {(event.structured_result?.evidence || []).map((item, index) => (
              <li key={index}>
                <strong>{human(item.meaning)}</strong>
                <span>{item.text}</span>
              </li>
            ))}
          </ul>
          <section className="sot-full-email">
            <h4>Complete email</h4>
            {event.received_email?.subject !== event.subject && (
              <p className="sot-full-email-note">
                Showing the original received message from this Gmail thread.
              </p>
            )}
            <dl>
              <div>
                <dt>From</dt>
                <dd>
                  {formatEmailAddress(
                    event.received_email?.sender_name || event.sender_name,
                    event.received_email?.sender_email || event.sender_email,
                  )}
                </dd>
              </div>
              <div>
                <dt>To</dt>
                <dd>
                  {event.received_email?.recipient_email ||
                    event.recipient_email ||
                    "Unknown"}
                </dd>
              </div>
              <div>
                <dt>Received</dt>
                <dd>
                  {formatEmailDate(
                    event.received_email?.sent_at ||
                      event.email_sent_at ||
                      event.created_at,
                  )}
                </dd>
              </div>
            </dl>
            <pre>
              {formatEmailBody(
                event.received_email?.body || event.email_body,
              ) || "Email body is not available."}
            </pre>
          </section>
          <button className="sot-primary-button" onClick={onChanged}>
            Refresh record
          </button>
        </>
      )}
    </aside>
  );
}

function AiNodeManager({
  nodes,
  activity = {},
  busy,
  onMakePrimary,
  onUnload,
}) {
  // Open on every load. It was collapsed to keep the mailbox list above the
  // fold -- the grid is three cards tall and sits between the header and the
  // list -- but node health is read often enough here that hiding it behind a
  // click cost more than the scroll does. Still a disclosure: one click
  // collapses it for the rest of the visit.
  const online = nodes.filter((node) => node.endpoint_reachable).length;
  const primary = nodes.find((node) => node.primary);
  return (
    <details className="sot-ai-nodes" aria-label="Ollama AI nodes" open>
      <summary>
        <strong>AI nodes</strong>
        <span
          className={`sot-ai-nodes-glance${online < nodes.length ? " is-degraded" : ""}`}
        >
          {nodes.length
            ? `${online}/${nodes.length} online${primary ? ` · ${primary.label}` : ""}`
            : "none configured"}
        </span>
      </summary>
      <div className="sot-ai-node-grid">
        {nodes.length ? (
          nodes.map((node) => {
            // What the gateway says this node is running right now -- the
            // record the booking page names its node from, so the two agree.
            const serving = [
              ...new Set(
                (activity[node.id] || [])
                  .map((item) => item.label)
                  .filter(Boolean),
              ),
            ];
            return (
              <article
                className={`sot-ai-node is-${node.status || "offline"}`}
                key={node.id}
              >
                {/* One row per node, and only what an operator acts on. The host
                  * URL, Ollama version, acceleration split and last success and
                  * failure timestamps were diagnostics that pushed the three
                  * nodes into a tall stack; they are still on the health
                  * endpoint for anyone debugging. */}
                <span className="sot-ai-node-name">
                  <i aria-hidden="true" />
                  <strong>{node.label}</strong>
                  {node.primary && (
                    <span className="sot-ai-node-primary">PRIMARY</span>
                  )}
                </span>
                <span
                  className={`sot-ai-node-state is-${
                    node.endpoint_reachable ? "up" : "down"
                  }`}
                >
                  {node.endpoint_reachable ? "Online" : "Offline"}
                </span>
                <span className="sot-ai-node-state">
                  {/* Busy and Ready are different facts -- a model held in memory
                    * versus every required model installed -- so they are shown
                    * as one state rather than one label meaning either. A request
                    * running on the node says what it is busy with. */}
                  {serving.length
                    ? `Busy · ${serving.join(" + ")}`
                    : node.model_loaded
                      ? "Busy"
                      : node.ready
                        ? "Ready"
                        : "Not ready"}
                </span>
                <span className="sot-ai-node-latency">
                  {node.response_time_ms == null
                    ? "—"
                    : `${node.response_time_ms} ms`}
                </span>
                <span className="sot-ai-node-actions">
                  {!node.primary && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => onMakePrimary(node)}
                      title={
                        node.ready
                          ? `Route AI work to ${node.label} for one hour`
                          : "This node is failing its model check — you will be asked to confirm"
                      }
                    >
                      {node.ready ? "Set primary" : "Set primary anyway"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="is-warning"
                    disabled={busy || !node.endpoint_reachable}
                    onClick={() => onUnload(node)}
                    title={`Unload AI models on ${node.label}`}
                  >
                    Unload
                  </button>
                </span>
              </article>
            );
          })
        ) : (
          <p className="sot-empty">Node health has not loaded yet.</p>
        )}
      </div>
    </details>
  );
}

const INTERVIEW_CLASSIFICATIONS = new Set([
  "interview_confirmed",
  "interview_rescheduled",
  "interview_cancelled",
]);

function JourneyFlow({ steps, tone = "blue" }) {
  return (
    <ol className={`sot-journey-flow is-${tone}`}>
      {steps.map((step, index) => (
        <li key={step.title}>
          <span className="sot-journey-index">{index + 1}</span>
          <div>
            <strong>{step.title}</strong>
            <small>{step.detail}</small>
          </div>
        </li>
      ))}
    </ol>
  );
}

function MonitoringOverview({
  metrics,
  mailboxRows,
  interviewSummary,
  aiStatus,
  onOpen,
}) {
  const activeMailboxes = mailboxRows.filter((row) =>
    ["CONNECTED", "SYNCING", "SYNC_QUEUED"].includes(row.uiStatus),
  ).length;
  return (
    <section className="sot-monitoring-overview">
      <div className="sot-command-strip">
        <article>
          <span>01</span>
          <div>
            <strong>Gmail monitoring</strong>
            <small>{activeMailboxes} accounts actively watched</small>
          </div>
        </article>
        <i aria-hidden="true">&rarr;</i>
        <article>
          <span>02</span>
          <div>
            <strong>Relevance filter</strong>
            <small>Noise is removed before lifecycle analysis</small>
          </div>
        </article>
        <i aria-hidden="true">&rarr;</i>
        <article>
          <span>03</span>
          <div>
            <strong>AI validation</strong>
            <small>
              {aiStatus?.status === "healthy"
                ? "qwen2.5:7b is ready"
                : "Failures retry automatically"}
            </small>
          </div>
        </article>
        <i aria-hidden="true">&rarr;</i>
        <article>
          <span>04</span>
          <div>
            <strong>Outcome router</strong>
            <small>Selection and interview flows separate here</small>
          </div>
        </article>
      </div>

      <div className="sot-journey-grid">
        <article className="sot-journey-card is-selection">
          <header>
            <div>
              <span className="sot-journey-kicker">CAREER OUTCOMES</span>
              <h2>Selection &amp; Offer flow</h2>
            </div>
            <strong className="sot-journey-total">
              {(metrics.selected || 0) +
                (metrics.offers_received || 0) +
                (metrics.joining_confirmed || 0)}
            </strong>
          </header>
          <p>
            Tracks positive employment outcomes and advances the candidate
            lifecycle only after validation.
          </p>
          <JourneyFlow
            tone="blue"
            steps={[
              {
                title: "Selected",
                detail: "Candidate-specific selection evidence",
              },
              {
                title: "Offer received",
                detail: "Offer or appointment letter detected",
              },
              {
                title: "Offer accepted",
                detail: "Acceptance confirmed by source email",
              },
              {
                title: "Joining",
                detail: "Joining date and onboarding monitored",
              },
            ]}
          />
          <button type="button" onClick={() => onOpen("selection")}>
            Open Selection &amp; Offers <span>&rarr;</span>
          </button>
        </article>

        <article className="sot-journey-card is-interview">
          <header>
            <div>
              <span className="sot-journey-kicker">SCHEDULE OPERATIONS</span>
              <h2>Interview monitoring flow</h2>
            </div>
            <strong className="sot-journey-total">
              {interviewSummary.auto_booked_interviews || 0}
            </strong>
          </header>
          <p>
            Extracts confirmed interview schedules and books only when every
            safety check succeeds.
          </p>
          <JourneyFlow
            tone="violet"
            steps={[
              {
                title: "Interview detected",
                detail: "Date, time, timezone and round extracted",
              },
              {
                title: "Safety validation",
                detail: "Candidate, payment, duplicate and conflict checks",
              },
              {
                title: "Slot booked",
                detail: "Confirmed slot appears in Daily Ops",
              },
              {
                title: "Attendance",
                detail: "Upcoming, attended or missed status recorded",
              },
            ]}
          />
          <button type="button" onClick={() => onOpen("interviews")}>
            Open Interview Monitoring <span>&rarr;</span>
          </button>
        </article>
      </div>

      <aside className="sot-review-rule">
        <span className="sot-review-rule-icon">!</span>
        <div>
          <strong>Fail-safe automation</strong>
          <small>
            Low confidence, model timeout, missing schedule, payment block or
            a deterministic booking block remains visible and is retried
            automatically; it never needs an approval decision.
          </small>
        </div>
        <button type="button" onClick={() => onOpen("reviews")}>
          {metrics.automation_pending ?? 0} retrying
        </button>
      </aside>
    </section>
  );
}

function InterviewWorkspace({ notifications, summary, onOpenMail }) {
  const today = new Date().toISOString().slice(0, 10);
  const upcoming = notifications.filter(
    (item) =>
      String(item.interview_date || "") >= today &&
      !["Cancelled", "Blocked", "Processing Failed"].includes(
        item.booking_status,
      ),
  ).length;
  const blocked = notifications.filter((item) =>
    ["Blocked", "Processing Failed"].includes(item.booking_status),
  ).length;
  return (
    <section className="sot-workspace sot-interview-workspace">
      <header className="sot-workspace-head">
        <div>
          <span className="sot-workspace-eyebrow">INTERVIEW OPERATIONS</span>
          <h2>Interview Monitoring</h2>
          <p>From a confirmed email schedule to a safe Daily Ops booking.</p>
        </div>
      </header>
      <div className="sot-interview-metrics">
        <MailboxMetric
          icon="AI"
          label="Detected alerts"
          value={notifications.length}
        />
        <MailboxMetric
          icon="OK"
          label="Auto-booked"
          value={summary.auto_booked_interviews || 0}
          tone="green"
        />
        <MailboxMetric
          icon="UP"
          label="Upcoming"
          value={upcoming}
          tone="blue"
        />
        <MailboxMetric
          icon="!"
          label="Blocked / failed"
          value={blocked}
          tone="amber"
        />
      </div>
      <JourneyFlow
        tone="violet"
        steps={[
          {
            title: "Confirmed email",
            detail: "Not a generic invite or recruiter campaign",
          },
          {
            title: "Structured schedule",
            detail: "Future date, 12-hour time and timezone required",
          },
          {
            title: "Operations checks",
            detail: "Owner, payment, duplicate and overlap validation",
          },
          {
            title: "Daily Ops slot",
            detail: "Booking, reschedule or cancellation is audited",
          },
        ]}
      />
      <div className="sot-interview-list">
        <div className="sot-section-title">
          <div>
            <h3>Recent interview activity</h3>
            <p>
              Select any row to open the complete source mail and its detection
              evidence.
            </p>
          </div>
          <span>{notifications.length} records</span>
        </div>
        {notifications.length ? (
          <div className="sot-table-shell">
            <table className="sot-interview-table">
              <thead>
                <tr>
                  <th>Candidate</th>
                  <th>Interview</th>
                  <th>Company / role</th>
                  <th>AI confidence</th>
                  <th>Booking result</th>
                </tr>
              </thead>
              <tbody>
                {notifications.slice(0, 20).map((item) => {
                  const eventId = item.ai_recruitment_event_id;
                  const openMail = () => eventId && onOpenMail(eventId);
                  return (
                    <tr
                      key={item.id}
                      className={eventId ? "is-clickable" : ""}
                      tabIndex={eventId ? 0 : undefined}
                      aria-label={
                        eventId
                          ? `Open source mail: ${item.email_subject || item.candidate_name || "interview notification"}`
                          : undefined
                      }
                      onClick={openMail}
                      onKeyDown={(keyboardEvent) => {
                        if (
                          eventId &&
                          (keyboardEvent.key === "Enter" ||
                            keyboardEvent.key === " ")
                        ) {
                          keyboardEvent.preventDefault();
                          openMail();
                        }
                      }}
                    >
                      <td>
                        <strong>{item.candidate_name || "Candidate"}</strong>
                        <small>{item.email_subject || "No subject"}</small>
                      </td>
                      <td>
                        <strong>
                          {item.interview_date || "Date unavailable"}
                        </strong>
                        <small>
                          {[
                            item.interview_time,
                            item.interview_timezone,
                            item.interview_round,
                          ]
                            .filter(Boolean)
                            .join(" · ") || "Schedule incomplete"}
                        </small>
                      </td>
                      <td>
                        {item.company_name || "Unknown company"}
                        <small>
                          {item.job_role || human(item.classification)}
                        </small>
                      </td>
                      <td>
                        {Math.round(Number(item.ai_confidence || 0) * 100)}%
                      </td>
                      <td>
                        <span
                          className={`sot-booking-state is-${String(
                            item.booking_status || "detected",
                          )
                            .toLowerCase()
                            .replace(/\s+/g, "-")}`}
                        >
                          {item.booking_status || human(item.classification)}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="sot-empty-state">
            <strong>No interview notifications yet</strong>
            <span>
              Validated interview emails and booking results will appear here.
            </span>
          </div>
        )}
      </div>
    </section>
  );
}

export default function RecruitmentMailPanelRedesign() {
  const today = new Date().toISOString().slice(0, 10);
  const { confirm } = useConfirm();
  // This page is intentionally mailbox-only. Career review and interview
  // operations live in their dedicated pages.
  const [tab, setTab] = useState("mailboxes");
  const [metrics, setMetrics] = useState({
    automation_pending: 0,
    selected: 0,
    offers_received: 0,
    offers_accepted: 0,
    joining_confirmed: 0,
    joined: 0,
  });
  const [charts, setCharts] = useState({});
  const [flags, setFlags] = useState([]);
  const [events, setEvents] = useState([]);
  const [offers, setOffers] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [mailboxes, setMailboxes] = useState([]);
  const [monitoringSummary, setMonitoringSummary] = useState({
    auto_booked_interviews: 0,
    automation_pending: 0,
    unread: 0,
  });
  const [interviewNotifications, setInterviewNotifications] = useState([]);
  const [candidateId, setCandidateId] = useState("");
  const [reviewStatusFilter, setReviewStatusFilter] = useState("");
  const [timeline, setTimeline] = useState([]);
  const [search, setSearch] = useState("");
  const [mailboxListMode, setMailboxListMode] = useState("linked");
  const [showAddMailbox, setShowAddMailbox] = useState(false);
  const [newMailboxCandidateId, setNewMailboxCandidateId] = useState("");
  const [newMailboxEmail, setNewMailboxEmail] = useState("");
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [evidenceId, setEvidenceId] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);
  const [aiStatus, setAiStatus] = useState(null);
  const [aiNodes, setAiNodes] = useState([]);
  // Which request each node is serving, from the gateway: the nodes endpoint
  // carries a snapshot and the live socket pushes every change. A snapshot from
  // before a restart has a different boot id, so the counter starting again
  // from zero cannot make a fresh one look old.
  const [aiActivity, setAiActivity] = useState({ boot: "", version: -1, nodes: {} });
  const acceptAiActivity = useCallback((snapshot) => {
    if (!snapshot || typeof snapshot.version !== "number") return;
    setAiActivity((current) =>
      snapshot.boot !== current.boot || snapshot.version >= current.version
        ? { boot: snapshot.boot, version: snapshot.version, nodes: snapshot.nodes || {} }
        : current,
    );
  }, []);
  const [refreshingAi, setRefreshingAi] = useState(false);
  const loadInFlight = useRef(null);
  const activeSyncRefreshInFlight = useRef(false);

  const load = useCallback(async ({ showLoader = false } = {}) => {
    if (loadInFlight.current) return loadInFlight.current;
    if (showLoader) setLoading(true);
    const operation = (async () => {
      try {
      setMessage("");
      const [people, mailboxOverview] = await Promise.all([
        request("/candidates?limit=500"),
        request("/api/candidate-mailboxes/overview").catch(() => ({
          mailboxes: [],
        })),
      ]);
      const candidateList = people.candidates || [];
      const candidatesById = new Map(
        candidateList.map((candidate) => [String(candidate.id), candidate]),
      );
      const mailboxRows = (mailboxOverview.mailboxes || [])
        .map((entry) => {
          const mailbox = entry.mailbox || entry;
          // Mailboxes connected before duplicate candidate rows were merged
          // can retain the legacy row id.  The API exposes the canonical id so
          // those valid Gmail accounts remain visible in the current roster.
          const candidate =
            candidatesById.get(String(mailbox.canonical_candidate_id || "")) ||
            candidatesById.get(String(mailbox.candidate_id));
          return candidate
            ? { candidate, mailbox, stats: entry.stats || {} }
            : null;
        })
        .filter(Boolean);
      setCandidates(candidateList);
      setMailboxes(mailboxRows);
      setUpdatedAt(new Date());
      } catch (error) {
        setMessage(error.message);
      } finally {
        setLoading(false);
      }
    })();
    loadInFlight.current = operation;
    try {
      return await operation;
    } finally {
      loadInFlight.current = null;
    }
  }, []);

  useEffect(() => {
    load({ showLoader: true });
  }, [load]);
  useEffect(() => {
    let stopped = false;
    const refreshMailboxHealth = async () => {
      try {
        const body = await request(`/api/candidate-mailboxes/health?_=${Date.now()}`);
        if (stopped) return;
        const healthById = new Map(
          (body.mailboxes || []).map((mailbox) => [String(mailbox.id), mailbox]),
        );
        setMailboxes((current) =>
          current.map((row) => {
            const health = healthById.get(String(row.mailbox.id));
            return health ? { ...row, mailbox: { ...row.mailbox, ...health } } : row;
          }),
        );
      } catch {
        /* The full page load remains the fallback when health polling fails. */
      }
    };
    const timer = window.setInterval(refreshMailboxHealth, 30000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, []);
  const refreshOllama = useCallback(async (interactive = true) => {
    if (interactive) setRefreshingAi(true);
    try {
      const [statusResult, nodesResult] = await Promise.allSettled([
        request(`/api/ai-recruitment/ollama/status?_=${Date.now()}`),
        request(`/api/ai-recruitment/ollama/nodes?_=${Date.now()}`),
      ]);
      if (statusResult.status === "fulfilled") {
        setAiStatus(statusResult.value.ollama || null);
        setUpdatedAt(
          new Date(statusResult.value.ollama?.last_checked_at || Date.now()),
        );
      }
      if (nodesResult.status === "fulfilled") {
        setAiNodes(nodesResult.value.nodes || []);
        acceptAiActivity(nodesResult.value.activity);
      }
      if (
        statusResult.status === "rejected" &&
        nodesResult.status === "rejected"
      ) {
        throw statusResult.reason;
      }
    } catch (error) {
      setAiStatus({
        status: "unavailable",
        diagnostic_status: "INTERNAL_ERROR",
        error_code: "DIAGNOSTICS_REQUEST_FAILED",
        error_message: error.message,
      });
      setUpdatedAt(new Date());
    } finally {
      if (interactive) setRefreshingAi(false);
    }
  }, [acceptAiActivity]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      refreshOllama(false);
    }, 60000);
    return () => window.clearInterval(timer);
  }, [refreshOllama]);
  useEffect(() => subscribeNodeActivity(acceptAiActivity), [acceptAiActivity]);
  useEffect(
    () =>
      // Pushes sent while the socket was down are not replayed, so read the
      // current snapshot each time it comes back.
      subscribeMailStatus((status) => {
        if (status !== "Live") return;
        request(`/api/ai-recruitment/ollama/activity?_=${Date.now()}`)
          .then((body) => acceptAiActivity(body.activity))
          .catch(() => {
            /* the minute-by-minute node refresh carries it too */
          });
      }),
    [acceptAiActivity],
  );
  useEffect(() => {
    if (!loading) refreshOllama(false);
  }, [loading, refreshOllama]);
  const activeSyncSignature = mailboxes
    .filter((row) =>
      ["QUEUED", "RUNNING"].includes(
        String(row.stats.latest_sync_status || "").toUpperCase(),
      ),
    )
    .map((row) => `${row.mailbox.id}:${row.stats.latest_sync_status}`)
    .join("|");
  useEffect(() => {
    if (!activeSyncSignature) return undefined;
    const refreshActiveSyncs = async () => {
      if (activeSyncRefreshInFlight.current) return;
      activeSyncRefreshInFlight.current = true;
      try {
        const body = await request(
          `/api/candidate-mailboxes/overview?_=${Date.now()}`,
        );
        const overviewById = new Map(
          (body.mailboxes || []).map((entry) => [
            String((entry.mailbox || entry).id),
            entry,
          ]),
        );
        setMailboxes((current) =>
          current.map((row) => {
            const entry = overviewById.get(String(row.mailbox.id));
            return entry
              ? {
                  ...row,
                  mailbox: { ...row.mailbox, ...(entry.mailbox || entry) },
                  stats: entry.stats || {},
                }
              : row;
          }),
        );
      } catch {
        /* The normal refresh button remains available when polling fails. */
      } finally {
        activeSyncRefreshInFlight.current = false;
      }
    };
    const timer = window.setInterval(refreshActiveSyncs, 3000);
    return () => window.clearInterval(timer);
  }, [activeSyncSignature]);
  useEffect(() => {
    if (!candidateId) {
      setTimeline([]);
      return;
    }
    request(`/api/candidates/${candidateId}/timeline`)
      .then((body) => setTimeline(body.entries || []))
      .catch((error) => setMessage(error.message));
  }, [candidateId]);

  const run = async (action, feedback = {}) => {
    setBusy(true);
    setMessage("");
    setNotice(feedback.started || "");
    try {
      const result = await action();
      await load();
      setNotice(
        typeof feedback.success === "function"
          ? feedback.success(result)
          : feedback.success || "",
      );
      return result;
    } catch (error) {
      setNotice("");
      setMessage(error.message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  const reconnect = (row) =>
    run(async () => {
      const redirect_uri = `${window.location.origin}/api/candidate-mailboxes/oauth/google/callback`;
      const result = await request(
        `/api/candidates/${row.candidate.id}/mailbox/connect`,
        {
          method: "POST",
          body: JSON.stringify({
            email_address: row.mailbox.email_address,
            redirect_uri,
          }),
        },
      );
      window.location.assign(result.authorization_url);
    });
  const connectNewMailbox = (event) => {
    event.preventDefault();
    const email = newMailboxEmail.trim().toLowerCase();
    if (!newMailboxCandidateId || !email) return;
    run(async () => {
      const redirect_uri = `${window.location.origin}/api/candidate-mailboxes/oauth/google/callback`;
      await request(`/candidates/${newMailboxCandidateId}`, {
        method: "PATCH",
        body: JSON.stringify({ email }),
      });
      const result = await request(
        `/api/candidates/${newMailboxCandidateId}/mailbox/connect`,
        {
          method: "POST",
          body: JSON.stringify({ email_address: email, redirect_uri }),
        },
      );
      if (!result.authorization_url)
        throw new Error("Google authorization could not be started");
      window.location.assign(result.authorization_url);
    });
  };
  const selectNewMailboxCandidate = (candidateId) => {
    setNewMailboxCandidateId(candidateId);
    const candidate = candidates.find(
      (row) => String(row.id) === String(candidateId),
    );
    // Check if this candidate already has mailboxes — if so, clear the field
    // so the user must type the new address rather than accidentally re-adding the same one
    const alreadyHasMailbox = mailboxes.some(
      (row) => String(row.candidate.id) === String(candidateId),
    );
    if (alreadyHasMailbox) {
      setNewMailboxEmail("");
    } else {
      setNewMailboxEmail(
        String(
          candidate?.email ||
            candidate?.email_address ||
            candidate?.gmail_address ||
            candidate?.candidate_email ||
            "",
        )
          .trim()
          .toLowerCase(),
      );
    }
  };
  const startPendingMailboxConnection = (candidate) => {
    selectNewMailboxCandidate(candidate.id);
    setShowAddMailbox(true);
  };
  const disconnect = async (row) => {
    const ok = await confirm({
      title: "Disconnect Gmail?",
      message:
        "Monitoring will stop and the stored OAuth credential will be removed.",
      confirmLabel: "Disconnect",
      variant: "danger",
    });
    if (ok)
      run(
        () =>
          request(
            `/api/candidates/${row.candidate.id}/mailbox?mailbox_id=${encodeURIComponent(row.mailbox.id)}`,
            {
              method: "DELETE",
            },
          ),
        {
          started: `Disconnecting ${row.candidate.name}'s Gmail (${row.mailbox.email_address})…`,
          success: `${row.candidate.name}'s Gmail (${row.mailbox.email_address}) was disconnected.`,
        },
      );
  };
  const mailboxAction = (action, row) => {
    if (action === "reconnect") return reconnect(row);
    if (action === "disconnect") return disconnect(row);
    if (action === "verify")
      return run(
        () =>
          request(`/api/candidates/${row.candidate.id}/mailbox/verify`, {
            method: "POST",
            body: JSON.stringify({ mailbox_id: row.mailbox.id }),
          }),
        {
          started: `Verifying ${row.candidate.name}'s Gmail connection…`,
          success: `${row.candidate.name}'s Gmail connection is verified and healthy.`,
        },
      );
    if (action === "sync")
      return run(
        () =>
          request(`/api/candidates/${row.candidate.id}/mailbox/sync`, {
            method: "POST",
            body: JSON.stringify({ mailbox_id: row.mailbox.id }),
          }),
        {
          started: `Requesting a mailbox sync for ${row.candidate.name}…`,
          success: `${row.candidate.name}'s mailbox sync is queued. Progress will update automatically.`,
        },
      );
    return run(
      () =>
        request(`/api/candidates/${row.candidate.id}/mailbox/settings`, {
          method: "PATCH",
          body: JSON.stringify({
            mailbox_id: row.mailbox.id,
            monitoring_enabled: action === "resume",
          }),
        }),
      {
        started:
          action === "resume"
            ? `Starting monitoring for ${row.candidate.name}…`
            : `Pausing monitoring for ${row.candidate.name}…`,
        success:
          action === "resume"
            ? `Monitoring is active for ${row.candidate.name}.`
            : `Monitoring is paused for ${row.candidate.name}.`,
      },
    );
  };

  const allRows = useMemo(
    () =>
      mailboxes.map((row) => {
        const syncStatus = String(
          row.stats.latest_sync_status || "",
        ).toUpperCase();
        // Connection state is decided before sync-job state; see
        // mailboxUiStatus for why that order matters.
        const uiStatus = mailboxUiStatus(row.mailbox, syncStatus);
        return { ...row, uiStatus };
      }),
    [mailboxes],
  );
  // The Gmail reconnect fault alert moved to GlobalNotificationSounds, which
  // polls mailbox health for the whole session. An expired token used to be
  // silent unless this page happened to be open — which is exactly when nobody
  // was looking at it.
  const rows = useMemo(
    () =>
      candidateId
        ? allRows.filter(
            (row) => String(row.candidate.id) === String(candidateId),
          )
        : allRows,
    [allRows, candidateId],
  );
  const visibleInterviewNotifications = useMemo(
    () =>
      candidateId
        ? interviewNotifications.filter(
            (notification) =>
              String(notification.candidate_id) === String(candidateId),
          )
        : interviewNotifications,
    [candidateId, interviewNotifications],
  );
  const visibleRows = rows.filter((row) => {
    const needle = search.trim().toLowerCase();
    const matchesSearch =
      !needle ||
      `${row.candidate.name} ${row.candidate.phone || ""} ${row.mailbox.email_address}`
        .toLowerCase()
        .includes(needle);
    return matchesSearch;
  });
  const activeMailboxCount = rows.filter((row) =>
    ["CONNECTED", "SYNC_QUEUED", "SYNCING"].includes(row.uiStatus),
  ).length;
  // The sidebar is told about every mailbox, not just the candidate being
  // viewed, and is told on each load so reconnecting an account clears the
  // badge with the row rather than on the sidebar's own next poll.
  const globalReconnectRequired = allRows.filter(
    (row) => row.uiStatus === "RECONNECT_REQUIRED",
  ).length;
  // The tab count is the worklist's own total -- already broken plus about to
  // break -- so the number on the tab is the number of rows behind it.
  const reconnectWorklistTotal = useMemo(
    () => reconnectWorklist(
      allRows.map((row) => ({ ...(row.mailbox || {}) })),
    ).total,
    [allRows],
  );
  useEffect(() => {
    publishGmailExpired(globalReconnectRequired);
  }, [globalReconnectRequired]);
  const pendingMailboxCandidates = useMemo(() => {
    const linkedCandidateIds = new Set(
      mailboxes.map((row) => String(row.candidate.id)),
    );
    return candidates.filter((candidate) => {
      const serviceType = String(
        candidate.service_type || "profile_service",
      ).toLowerCase();
      return (
        serviceType === "profile_service" &&
        String(candidate.stage || "").toLowerCase() === "in_progress" &&
        !linkedCandidateIds.has(String(candidate.id))
      );
    });
  }, [candidates, mailboxes]);
  const visiblePendingMailboxCandidates = pendingMailboxCandidates.filter(
    (candidate) => {
      const needle = search.trim().toLowerCase();
      return (
        !needle ||
        `${candidate.name || ""} ${candidate.phone || ""} ${
          candidate.technology || ""
        } ${candidate.email || ""}`
          .toLowerCase()
          .includes(needle)
      );
    },
  );
  const availableMailboxCandidates = candidates;
  const names = Object.fromEntries(
    candidates.map((candidate) => [candidate.id, candidate.name]),
  );
  const summary = [
    {
      tone: "amber",
      icon: "△",
      value: metrics.automation_pending ?? 0,
      title: "AI Retry Pending",
      subtitle: "Retries automatically",
      group: "automation_pending",
    },
    {
      tone: "blue",
      icon: "♙",
      value: metrics.selected ?? 0,
      title: "Selected",
      subtitle: "AI-detected selections",
      group: "selected",
    },
    {
      tone: "blue",
      icon: "✉",
      value: `${metrics.offers_received ?? 0} / ${metrics.offers_accepted ?? 0}`,
      title: "Offers",
      subtitle: "Received / Accepted",
      group: "offers",
    },
    {
      tone: "green",
      icon: "♧",
      value: `${metrics.joining_confirmed ?? 0} / ${metrics.joined ?? 0}`,
      title: "Joining",
      subtitle: "Confirmed / Joined",
      group: "joining",
    },
  ];

  const prioritizedReviewEvents = useMemo(
    () =>
      [...events].sort((left, right) => {
        const pendingRank = (event) =>
          String(event.review_status || "").toUpperCase() === "PENDING" ? 0 : 1;
        const rankDifference = pendingRank(left) - pendingRank(right);
        if (rankDifference) return rankDifference;
        const leftTime = new Date(
          left.email_sent_at || left.created_at || 0,
        ).getTime();
        const rightTime = new Date(
          right.email_sent_at || right.created_at || 0,
        ).getTime();
        return rightTime - leftTime;
      }),
    [events],
  );
  const selectStatusFilter = (group) => {
    setReviewStatusFilter((current) => (current === group ? "" : group));
    setTab("reviews");
  };
  const testOllama = (kind) =>
    run(async () => {
      const result = await request(`/api/ai-recruitment/ollama/test-${kind}`, {
        method: "POST",
        body: "{}",
      });
      if (result.ollama) setAiStatus(result.ollama);
      if (result.status !== "ok")
        throw new Error(
          result.error_message ||
            result.ollama?.error_message ||
            "Ollama test failed",
        );
    });
  const makePrimaryNode = (node) =>
    run(
      async () => {
        // A node failing its model check would take every AI feature down if it
        // became primary, so the override is a deliberate answer, not a default.
        let override = false;
        if (!node.ready) {
          const proceed = window.confirm(
            `${node.label} is not passing its model health check.\n\n` +
              "Making it primary can stop payment extraction, interview " +
              "booking and AI Mail Review from working.\n\nSet it as primary anyway?",
          );
          if (!proceed) return;
          override = true;
        }
        await request(
          `/api/ai-recruitment/ollama/nodes/${node.id}/primary${override ? "?override=true" : ""}`,
          { method: "POST", body: "{}" },
        );
        await refreshOllama(false);
      },
      { success: `${node.label} is now the primary AI node.` },
    );
  const unloadNodeModels = async (node) => {
    const ok = await confirm({
      title: `Unload models on ${node.label}?`,
      message:
        "This frees GPU/RAM but does not power off the laptop. Models reload automatically on the next request.",
      confirmLabel: "Unload models",
      tone: "warning",
    });
    if (!ok) return;
    await run(
      async () => {
        await request(`/api/ai-recruitment/ollama/nodes/${node.id}/unload`, {
          method: "POST",
          body: "{}",
        });
        await refreshOllama(false);
      },
      { success: `Models unloaded from ${node.label}.` },
    );
  };

  return (
    <main className="sot-page sot-mailboxes-page">
      <header className="sot-header">
        <div className="sot-title">
          <span className="sot-brand-avatar">AD</span>
          <div>
            <span className="sot-page-eyebrow">GMAIL OPERATIONS</span>
            <h1>Candidate Mailboxes</h1>
            <p>Connect and monitor candidate Gmail accounts.</p>
          </div>
        </div>
        <div className="sot-header-actions">
          <label className="sot-global-candidate-filter">
            <select
              aria-label="Global candidate filter"
              value={candidateId}
              onChange={(event) => setCandidateId(event.target.value)}
            >
              <option value="">All candidates</option>
              {candidates.map((candidate) => (
                <option key={candidate.id} value={candidate.id}>
                  {candidate.name}
                </option>
              ))}
            </select>
          </label>
          <PureOllamaToggle />
          <OcrToggle />
          <span>
            Last updated: {updatedAt
              ? formatTime(updatedAt)
              : <InlineLoader label="Loading data…" />}
          </span>
          {/* The only Refresh on the page. It used to reload candidates and
              mailboxes while the AI panel carried a second button for node
              health, so which one an operator wanted depended on what they
              were looking at. This does both. */}
          <button
            onClick={() => {
              load({ showLoader: true });
              refreshOllama(true);
            }}
            disabled={busy || refreshingAi || loading}
          >
            <ButtonContent loading={loading} loadingLabel="Refreshing">
              ↻ Refresh
            </ButtonContent>
          </button>
        </div>
      </header>
      <AiNodeManager
        nodes={aiNodes}
        activity={aiActivity.nodes}
        busy={busy}
        onMakePrimary={makePrimaryNode}
        onUnload={unloadNodeModels}
      />
      {loading && (
        <div className="sot-page-loader">
          <OverlayLoader label="Loading candidate mailboxes…" />
        </div>
      )}
      {message && <div className="sot-alert">{message}</div>}
      {notice && (
        <div className="sot-notice" role="status">
          <span aria-hidden="true">✓</span>
          {notice}
        </div>
      )}
      {tab === "overview" && (
        <>
          <MonitoringOverview
            metrics={metrics}
            mailboxRows={rows}
            interviewSummary={monitoringSummary}
            aiStatus={aiStatus}
            onOpen={setTab}
          />
          <details className="sot-content-card sot-overview-analytics">
            <summary>Operational analytics &amp; AI diagnostics</summary>
            <p>
              Open only when you need trends, conflict checks, or AI
              troubleshooting.
            </p>
            <Analytics
              charts={charts}
              flags={flags}
              names={names}
              aiStatus={aiStatus}
              onConnectionTest={() => testOllama("connection")}
              onModelTest={() => testOllama("model")}
              busy={busy}
            />
          </details>
        </>
      )}
      {tab === "selection" && (
        <>
          <section className="sot-workspace sot-selection-workspace">
            <header className="sot-workspace-head">
              <div>
                <span className="sot-workspace-eyebrow">CAREER OUTCOMES</span>
                <h2>Selection &amp; Offer Tracking</h2>
                <p>
                  Only candidate-specific, source-supported positive outcomes
                  advance this flow.
                </p>
              </div>
              <button
                type="button"
                className="sot-secondary-button"
                onClick={() => setTab("reviews")}
              >
                Open automation activity
              </button>
            </header>
            <section className="sot-summary-grid">
              {summary.map((card) => (
                <SummaryCard
                  key={card.title}
                  {...card}
                  onClick={() => selectStatusFilter(card.group)}
                  active={reviewStatusFilter === card.group}
                />
              ))}
            </section>
            <div className="sot-selection-flow-copy">
              <div>
                <span>What enters</span>
                <strong>
                  Selection, offer, acceptance, joining and onboarding evidence
                </strong>
              </div>
              <div>
                <span>What stays out</span>
                <strong>
                  Job alerts, generic recruiter campaigns, rejections and
                  unconfirmed applications
                </strong>
              </div>
              <div>
                <span>Safety rule</span>
                <strong>
                  Unknown or unsupported outcomes retry automatically
                </strong>
              </div>
            </div>
          </section>
          {candidateId && (
            <CandidateOutcomes
              offers={offers}
              selectedId={candidateId}
              timeline={timeline}
              onEvidence={setEvidenceId}
            />
          )}
        </>
      )}
      {tab === "interviews" && (
        <InterviewWorkspace
          notifications={visibleInterviewNotifications}
          summary={monitoringSummary}
          onOpenMail={setEvidenceId}
        />
      )}
      {tab === "mailboxes" && (
        <>
          <section className="sot-content-card sot-mailbox-overview">
            {/* Search and Add Gmail used to sit here, a full row above the
                four metric cards, which pushed the list they act on further
                down the page. They now sit in the toolbar directly above the
                table -- next to what they filter and add to. */}
            <div className="sot-overview-head">
              <div>
                <h2>Candidate Gmail</h2>
                <p>
                  Link accounts and monitor important job outcomes.
                </p>
              </div>
            </div>
            {/* One row, and every count on it exactly once. The three
                filters carried the same numbers as the chips beside them
                -- Total Mailboxes was Linked, Monitoring Active was the
                note inside it, Pending Gmail was itself -- so the chips
                went and the tabs kept the counts. Active stays as a
                reading, outside the tablist because it filters nothing. */}
            <div className="sot-mailbox-toolbar">
            {/* Tabs and the controls that act on the list, on one row directly
                above it. The tablist stays its own element: search and a button
                are not tabs and must not sit inside it. */}
            <div className="sot-list-toolbar">
              <div
                className="sot-mailbox-view-tabs"
                role="tablist"
                aria-label="Mailbox list"
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={mailboxListMode === "linked"}
                  className={mailboxListMode === "linked" ? "active" : ""}
                  onClick={() => setMailboxListMode("linked")}
                >
                  <i aria-hidden="true">&#9993;</i> Linked <span>{rows.length}</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mailboxListMode === "pending"}
                  className={mailboxListMode === "pending" ? "active" : ""}
                  onClick={() => setMailboxListMode("pending")}
                >
                  <i aria-hidden="true">!</i> Pending Gmail <span>{pendingMailboxCandidates.length}</span>
                </button>
                <button
                  type="button"
                  role="tab"
                  aria-selected={mailboxListMode === "reconnect"}
                  className={mailboxListMode === "reconnect" ? "active" : ""}
                  onClick={() => setMailboxListMode("reconnect")}
                >
                  <i aria-hidden="true">&#8635;</i> Reconnect <span>{reconnectWorklistTotal}</span>
                </button>
              </div>
              {/* A reading, not a filter: there is no Active list to show,
                  so it is a <p> rather than a fourth tab that would do
                  nothing when pressed. */}
              <p className="sot-mailbox-status">
                <i aria-hidden="true" />
                Active <span>{activeMailboxCount}</span>
              </p>
              <div className="sot-list-toolbar-actions">
                <SearchInput value={search} onChange={setSearch} />
                <button
                  type="button"
                  className="sot-add-mailbox-button"
                  onClick={() => setShowAddMailbox((visible) => !visible)}
                  aria-expanded={showAddMailbox}
                >
                  {showAddMailbox ? "Cancel" : "+ Add Gmail"}
                </button>
              </div>
            </div>
            </div>
            {showAddMailbox && (
              <form
                className="sot-add-mailbox-form"
                onSubmit={connectNewMailbox}
              >
                <h3>Connect Gmail</h3>
                <label>
                  Candidate
                  <select
                    aria-label="Candidate Gmail owner"
                    value={newMailboxCandidateId}
                    onChange={(event) =>
                      selectNewMailboxCandidate(event.target.value)
                    }
                    required
                  >
                    <option value="">Select candidate</option>
                    {availableMailboxCandidates.map((candidate) => (
                      <option key={candidate.id} value={candidate.id}>
                        {candidate.name} · {candidate.phone || "no phone"}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Gmail address
                  <input
                    type="email"
                    value={newMailboxEmail}
                    onChange={(event) => setNewMailboxEmail(event.target.value)}
                    placeholder="candidate@gmail.com (or 2nd Gmail)"
                    autoComplete="email"
                    required
                  />
                </label>
                <button
                  type="submit"
                  className="sot-primary-button"
                  disabled={
                    busy || !newMailboxCandidateId || !newMailboxEmail.trim()
                  }
                >
                  {busy ? "Starting…" : "Connect Gmail"}
                </button>
              </form>
            )}
            {mailboxListMode === "reconnect" ? (
              <ReconnectWorklist
                rows={allRows}
                busy={busy}
                onAction={mailboxAction}
              />
            ) : mailboxListMode === "pending" ? (
              <PendingMailboxTable
                candidates={visiblePendingMailboxCandidates}
                busy={busy}
                onConnect={startPendingMailboxConnection}
              />
            ) : (
              <MailboxTable
                rows={visibleRows}
                busy={busy}
                onAction={mailboxAction}
              />
            )}
          </section>
        </>
      )}
      {tab === "reviews" && (
        <>
          {showAddMailbox && (
            <AddMailboxForm
              candidates={availableMailboxCandidates}
              candidateId={newMailboxCandidateId}
              email={newMailboxEmail}
              busy={busy}
              onCandidate={selectNewMailboxCandidate}
              onEmail={setNewMailboxEmail}
              onSubmit={connectNewMailbox}
            />
          )}
          <AutomationActivity
            events={prioritizedReviewEvents
              .filter((event) =>
                candidateId
                  ? String(event.candidate_id) === String(candidateId) ||
                    String(event.canonical_candidate_id) === String(candidateId)
                  : true,
              )
              .filter((event) =>
                reviewStatusFilter
                  ? (STATUS_GROUP_STATUSES[reviewStatusFilter] || []).includes(
                      event.primary_status,
                    )
                  : true,
              )}
            names={names}
            candidateId={candidateId}
            onClearCandidate={() => setCandidateId("")}
            statusFilterLabel={
              reviewStatusFilter
                ? summary.find((card) => card.group === reviewStatusFilter)
                    ?.title
                : ""
            }
            onClearStatusFilter={() => setReviewStatusFilter("")}
            onEvidence={setEvidenceId}
            onAddMailbox={() => setShowAddMailbox((visible) => !visible)}
            addMailboxOpen={showAddMailbox}
          />
        </>
      )}
      {evidenceId && (
        <EvidenceDrawer
          id={evidenceId}
          onClose={() => setEvidenceId(null)}
          onChanged={load}
        />
      )}
    </main>
  );
}
