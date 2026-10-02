import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { API } from "../config.js";
import {
  publishPendingWorkChanged,
  stashPendingWorkOpenIntent,
} from "../dailyOps/PendingWorksProvider.jsx";
import "./PendingWorksTab.css";

/**
 * The work behind the Candidates badge, on the page the badge names.
 *
 * The number in the sidebar had nowhere to land: it is rendered on Daily Ops,
 * while the Candidates page never asked for it at all. This is the same figure,
 * itemised.
 *
 * It fetches `/candidates/pending-works?month=all` itself rather than reading
 * PendingWorksProvider, and that is deliberate, not a duplicate: the provider
 * disables this exact query while the Candidates page is open
 * (`deferCandidates = mainView === 'candidates'`), so the context reports zero
 * here by design. The endpoint is the same one, and the work is still decided
 * server-side — nothing about what counts as pending is re-implemented.
 */

/** What to do about each kind of gap. Anything unlisted opens the editor. */
const ACTIONS = {
  missing_resume: "Upload Resume",
  missing_phone: "Add Phone",
  missing_payment_proof: "Upload Proof",
  payment_proof_file_lost: "Upload Proof Again",
  payment_evidenced_elsewhere: "Review Row",
};
const DEFAULT_ACTION = "Edit Candidate";

/**
 * The server orders by a numeric priority — reference 10, resume 20, payment
 * 30, follow-up 35, phone 50. These bands keep that order while giving the
 * column something readable; the number itself stays in the title.
 */
function priorityBand(priority) {
  const value = Number(priority);
  if (!Number.isFinite(value)) return { label: "—", tone: "low" };
  if (value <= 20) return { label: "High", tone: "high" };
  if (value <= 35) return { label: "Medium", tone: "medium" };
  return { label: "Low", tone: "low" };
}

export function PendingWorksTab({ onOpenCandidate }) {
  const [works, setWorks] = useState([]);
  const [totals, setTotals] = useState({ tasks: 0, candidates: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  // The last count this tab told the rest of the shell about. null until the
  // first read, so arriving at a number is not itself announced as a change.
  const announced = useRef(null);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setLoading(true);
    try {
      const res = await fetch(
        `${API}/candidates/pending-works?month=all&_ts=${Date.now()}`,
        { credentials: "include", cache: "no-store" },
      );
      const data = await res.json();
      if (!res.ok || data.status !== "ok") {
        throw new Error(data.message || "Could not load pending works");
      }
      setWorks(data.works || []);
      // Straight from the payload: the badge and this header must not each
      // arrive at a total of their own.
      const candidates = Number(data.candidate_count) || 0;
      setTotals({ tasks: Number(data.count) || 0, candidates });
      // Finishing a task happens in the candidate editor, which announces
      // nothing, so this tab is what notices. Announced only on a real change,
      // and without a number: the count carried by this event is an interview
      // count belonging to another badge, so listeners must re-read rather than
      // take a figure that does not mean what theirs does.
      if (announced.current !== null && announced.current !== candidates) {
        publishPendingWorkChanged();
      }
      announced.current = candidates;
      setError("");
    } catch (err) {
      if (!silent) setError(err.message || "Could not load pending works");
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // A task is finished in the candidate editor, not here, so the counts have
    // to catch up when attention returns to this tab. The roster's existing
    // announcement covers changes made elsewhere in the shell.
    const refresh = () => load({ silent: true });
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh();
    };
    window.addEventListener("focus", refresh);
    window.addEventListener("teleautomation:pending-work-changed", refresh);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", refresh);
      window.removeEventListener("teleautomation:pending-work-changed", refresh);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  /** One row per candidate, with their gaps listed under them. */
  const groups = useMemo(() => {
    const byCandidate = new Map();
    for (const work of works) {
      const key =
        String(work.candidate_id || "") ||
        String(work.candidate_name || "").trim().toLowerCase();
      if (!byCandidate.has(key)) {
        byCandidate.set(key, {
          key,
          name: work.candidate_name || "Unnamed candidate",
          technology: work.technology || "",
          reference: work.reference || "",
          tasks: [],
        });
      }
      const group = byCandidate.get(key);
      group.tasks.push(work);
      // Technology is per-row and a merged profile may leave it blank on one
      // of them; keep the first real value rather than showing a dash.
      if (!group.technology && work.technology) group.technology = work.technology;
    }
    return Array.from(byCandidate.values()).sort((a, b) => {
      const byPriority =
        Math.min(...a.tasks.map((t) => Number(t.priority) || 999)) -
        Math.min(...b.tasks.map((t) => Number(t.priority) || 999));
      if (byPriority) return byPriority;
      return a.name.toLowerCase().localeCompare(b.name.toLowerCase());
    });
  }, [works]);

  const openCandidate = useCallback(
    (work) => {
      // The page already knows how to find a candidate and open its editor from
      // this intent -- it is how Daily Ops jumps here -- so this reuses that
      // path rather than reaching into the table.
      stashPendingWorkOpenIntent(work);
      onOpenCandidate?.(work);
    },
    [onOpenCandidate],
  );

  if (loading && !works.length) {
    return <div className="cand-pending-empty">Loading pending work…</div>;
  }

  if (error) {
    return (
      <div className="cand-pending-empty cand-pending-empty--error">
        <p>{error}</p>
        <button type="button" className="cand-pending-action" onClick={() => load()}>
          Try again
        </button>
      </div>
    );
  }

  if (!groups.length) {
    return (
      <div className="cand-pending-empty">
        <p className="cand-pending-empty__title">No pending work</p>
        <p>Every active candidate has a reference, a resume and a phone number.</p>
      </div>
    );
  }

  return (
    <section className="cand-pending" aria-label="Pending works">
      <header className="cand-pending__head">
        <p className="cand-pending__summary">
          <strong>{totals.candidates}</strong>{" "}
          {totals.candidates === 1 ? "candidate needs" : "candidates need"} attention
          {" · "}
          <strong>{totals.tasks}</strong>{" "}
          {totals.tasks === 1 ? "pending task" : "pending tasks"}
        </p>
        <button
          type="button"
          className="cand-pending-action cand-pending-action--ghost"
          onClick={() => load()}
        >
          Refresh
        </button>
      </header>

      <div className="cand-table-wrap">
        <table className="cand-table cand-pending-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Technology</th>
              <th>Missing item</th>
              <th>Priority</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {groups.map((group) =>
              group.tasks.map((task, index) => {
                const band = priorityBand(task.priority);
                return (
                  <tr
                    key={task.id || `${group.key}-${task.kind}`}
                    className={index === 0 ? "cand-pending-row--first" : undefined}
                  >
                    {index === 0 ? (
                      <>
                        <td data-label="Candidate" rowSpan={group.tasks.length}>
                          <span className="cand-pending__name">{group.name}</span>
                          {task.service_type === "round_wise" && task.date ? (
                            <span className="cand-pending__task-count">
                              Round-wise · {task.date}
                            </span>
                          ) : null}
                          {group.tasks.length > 1 && (
                            <span className="cand-pending__task-count">
                              {group.tasks.length} tasks
                            </span>
                          )}
                        </td>
                        <td data-label="Technology" rowSpan={group.tasks.length}>
                          {group.technology || "—"}
                        </td>
                      </>
                    ) : null}
                    <td data-label="Missing item">
                      {task.label || task.kind}
                      {task.detail ? (
                        <span className="cand-pending__detail">{task.detail}</span>
                      ) : null}
                    </td>
                    <td data-label="Priority">
                      <span
                        className={`cand-pending__priority is-${band.tone}`}
                        title={`Priority ${task.priority}`}
                      >
                        {band.label}
                      </span>
                    </td>
                    <td data-label="Action">
                      <button
                        type="button"
                        className="cand-pending-action"
                        onClick={() => openCandidate(task)}
                      >
                        {ACTIONS[task.kind] || DEFAULT_ACTION}
                      </button>
                    </td>
                  </tr>
                );
              }),
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default PendingWorksTab;
