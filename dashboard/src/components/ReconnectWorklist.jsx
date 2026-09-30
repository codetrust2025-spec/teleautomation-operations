import React, { useMemo } from "react";
import { reconnectWorklist } from "../utils/mailboxStatus.js";
import { ExpiryCountdown } from "./ExpiryCountdown.jsx";
import "./ReconnectWorklist.css";

/**
 * Every Gmail account that needs reconnecting, in one place.
 *
 * The OAuth app is in Testing mode, so Google expires refresh tokens seven days
 * after consent — measured, not assumed: across 54 consecutive reconnects the
 * median gap is 7.0 days. With nineteen connected mailboxes that is roughly
 * three reconnects a day, and until the app is published to Production there is
 * no code change that prevents them. What there can be is one screen that shows
 * them together instead of an operator finding them a banner at a time.
 *
 * Two groups, deliberately separate. Already broken is work that must happen;
 * about to break is work that can be batched with it while someone is here. The
 * split is computed once in `reconnectWorklist`, off the same rows the mailbox
 * table renders, so this list and the badges beside those accounts cannot
 * disagree.
 */
/**
 * What to print instead of the countdown, or "" to let it count.
 *
 * Google can revoke before the seven days are up -- a password change, or the
 * account holder withdrawing access -- so a broken mailbox may still have time
 * left on the clock. A countdown over an account that has already stopped
 * collecting mail would be plainly wrong, so the broken list says so instead.
 * Once the clock has run out too, the countdown's own "Expired" is the truth
 * and is left to say it.
 */
function overrideLabel(row, { alreadyExpired = false } = {}) {
  if (!alreadyExpired) return "";
  const expiresAt = row.grantExpiresAt;
  if (!Number.isFinite(expiresAt)) return "authorisation revoked";
  return expiresAt > Date.now() ? "authorisation revoked" : "";
}


/** "2 days ago", "3 h ago": when the server first saw the account broken. */
export function ago(iso, now = Date.now()) {
  const then = Date.parse(iso || "");
  if (!Number.isFinite(then)) return "";
  const minutes = Math.max(0, Math.round((now - then) / 60000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function when(iso) {
  const date = new Date(iso || "");
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("en-IN", {
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "Asia/Kolkata",
  });
}

/**
 * The reminder history of one account, in words. All of it is the server's:
 * this never works out a schedule of its own, so the screen cannot promise a
 * reminder the server is not going to send.
 */
export function reminderSummary(notice, now = Date.now()) {
  if (!notice) return "Waiting for the next check";
  const detected = ago(notice.detected_at, now);
  const told = Number(notice.notice_count) || 0;
  const parts = [detected ? `Detected ${detected}` : "Detected"];
  parts.push(told ? `team told ${told}×` : "team not told yet");
  const next = when(notice.next_notice_at);
  if (next) parts.push(`next reminder ${next} IST`);
  return parts.join(" · ");
}

function CopyLink({ link }) {
  const [copied, setCopied] = React.useState(false);
  if (!link) return null;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 2500);
    } catch {
      window.prompt("Copy this reconnect link", link);
    }
  };
  return (
    <button type="button" className="sot-reconnect-copy" onClick={copy}>
      {copied ? "Link copied" : "Copy link"}
    </button>
  );
}

function AutomationBanner({ automation }) {
  if (!automation) return null;
  if (automation.enabled === false) {
    return (
      <p className="sot-reconnect-automation is-off" role="status">
        Automatic reminders are switched off. Accounts below need a manual reconnect.
      </p>
    );
  }
  const last = when(automation.last_digest_at);
  return (
    <p className="sot-reconnect-automation" role="status">
      Reminders are automatic: the team is told about each account that needs
      reconnecting, then reminded at most twice a day until it is fixed.
      {last ? ` Last reminder ${last} IST.` : " No reminder has been sent yet."}
      {automation.resolved_7d ? ` ${automation.resolved_7d} reconnected in the last 7 days.` : ""}
      {" "}Only the Gmail account holder can approve Google's consent screen.
    </p>
  );
}

function Group({ title, hint, rows, busy, onAction, tone, notices }) {
  if (!rows.length) return null;
  return (
    <section className={`sot-reconnect-group is-${tone}`}>
      <header>
        <h3>
          {title} <span className="sot-reconnect-count">{rows.length}</span>
        </h3>
        <p>{hint}</p>
      </header>
      <div className="sot-table-wrap">
        <table className="sot-mailbox-table sot-reconnect-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Gmail</th>
              <th>Grant</th>
              {notices ? <th>Reminders</th> : null}
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id || row.email_address}>
                <td data-label="Candidate">{row.candidateName || "—"}</td>
                <td data-label="Gmail">{row.email_address}</td>
                <td data-label="Grant">
                  <ExpiryCountdown
                    className={`sot-reconnect-when is-${tone}`}
                    expiresAt={row.grantExpiresAt}
                    override={overrideLabel(row, { alreadyExpired: tone === "expired" })}
                  />
                </td>
                {notices ? (
                  <td data-label="Reminders">
                    <span className="sot-reconnect-reminders">
                      {reminderSummary(notices[row.id])}
                    </span>
                  </td>
                ) : null}
                <td data-label="Action">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onAction("reconnect", row.source)}
                  >
                    Reconnect Gmail
                  </button>
                  {notices ? <CopyLink link={notices[row.id]?.link} /> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ReconnectWorklist({ rows, busy, onAction, withinDays = 2, automation = null }) {
  const notices = useMemo(() => {
    if (!automation || !Array.isArray(automation.notices)) return null;
    return Object.fromEntries(automation.notices.map((n) => [n.mailbox_id, n]));
  }, [automation]);
  const worklist = useMemo(() => {
    // The table's rows carry the mailbox beside the candidate; flatten them so
    // one derivation sees both, and keep the original row for the reconnect
    // action, which expects exactly what the table passes it.
    const flattened = (Array.isArray(rows) ? rows : []).map((row) => ({
      ...(row.mailbox || {}),
      candidateName: row.candidate?.name || "",
      source: row,
    }));
    return reconnectWorklist(flattened, { withinDays });
  }, [rows, withinDays]);

  if (!worklist.total) {
    return (
      <div className="sot-empty sot-reconnect-empty">
        <p>No Gmail account needs reconnecting.</p>
        <p className="sot-reconnect-empty__hint">
          Grants last {"≈"}7 days while the OAuth app is in Testing mode, so
          accounts will appear here as they approach expiry.
        </p>
      </div>
    );
  }

  return (
    <div className="sot-reconnect-worklist">
      <AutomationBanner automation={automation} />
      <Group
        title="Reconnect required"
        hint="Monitoring has stopped on these accounts. Google will not issue a new token until someone reconnects them."
        rows={worklist.expired}
        busy={busy}
        onAction={onAction}
        tone="expired"
        notices={notices}
      />
      <Group
        title="Expiring soon"
        hint="Still monitoring, but their grant is nearly up. Reconnecting now avoids a gap."
        rows={worklist.expiring}
        busy={busy}
        onAction={onAction}
        tone="soon"
      />
    </div>
  );
}

export default ReconnectWorklist;
