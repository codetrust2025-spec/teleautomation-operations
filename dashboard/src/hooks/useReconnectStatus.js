import { useEffect, useState } from "react";
import { API } from "../config.js";

/**
 * What the server knows about Gmail reconnects: which accounts are waiting, when
 * each was detected, how often the team has been reminded and when the next
 * reminder is due. The server decides all of it; this only reads it.
 *
 * Polled while `enabled`, because a reconnect made on the account holder's own
 * device changes the state without anything on this page having happened. A
 * failed read leaves the last good answer in place rather than blanking the
 * screen, and is never an error the operator has to act on.
 */
export function useReconnectStatus(enabled = true, intervalMs = 60000) {
  const [status, setStatus] = useState(null);

  useEffect(() => {
    if (!enabled || typeof fetch !== "function") return undefined;
    let stopped = false;
    const read = async () => {
      try {
        const response = await fetch(
          `${API}/api/candidate-mailboxes/reconnect-status?_=${Date.now()}`,
          { credentials: "include", cache: "no-store" },
        );
        if (!response.ok) return;
        const data = await response.json();
        if (!stopped && data && data.status === "ok") setStatus(data);
      } catch {
        /* keep the last good answer */
      }
    };
    read();
    const timer = setInterval(read, intervalMs);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [enabled, intervalMs]);

  return status;
}

export default useReconnectStatus;
