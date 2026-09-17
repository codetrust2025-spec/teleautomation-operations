import React, { useEffect, useState } from "react";

import { expiryTickMs, formatExpiry } from "../utils/mailboxStatus.js";

/**
 * A grant expiry that counts itself down, without the page being reloaded.
 *
 * The deadline comes from the server (`grant_expires_at`); only the clock is
 * local. The redraw is a chain of timeouts rather than one interval, because
 * the step has to shorten as the deadline approaches -- five minutes apart
 * while it says "2 days", one second apart once it is counting seconds.
 *
 * A hidden tab is throttled to about one timer a minute, and a sleeping laptop
 * runs none at all, so the label is also recomputed the moment the tab is
 * shown again. Without that, coming back to the page shows a stale countdown
 * for up to a minute -- which is the whole of the last minute.
 */
export function ExpiryCountdown({ expiresAt, override = "", className = "" }) {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!Number.isFinite(expiresAt)) return undefined;
    let timer = 0;
    const tick = () => {
      const current = Date.now();
      setNow(current);
      const step = expiryTickMs(expiresAt - current);
      if (step) timer = setTimeout(tick, step);
    };
    const wake = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      clearTimeout(timer);
      tick();
    };
    tick();
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", wake);
    }
    return () => {
      clearTimeout(timer);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", wake);
      }
    };
  }, [expiresAt]);

  if (override) return <span className={className}>{override}</span>;
  if (!Number.isFinite(expiresAt)) return <span className={className}>unknown</span>;
  return (
    <time className={className} dateTime={new Date(expiresAt).toISOString()}>
      {formatExpiry(expiresAt - now)}
    </time>
  );
}

export default ExpiryCountdown;
