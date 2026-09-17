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

function Group({ title, hint, rows, busy, onAction, tone }) {
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
                <td data-label="Action">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => onAction("reconnect", row.source)}
                  >
                    Reconnect Gmail
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function ReconnectWorklist({ rows, busy, onAction, withinDays = 2 }) {
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
      <Group
        title="Reconnect required"
        hint="Monitoring has stopped on these accounts. Google will not issue a new token until someone reconnects them."
        rows={worklist.expired}
        busy={busy}
        onAction={onAction}
        tone="expired"
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
