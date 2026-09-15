/**
 * Candidates tracker UI — extracted from production teleautomation-app.jsx.
 * CSS: index.css (.cand-*). API: /candidates, /handler-expenses.
 */
import React from "react";
import { useConfirm } from "../context/ConfirmContext.jsx";
import { useAuth } from "../context/AuthContext.jsx";
import {
  formatIstDate as fmtIstD,
  formatIstDateTime as fmtIstDt,
} from "../utils/istTime.js";
import { CandidatesActiveRoster } from "./CandidatesActiveRoster.jsx";
import { triggerRosterDownload } from "./candidatesRosterUtils.js";
import { consumePendingWorkOpenIntent } from "../dailyOps/PendingWorksProvider.jsx";
import { PendingWorksTab } from "./PendingWorksTab.jsx";
import PayoutModal from "./PayoutModal.jsx";
import "./PayoutModal.css";
import EarningsBreakdown from "./EarningsBreakdown.jsx";
import "./EarningsBreakdown.css";
import CompanyExpenditure from "./CompanyExpenditure.jsx";
import "./CompanyExpenditure.css";
import { normalizePaymentProofs } from "./paymentProofs.js";

const w = React;
const s = { Fragment: React.Fragment };

const K1 = typeof window !== "undefined" && window.location.port === "3000";
const ve = K1
  ? ""
  : typeof window !== "undefined"
    ? `${window.location.protocol}//${window.location.host}`
    : "";

function nc() {
  return useConfirm();
}

function wu() {
  return useAuth();
}

function cR() {
  const [gate, setGate] = w.useState(null);
  const closeGate = w.useCallback(() => setGate(null), []);
  const runProtected = w.useCallback((action, opts = {}) => {
    if (typeof action === "function") {
      setGate({
        title: opts.title || "Admin password required",
        message:
          opts.message ||
          "Enter the main dashboard admin password to continue.",
        onVerified: () => {
          setGate(null);
          action();
        },
      });
    }
  }, []);
  return { gate, closeGate, runProtected };
}

function kx(e) {
  const t = Number(e) || 0;
  if (t < 1024) {
    return `${t} B`;
  } else if (t < 1048576) {
    return `${(t / 1024).toFixed(0)} KB`;
  } else {
    return `${(t / 1048576).toFixed(1)} MB`;
  }
}
function Nx(e) {
  if (!e) return "";
  return fmtIstDt(e) === "—" ? "" : fmtIstDt(e);
}
const bx = 8388608;
const PROOF_UPLOAD_STALL_MS = 20000;
const PROOF_UPLOAD_TIMEOUT_MS = 120000;
function proofCandidateId(proof, fallbackId) {
  if (proof?.candidate_id) return proof.candidate_id;
  const match = String(proof?.url || "").match(/^\/candidates\/([^/]+)\/(?:proofs|attachments\/payment_proof)\//);
  return match?.[1] || fallbackId;
}
function createProofUploadJob(file) {
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2)}`,
    file,
    previewUrl: URL.createObjectURL(file),
    status: "selected",
    progress: 0,
    error: "",
    slow: false,
    proof: null,
  };
}
// Evidence the server can no longer re-read. ARCHIVED is excluded: that is a
// deliberate retirement, not a problem waiting to be fixed.
const BROKEN_EVIDENCE_STATES = {
  MISSING_FILE: "Original file unavailable",
  CHECKSUM_MISMATCH: "Stored file does not match its checksum",
  UNREADABLE: "Stored file could not be read",
};

function isEvidenceBroken(proof) {
  return Boolean(BROKEN_EVIDENCE_STATES[proof?.file_availability]);
}

function evidenceProblem(proof) {
  return BROKEN_EVIDENCE_STATES[proof?.file_availability] || "";
}

export function PaymentProofUploader({
  candidateId: e,
  proofs: t = [],
  onChange: r,
  onBusyChange,
  controlRef,
}) {
  const [n, a] = w.useState(false);
  const [i, l] = w.useState("");
  const [c, o] = w.useState("");
  const [u, d] = w.useState(null);
  const [f, h] = w.useState(null);
  const [x, v] = w.useState("");
  const [g, p] = w.useState(false);
  const [aiResult, setAiResult] = w.useState(null);
  const [uploadJobs, setUploadJobs] = w.useState([]);
  const m = w.useRef(null);
  // `n` below is the same "busy" signal, but it is state: it stays false for
  // the rest of the tick, so two change events batched into one task both pass
  // the guard and both POST. Production saw exactly that - the first request
  // stored the proof, the second was refused by the fraud check, and the panel
  // rendered the refusal over a payment that had in fact been saved. A ref
  // updates synchronously, so the second caller sees it immediately.
  const uploadInFlightRef = w.useRef(false);
  const uploadRequestRef = w.useRef(null);
  const stallTimerRef = w.useRef(null);
  const mountedRef = w.useRef(true);
  const cancelledJobsRef = w.useRef(new Set());
  const previewUrlsRef = w.useRef(new Set());
  const _ = !e;
  const updateJob = w.useCallback((jobId, patch) => {
    if (!mountedRef.current) return;
    setUploadJobs((jobs) =>
      jobs.map((job) =>
        job.id === jobId
          ? { ...job, ...(typeof patch === "function" ? patch(job) : patch) }
          : job,
      ),
    );
  }, []);
  const clearStallTimer = w.useCallback(() => {
    if (stallTimerRef.current) {
      window.clearTimeout(stallTimerRef.current);
      stallTimerRef.current = null;
    }
  }, []);
  const armStallTimer = w.useCallback(
    (jobId) => {
      clearStallTimer();
      stallTimerRef.current = window.setTimeout(() => {
        updateJob(jobId, { slow: true });
      }, PROOF_UPLOAD_STALL_MS);
    },
    [clearStallTimer, updateJob],
  );
  const cancelJob = w.useCallback(
    (jobId) => {
      cancelledJobsRef.current.add(jobId);
      const active = uploadRequestRef.current;
      if (active?.jobId === jobId) active.xhr.abort();
      updateJob(jobId, {
        status: "cancelled",
        error: "",
        slow: false,
      });
    },
    [updateJob],
  );
  const cancelAll = w.useCallback(() => {
    uploadJobs.forEach((job) => cancelledJobsRef.current.add(job.id));
    uploadRequestRef.current?.xhr?.abort();
    setUploadJobs((jobs) =>
      jobs.map((job) =>
        ["selected", "uploading", "processing"].includes(job.status)
          ? { ...job, status: "cancelled", slow: false }
          : job,
      ),
    );
    a(false);
  }, [uploadJobs]);
  w.useEffect(() => {
    if (onBusyChange) onBusyChange(n);
  }, [n, onBusyChange]);
  w.useEffect(() => {
    if (controlRef) controlRef.current = { cancelAll };
    return () => {
      if (controlRef) controlRef.current = null;
    };
  }, [cancelAll, controlRef]);
  w.useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearStallTimer();
      uploadRequestRef.current?.xhr?.abort();
      previewUrlsRef.current.forEach((url) => URL.revokeObjectURL(url));
      previewUrlsRef.current.clear();
    };
  }, [clearStallTimer]);
  const y = w.useCallback(
    async (job, { clearNote = true } = {}) => {
      const b = job.file;
      if (!e) {
        return false;
      }
      if (b.size > bx) {
        l(`File too large (max ${bx / 1048576} MB)`);
        return false;
      }
      if (!/^image\//.test(b.type || "")) {
        l("Only image files are allowed (jpg / png / webp / gif / heic)");
        return false;
      }
      updateJob(job.id, {
        status: "uploading",
        progress: 0,
        error: "",
        slow: false,
      });
      armStallTimer(job.id);
      return new Promise((resolve) => {
        const A = new FormData();
        A.append("file", b);
        A.append("attachment_type", "payment_proof");
        if (c.trim()) {
          A.append("note", c.trim());
        }
        const xhr = new XMLHttpRequest();
        uploadRequestRef.current = { xhr, jobId: job.id };
        xhr.open("POST", `${ve}/candidates/${e}/proofs`);
        xhr.withCredentials = true;
        xhr.timeout = PROOF_UPLOAD_TIMEOUT_MS;
        xhr.upload.onprogress = (event) => {
          if (!event.lengthComputable) return;
          const progress = Math.min(
            99,
            Math.round((event.loaded * 100) / event.total),
          );
          updateJob(job.id, { status: "uploading", progress, slow: false });
          armStallTimer(job.id);
        };
        xhr.upload.onload = () => {
          updateJob(job.id, {
            status: "processing",
            progress: 100,
            slow: false,
          });
          armStallTimer(job.id);
        };
        const finish = (status, details = {}) => {
          clearStallTimer();
          if (uploadRequestRef.current?.jobId === job.id) {
            uploadRequestRef.current = null;
          }
          updateJob(job.id, { status, slow: false, ...details });
          resolve(status === "success");
        };
        xhr.onload = () => {
          let L = {};
          try {
            L = xhr.responseText ? JSON.parse(xhr.responseText) : {};
          } catch {
            finish("error", {
              error:
                xhr.status === 504
                  ? "The server timed out while saving the payment proof."
                  : `Upload failed (${xhr.status || "invalid response"}).`,
            });
            return;
          }
          if (xhr.status < 200 || xhr.status >= 300 || L.status !== "ok") {
            finish("error", {
              error:
                L.message ||
                L.detail ||
                `Unable to save payment proof (${xhr.status}).`,
            });
            return;
          }
          const nextProofs = normalizePaymentProofs(L.candidate);
          const uploadedProof =
            nextProofs.find(
              (proof) =>
                proof.original_name === b.name &&
                (!proof.size || Number(proof.size) === Number(b.size)),
            ) || nextProofs[nextProofs.length - 1] || null;
          if (L.ai_extraction) setAiResult(L.ai_extraction);
          // Hand the whole authoritative row up, not just the proofs. The
          // recalculated Received total rides on `candidate`/`payment_summary`,
          // and dropping it here was why the field stayed stale until Save.
          if (L.candidate && r != null)
            r(nextProofs, L.candidate, L.payment_summary);
          if (clearNote) o("");
          l("");
          finish("success", {
            progress: 100,
            proof: uploadedProof,
            error: "",
          });
        };
        xhr.onerror = () =>
          finish("error", {
            error: "Network connection lost. Please try again.",
          });
        xhr.ontimeout = () =>
          finish("error", {
            error: "Upload timed out before the payment proof was saved.",
          });
        xhr.onabort = () =>
          finish("cancelled", {
            error: "",
          });
        xhr.send(A);
      });
    },
    [armStallTimer, c, clearStallTimer, e, r, updateJob],
  );
  const reconcileJob = w.useCallback(
    async (job) => {
      // A refusal does not always mean nothing was saved. If a duplicate
      // request raced this one, or the response was lost after the server
      // committed, the proof is on the record and the upload the operator
      // performed did succeed. Reporting failure then is worse than useless:
      // it invites someone to "fix" a payment that is already correct.
      //
      // So ask the server what it actually holds rather than trusting the
      // response we happened to get. Nothing is invented locally - the job is
      // only promoted if a matching proof really exists, which leaves a
      // genuine rejection (entitlement, duplicate, unreadable) showing as the
      // error it is.
      if (!e || !job?.file) return false;
      try {
        const response = await fetch(`${ve}/candidates/${e}`, {
          credentials: "include",
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== "ok" || !payload.candidate) {
          return false;
        }
        const currentProofs = normalizePaymentProofs(payload.candidate);
        const existing = currentProofs.find(
          (proof) =>
            proof.original_name === job.file.name &&
            (!proof.size || Number(proof.size) === Number(job.file.size)),
        );
        if (!existing) return false;
        if (r) r(currentProofs, payload.candidate, payload.payment_summary);
        updateJob(job.id, {
          status: "success",
          progress: 100,
          proof: existing,
          error: "",
          slow: false,
        });
        return true;
      } catch {
        // No reconciliation available - leave the failure standing.
        return false;
      }
    },
    [e, r, updateJob],
  );
  const M = w.useCallback(
    async (b) => {
      if (!e || n || uploadInFlightRef.current || !b || b.length === 0) {
        return;
      }
      const selectedFiles = Array.from(b);
      const invalidFile = selectedFiles.find(
        (file) => !file || !/^image\//.test(file.type || ""),
      );
      if (invalidFile) {
        l("Only image files are allowed (jpg / png / webp / gif / heic)");
        return;
      }
      const oversizedFile = selectedFiles.find((file) => file.size > bx);
      if (oversizedFile) {
        l(`${oversizedFile.name} is larger than the ${bx / 1048576} MB limit.`);
        return;
      }
      // Claimed synchronously, before the first await, so a second event in
      // the same task cannot get past the guard above.
      uploadInFlightRef.current = true;
      const jobs = selectedFiles.map(createProofUploadJob);
      jobs.forEach((job) => previewUrlsRef.current.add(job.previewUrl));
      l("");
      setUploadJobs((current) => [...current, ...jobs]);
      a(true);
      try {
        // Yield once so the selected-file card is painted before upload begins.
        await new Promise((resolve) => window.setTimeout(resolve, 0));
        for (let O = 0; O < jobs.length; O++) {
          const job = jobs[O];
          if (cancelledJobsRef.current.has(job.id)) continue;
          const uploaded = await y(job, {
            clearNote: O === jobs.length - 1,
          });
          if (!uploaded && !cancelledJobsRef.current.has(job.id)) {
            await reconcileJob(job);
          }
        }
      } finally {
        uploadInFlightRef.current = false;
        a(false);
        if (m.current) {
          m.current.value = "";
        }
      }
    },
    [e, n, reconcileJob, y],
  );
  const retryJob = w.useCallback(
    async (job) => {
      if (n || !job?.file) return;
      cancelledJobsRef.current.delete(job.id);
      a(true);
      try {
        // The request may time out after the backend has committed the file.
        // Refresh before retrying so one screenshot cannot create two proofs.
        try {
          const response = await fetch(`${ve}/candidates/${e}`, {
            credentials: "include",
          });
          const payload = await response.json();
          if (response.ok && payload.status === "ok" && payload.candidate) {
            const currentProofs = normalizePaymentProofs(payload.candidate);
            const existing = currentProofs.find(
              (proof) =>
                proof.original_name === job.file.name &&
                (!proof.size ||
                  Number(proof.size) === Number(job.file.size)),
            );
            if (existing) {
              if (r) r(currentProofs);
              updateJob(job.id, {
                status: "success",
                progress: 100,
                proof: existing,
                error: "",
                slow: false,
              });
              return;
            }
          }
        } catch {
          // If refresh is unavailable, continue with the normal retry path.
        }
        await y(job);
      } finally {
        a(false);
      }
    },
    [e, n, r, updateJob, y],
  );
  const removeJob = w.useCallback(
    (job) => {
      if (["uploading", "processing"].includes(job.status)) cancelJob(job.id);
      previewUrlsRef.current.delete(job.previewUrl);
      URL.revokeObjectURL(job.previewUrl);
      setUploadJobs((jobs) => jobs.filter((item) => item.id !== job.id));
    },
    [cancelJob],
  );
  const keepWaiting = w.useCallback(
    (jobId) => {
      updateJob(jobId, { slow: false });
      armStallTimer(jobId);
    },
    [armStallTimer, updateJob],
  );
  function jobStatusText(job) {
    if (job.status === "selected") return "File selected";
    if (job.status === "uploading")
      return `Uploading screenshot… ${job.progress}%`;
    if (job.status === "processing") return "Processing screenshot…";
    if (job.status === "success")
      return "Screenshot uploaded successfully";
    if (job.status === "cancelled") return "Upload cancelled";
    return "Upload failed";
  }
  function k(b) {
    var O;
    const A = (O = b.target.files) == null ? undefined : O;
    if (A != null && A.length) {
      M(A);
    }
  }
  function T(b) {
    var O;
    b.preventDefault();
    p(false);
    const A = (O = b.dataTransfer) == null ? undefined : O.files;
    if (A != null && A.length) {
      M(A);
    }
  }
  async function S(b) {
    var A;
    if (!e) return;
    const ok = await window.__TA_CONFIRM_VALUE__?.confirm?.({
      title: "Remove this proof?",
      message: b.note || "This payment proof will be removed.",
      confirmLabel: "Remove",
      variant: "danger",
    });
    if (!ok) return;
    try {
      const ownerId = proofCandidateId(b, e);
      const L = await (
        await fetch(`${ve}/candidates/${ownerId}/proofs/${b.id}`, {
          method: "DELETE",
        })
      ).json();
      if (L.status === "ok") {
        if (r != null) {
          r(
            normalizePaymentProofs((A = L.candidate) == null ? undefined : A),
            L.candidate,
            L.payment_summary,
          );
        }
      } else {
        l(L.message || "Delete failed");
      }
    } catch (O) {
      l(O.message || "Network error");
    }
  }
  const [evidenceHistory, setEvidenceHistory] = w.useState(null);
  const replaceInputRef = w.useRef(null);
  const [replaceTarget, setReplaceTarget] = w.useState(null);

  function replaceProof(proof) {
    setReplaceTarget(proof);
    replaceInputRef.current?.click();
  }

  async function submitReplacement(fileList) {
    const file = fileList && fileList[0];
    const proof = replaceTarget;
    setReplaceTarget(null);
    if (replaceInputRef.current) replaceInputRef.current.value = "";
    if (!file || !proof || !e) return;
    a(true);
    l("");
    try {
      const ownerId = proofCandidateId(proof, e);
      const body = new FormData();
      body.append("file", file);
      body.append(
        "reason",
        "Administrator re-uploaded the original payment screenshot.",
      );
      const res = await fetch(
        `${ve}/candidates/${ownerId}/proofs/${proof.id}/replace`,
        { method: "POST", body, credentials: "include" },
      );
      const payload = await res.json();
      if (payload.status !== "ok") {
        l(payload.message || "Replacement failed");
        return;
      }
      if (r != null) {
        r(
          normalizePaymentProofs(payload.candidate),
          payload.candidate,
          payload.payment_summary,
        );
      }
    } catch (err) {
      l(err.message || "Network error");
    } finally {
      a(false);
    }
  }

  async function archiveProof(proof) {
    if (!e) return;
    a(true);
    l("");
    try {
      const ownerId = proofCandidateId(proof, e);
      const res = await fetch(
        `${ve}/candidates/${ownerId}/proofs/${proof.id}/archive`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify({
            reason: "Original evidence is unavailable and is not being retrieved.",
          }),
        },
      );
      const payload = await res.json();
      if (payload.status !== "ok") {
        l(payload.message || "Archive failed");
        return;
      }
      if (r != null) {
        r(
          normalizePaymentProofs(payload.candidate),
          payload.candidate,
          payload.payment_summary,
        );
      }
    } catch (err) {
      l(err.message || "Network error");
    } finally {
      a(false);
    }
  }

  async function openHistory(proof) {
    if (!e) return;
    try {
      const ownerId = proofCandidateId(proof, e);
      const res = await fetch(
        `${ve}/candidates/${ownerId}/proofs/${proof.id}/history`,
        { credentials: "include" },
      );
      const payload = await res.json();
      if (payload.status === "ok") {
        setEvidenceHistory(payload.history);
      } else {
        l(payload.message || "Could not load evidence history");
      }
    } catch (err) {
      l(err.message || "Network error");
    }
  }

  async function E(b) {
    if (e) {
      try {
        const ownerId = proofCandidateId(b, e);
        const O = await (
          await fetch(`${ve}/candidates/${ownerId}/proofs/${b.id}`, {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              note: x,
            }),
          })
        ).json();
        if (O.status === "ok") {
          const L = t.map((M) =>
            M.id === b.id
              ? {
                  ...M,
                  note: x,
                }
              : M,
          );
          if (r != null) {
            r(L, O.candidate, O.payment_summary);
          }
          h(null);
        } else {
          l(O.message || "Save failed");
        }
      } catch (A) {
        l(A.message || "Network error");
      }
    }
  }
  w.useEffect(() => {
    function b(A) {
      if (A.key === "Escape") {
        d(null);
      }
    }
    if (u) {
      document.addEventListener("keydown", b);
    }
    return () => document.removeEventListener("keydown", b);
  }, [u]);
  return (
    <div className="cand-proofs">
      <div className="cand-proofs-header">
        <span className="cand-field-label">
          Payment proofs<span className="cand-proofs-count">{t.length}</span>
        </span>
        {!_ && t.length > 0 && (
          <span className="cand-proofs-hint">
            Click a thumbnail to enlarge · drag to reorder is coming soon
          </span>
        )}
      </div>
      {_ ? (
        <div className="cand-proofs-empty cand-proofs-empty--blocked">
          <strong>Save the candidate first</strong>, then re-open this form to
          attach payment screenshots.
        </div>
      ) : (
        <s.Fragment>
          <div
            className={`cand-proofs-drop${g ? " cand-proofs-drop--active" : ""}${n ? " cand-proofs-drop--busy" : ""}`}
            onDragOver={(b) => {
              b.preventDefault();
              p(true);
            }}
            onDragLeave={() => p(false)}
            onDrop={T}
            onClick={() => {
              var b;
              return !n && ((b = m.current) == null ? undefined : b.click());
            }}
            role="button"
            tabIndex={0}
            onKeyDown={(b) => {
              var A;
              if (!n && (b.key === "Enter" || b.key === " ")) {
                b.preventDefault();
                if ((A = m.current) != null) {
                  A.click();
                }
              }
            }}
          >
            <input
              ref={m}
              type="file"
              accept="image/*"
              multiple={true}
              onChange={k}
              hidden={true}
              disabled={n}
            />
            <div className="cand-proofs-drop-icon" aria-hidden={true}>
              📷
            </div>
            <div className="cand-proofs-drop-text">
              <strong>
                {n ? "Upload in progress" : "Upload payment screenshot"}
              </strong>
              <span className="cand-proofs-drop-sub">
                PNG · JPG · WebP · up to 8 MB each · select multiple
              </span>
            </div>
          </div>
          <input
            className="cand-input cand-input--small"
            placeholder="Optional note for the next upload — e.g. ₹10k UPI · 26 May"
            value={c}
            onChange={(b) => o(b.target.value)}
            disabled={n}
          />
          {uploadJobs.length > 0 && (
            <div
              className="cand-proof-upload-jobs"
              aria-live="polite"
              aria-label="Payment screenshot upload status"
            >
              {uploadJobs.map((job, jobIndex) => {
                const active = ["selected", "uploading", "processing"].includes(
                  job.status,
                );
                return (
                  <article
                    className={`cand-proof-upload-job cand-proof-upload-job--${job.status}`}
                    key={job.id}
                  >
                    <img
                      className="cand-proof-upload-preview"
                      src={job.previewUrl}
                      alt=""
                    />
                    <div className="cand-proof-upload-main">
                      <div className="cand-proof-upload-head">
                        <strong>Payment screenshot{uploadJobs.length > 1 ? ` ${jobIndex + 1}` : ""}</strong>
                        <span>{kx(job.file.size)}</span>
                      </div>
                      <div className="cand-proof-upload-state">
                        {job.status === "processing" && (
                          <span
                            className="cand-proof-upload-spinner"
                            aria-hidden="true"
                          />
                        )}
                        <span>{jobStatusText(job)}</span>
                      </div>
                      {job.status === "uploading" && (
                        <div
                          className="cand-proof-upload-progress"
                          role="progressbar"
                          aria-label="Uploading payment screenshot"
                          aria-valuemin="0"
                          aria-valuemax="100"
                          aria-valuenow={job.progress}
                        >
                          <span style={{ width: `${job.progress}%` }} />
                        </div>
                      )}
                      {job.status === "processing" && (
                        <div
                          className="cand-proof-upload-progress cand-proof-upload-progress--processing"
                          role="progressbar"
                          aria-label="Processing payment screenshot"
                        >
                          <span />
                        </div>
                      )}
                      {job.error && (
                        <div className="cand-proof-upload-error">{job.error}</div>
                      )}
                      {job.slow && active && (
                        <div className="cand-proof-upload-slow">
                          <span>This upload is taking longer than expected.</span>
                          <button
                            type="button"
                            className="cand-proof-upload-link"
                            onClick={() => keepWaiting(job.id)}
                          >
                            Keep waiting
                          </button>
                        </div>
                      )}
                    </div>
                    <div className="cand-proof-upload-actions">
                      {active && (
                        <button
                          type="button"
                          className="cand-btn cand-btn--xs cand-btn--ghost"
                          onClick={() => cancelJob(job.id)}
                        >
                          Cancel
                        </button>
                      )}
                      {["error", "cancelled"].includes(job.status) && (
                        <button
                          type="button"
                          className="cand-btn cand-btn--xs cand-btn--primary"
                          onClick={() => retryJob(job)}
                          disabled={n}
                        >
                          Retry
                        </button>
                      )}
                      {job.status === "success" && job.proof && (
                        <button
                          type="button"
                          className="cand-btn cand-btn--xs cand-btn--ghost"
                          onClick={() => d(job.proof)}
                        >
                          View
                        </button>
                      )}
                      {job.status === "success" && (
                        <button
                          type="button"
                          className="cand-btn cand-btn--xs cand-btn--ghost"
                          onClick={() => m.current?.click()}
                        >
                          Replace
                        </button>
                      )}
                      {!active && (
                        <button
                          type="button"
                          className="cand-btn cand-btn--xs cand-btn--ghost"
                          onClick={() => removeJob(job)}
                          aria-label="Dismiss upload status"
                        >
                          Dismiss
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </s.Fragment>
      )}
      {i && <div className="cand-proofs-error">{i}</div>}
      {aiResult && (
        <div
          className="cand-proofs-ai-result"
          style={{
            margin: "8px 0",
            padding: "8px 12px",
            borderRadius: "6px",
            background: aiResult.deterministic_verified
              ? "rgba(34,197,94,.1)"
              : "rgba(251,191,36,.1)",
            border: aiResult.deterministic_verified
              ? "1px solid rgba(34,197,94,.25)"
              : "1px solid rgba(251,191,36,.25)",
            fontSize: "12px",
            lineHeight: "1.4",
          }}
        >
          <div className="cand-proofs-ai-grid">
            <span>Amount</span>
            <strong>₹{Number(aiResult.amount || 0).toLocaleString("en-IN")}</strong>
            <button
              type="button"
              onClick={() => setAiResult(null)}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                opacity: 0.5,
                fontSize: "13px",
                color: "inherit",
                padding: "0 2px",
              }}
            >
              ×
            </button>
            <span>Receiver</span>
            <strong className="cand-proofs-ai-value">
              {aiResult.receiver_name || "Not detected"}
            </strong>
            <span>UPI/reference ID</span>
            {/* A UTR is 12-16 characters and a PhonePe transaction ID is 22, so
                this value has to be allowed to break mid-token. Left to itself
                it is one unbreakable word and pushes the panel wider than its
                column. */}
            <strong className="cand-proofs-ai-value cand-proofs-ai-value--id">
              {aiResult.utr_number || aiResult.transaction_id || "Not detected"}
            </strong>
            <span>Verification status</span>
            <strong
              className="cand-proofs-ai-value"
              style={{
                color: aiResult.deterministic_verified ? "#22c55e" : "#f59e0b",
              }}
            >
              {aiResult.deterministic_verified ? "Verified" : "Needs review"}
            </strong>
          </div>
        </div>
      )}
      <input
        ref={replaceInputRef}
        type="file"
        accept="image/*,application/pdf"
        hidden={true}
        onChange={(ev) => submitReplacement(ev.target.files)}
      />
      {evidenceHistory && (
        <div className="cand-evidence-history">
          <div className="cand-evidence-history-head">
            <strong>Evidence history</strong>
            <button
              type="button"
              className="cand-btn cand-btn--xs cand-btn--ghost"
              onClick={() => setEvidenceHistory(null)}
            >
              Close
            </button>
          </div>
          <dl className="cand-evidence-facts">
            <div>
              <dt>Amount</dt>
              <dd>{$n(evidenceHistory.verified_amount)}</dd>
            </div>
            <div>
              <dt>Counts towards total</dt>
              <dd>{$n(evidenceHistory.counts_towards_total)}</dd>
            </div>
            <div>
              <dt>Verification</dt>
              <dd>{evidenceHistory.verification_state || "—"}</dd>
            </div>
            <div>
              <dt>File</dt>
              <dd>{evidenceHistory.file_availability}</dd>
            </div>
            <div>
              <dt>UTR</dt>
              <dd>{evidenceHistory.utr_number || "—"}</dd>
            </div>
            <div>
              <dt>Transaction</dt>
              <dd>{evidenceHistory.transaction_id || "—"}</dd>
            </div>
            <div>
              <dt>Checksum</dt>
              <dd className="cand-evidence-checksum">
                {evidenceHistory.checksum || "—"}
              </dd>
            </div>
            <div>
              <dt>Stored in</dt>
              <dd>{evidenceHistory.stored_in}</dd>
            </div>
          </dl>
          <ol className="cand-evidence-events">
            {(evidenceHistory.events || []).map((event, index) => (
              <li key={`${event.kind}-${index}`}>
                <span className="cand-evidence-kind">{event.kind}</span>
                <span className="cand-evidence-summary">{event.summary}</span>
                <span className="cand-evidence-when">
                  {event.at ? Nx(event.at) : ""}
                  {event.actor ? ` · ${event.actor}` : ""}
                </span>
                {event.reason && (
                  <span className="cand-evidence-reason">{event.reason}</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
      {t.length > 0 && (
        <ul className="cand-proofs-grid">
          {t.map((b) => (
            <li className="cand-proof-card" key={b.id}>
              <button
                type="button"
                className="cand-proof-thumb"
                onClick={() => d(b)}
                aria-label="Preview proof"
              >
                <img
                  src={`${ve}${b.url}`}
                  alt={b.note || "payment proof"}
                  loading="lazy"
                />
              </button>
              <div className="cand-proof-meta">
                {f === b.id ? (
                  <div className="cand-proof-note-edit">
                    <input
                      className="cand-input cand-input--small"
                      value={x}
                      onChange={(A) => v(A.target.value)}
                      placeholder="e.g. ₹10k UPI"
                      autoFocus={true}
                    />
                    <button
                      type="button"
                      className="cand-btn cand-btn--xs cand-btn--primary"
                      onClick={() => E(b)}
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      className="cand-btn cand-btn--xs cand-btn--ghost"
                      onClick={() => h(null)}
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="cand-proof-note"
                    onClick={() => {
                      h(b.id);
                      v(b.note || "");
                    }}
                    title="Click to edit"
                  >
                    {b.note || <em>add a note…</em>}
                  </button>
                )}
                <div className="cand-proof-sub">
                  <span>{Nx(b.uploaded_at)}</span>
                  <span>·</span>
                  <span>{kx(b.size)}</span>
                </div>
                {isEvidenceBroken(b) && (
                  <div className="cand-proof-broken">
                    <span className="cand-proof-broken-label">
                      {evidenceProblem(b)}
                    </span>
                    <div className="cand-proof-broken-actions">
                      <button
                        type="button"
                        className="cand-btn cand-btn--xs cand-btn--primary"
                        onClick={() => replaceProof(b)}
                        disabled={n}
                      >
                        Re-upload proof
                      </button>
                      <button
                        type="button"
                        className="cand-btn cand-btn--xs cand-btn--ghost"
                        onClick={() => archiveProof(b)}
                        disabled={n}
                      >
                        Archive reference
                      </button>
                      <button
                        type="button"
                        className="cand-btn cand-btn--xs cand-btn--ghost"
                        onClick={() => openHistory(b)}
                      >
                        Evidence history
                      </button>
                    </div>
                  </div>
                )}
              </div>
              <button
                type="button"
                className="cand-proof-delete"
                onClick={() => S(b)}
                title="Delete proof"
                aria-label="Delete proof"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}
      {u && (
        <div
          className="cand-proof-lightbox"
          onClick={() => d(null)}
          role="dialog"
          aria-label="Payment proof preview"
        >
          <button
            type="button"
            className="cand-proof-lightbox-close"
            onClick={() => d(null)}
            aria-label="Close preview"
          >
            ×
          </button>
          <img
            src={`${ve}${u.url}`}
            alt={u.note || "payment proof"}
            onClick={(b) => b.stopPropagation()}
          />
          <div
            className="cand-proof-lightbox-caption"
            onClick={(b) => b.stopPropagation()}
          >
            {u.note && <strong>{u.note}</strong>}
            <span>
              {Nx(u.uploaded_at)} · {kx(u.size)}
            </span>
            <a
              href={`${ve}${u.url}`}
              download={u.original_name || u.filename}
              className="cand-btn cand-btn--ghost cand-btn--xs"
            >
              Download
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
function ResumeAutoFill({ candidateId, onExtracted }) {
  const [busy, setBusy] = w.useState(false);
  const [aiData, setAiData] = w.useState(null);
  const [error, setError] = w.useState("");
  const [filled, setFilled] = w.useState(false);
  const inputRef = w.useRef(null);
  async function handleFile(file) {
    if (!file) return;
    setBusy(true);
    setError("");
    setAiData(null);
    setFilled(false);
    try {
      const body = new FormData();
      body.append("file", file);
      // Use a long timeout — AI model may take up to 2 minutes on cold start
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 200000); // 200s
      let res;
      try {
        const endpoint = candidateId
          ? `${ve}/candidates/${candidateId}/resumes`
          : `${ve}/public/slots/extract-resume-ai`;
        const resp = await fetch(endpoint, {
          method: "POST",
          body,
          signal: controller.signal,
        });
        res = await resp.json();
        if (candidateId && res.status === "ok") {
          if (res.ai_extraction) {
            res = { status: "ok", success: true, data: res.ai_extraction };
          } else {
            const extractionBody = new FormData();
            extractionBody.append("file", file);
            const extractionResponse = await fetch(
              `${ve}/public/slots/extract-resume-ai`,
              {
                method: "POST",
                body: extractionBody,
                signal: controller.signal,
              },
            );
            const extractionResult = await extractionResponse.json();
            res =
              extractionResult.status === "ok"
                ? extractionResult
                : {
                    status: "error",
                    data: {
                      error:
                        extractionResult.data?.error ||
                        "Resume was saved, but AI could not extract profile fields.",
                    },
                  };
          }
        }
      } finally {
        clearTimeout(timer);
      }
      if (res.status === "ok" && res.data) {
        const d = res.data;
        const hasUsefulData =
          d.candidate_name || d.phone || d.email || d.technology;
        if (res.success || hasUsefulData) {
          setAiData(d);
          // Warn user if it was partial (regex-only, no AI)
          if (!res.success && hasUsefulData) {
            setError(
              "Partial extraction (AI unavailable). Review fields before saving.",
            );
          }
        } else {
          setError(
            d.error ||
              "Could not extract profile from resume. Fill fields manually.",
          );
        }
      } else {
        setError(
          res.data?.error ||
            "Could not extract profile from resume. Fill fields manually.",
        );
      }
    } catch (err) {
      if (err.name === "AbortError") {
        setError(
          "Request timed out. Make sure Ollama tunnel is running on your laptop.",
        );
      } else {
        setError(err.message || "Resume extraction failed");
      }
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }
  function handleFill() {
    if (aiData && onExtracted) {
      onExtracted(aiData);
      setFilled(true);
    }
  }
  return (
    <div
      style={{
        margin: "0 20px 12px",
        padding: "12px 14px",
        borderRadius: "8px",
        background: "rgba(99,102,241,.06)",
        border: "1px dashed rgba(99,102,241,.25)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "10px",
          flexWrap: "wrap",
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          hidden
          onChange={(ev) => handleFile(ev.target.files?.[0])}
          disabled={busy}
        />
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          style={{
            padding: "6px 14px",
            borderRadius: "6px",
            background: "rgba(99,102,241,.15)",
            border: "1px solid rgba(99,102,241,.3)",
            color: "#a5b4fc",
            fontSize: "12px",
            fontWeight: 500,
            cursor: "pointer",
            whiteSpace: "nowrap",
          }}
        >
          {busy
            ? "⏳ AI analyzing (~30-60s)…"
            : "📄 Upload resume PDF to auto-fill"}
        </button>
        <span style={{ fontSize: "11px", color: "rgba(148,163,184,.7)" }}>
          AI reads the PDF and fills name, phone, email, and technology
          automatically
        </span>
      </div>
      {error && (
        <div
          style={{
            marginTop: "6px",
            fontSize: "12px",
            color: error.startsWith("Partial") ? "#fbbf24" : "#f87171",
          }}
        >
          {error}
        </div>
      )}
      {aiData && !filled && (
        <div
          style={{
            marginTop: "8px",
            padding: "8px 10px",
            borderRadius: "6px",
            background: "rgba(34,197,94,.08)",
            border: "1px solid rgba(34,197,94,.2)",
            fontSize: "12px",
          }}
        >
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "6px 14px",
              color: "rgba(226,232,240,.85)",
              marginBottom: "6px",
            }}
          >
            {aiData.candidate_name && <span>👤 {aiData.candidate_name}</span>}
            {aiData.technology && <span>💻 {aiData.technology}</span>}
            {aiData.phone && <span>📱 {aiData.phone}</span>}
            {aiData.years_of_experience && (
              <span>📅 {aiData.years_of_experience} yrs</span>
            )}
            {aiData.current_company && <span>🏢 {aiData.current_company}</span>}
            {aiData.email && <span>✉ {aiData.email}</span>}
          </div>
          <button
            type="button"
            onClick={handleFill}
            style={{
              padding: "5px 12px",
              borderRadius: "5px",
              background: "#22c55e",
              color: "#fff",
              border: "none",
              fontSize: "12px",
              fontWeight: 500,
              cursor: "pointer",
            }}
          >
            ✓ Fill profile fields
          </button>
        </div>
      )}
      {filled && (
        <div style={{ marginTop: "6px", fontSize: "12px", color: "#22c55e" }}>
          ✓ Fields filled from resume
        </div>
      )}
    </div>
  );
}
function ResumeUpload({ candidateId, resumes = [], onExtracted }) {
  const [busy, setBusy] = w.useState(false);
  const [message, setMessage] = w.useState("");
  const [aiData, setAiData] = w.useState(null);
  const inputRef = w.useRef(null);
  const disabled = !candidateId;
  async function upload(file) {
    if (!file || disabled) return;
    setBusy(true);
    setMessage("");
    setAiData(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await (
        await fetch(`${ve}/candidates/${candidateId}/resumes`, {
          method: "POST",
          body,
        })
      ).json();
      if (result.status !== "ok")
        throw new Error(result.message || "Resume upload failed");
      setMessage("Resume uploaded successfully.");
      if (result.ai_extraction && result.ai_extraction.is_resume) {
        setAiData(result.ai_extraction);
      }
    } catch (err) {
      setMessage(err.message || "Resume upload failed");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }
  function handleFillProfile() {
    if (aiData && onExtracted) {
      onExtracted(aiData);
      setAiData(null);
      setMessage("Profile fields updated from resume.");
    }
  }
  return (
    <div className="cand-proofs cand-resume-upload">
      <div className="cand-proofs-header">
        <span className="cand-field-label">
          Resume AI reader
          <span className="cand-proofs-count">{resumes.length}</span>
        </span>
      </div>
      {disabled ? (
        <div className="cand-proofs-empty cand-proofs-empty--blocked">
          <strong>Save the candidate first</strong>, then upload the resume.
        </div>
      ) : (
        <>
          <input
            ref={inputRef}
            type="file"
            accept=".pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            hidden
            onChange={(event) => upload(event.target.files?.[0])}
            disabled={busy}
          />
          <button
            type="button"
            className="cand-btn cand-btn--primary"
            onClick={() => inputRef.current?.click()}
            disabled={busy}
          >
            {busy ? "Analyzing resume…" : "Upload and analyze resume"}
          </button>
          <span className="cand-field-hint">
            PDF, DOC, or DOCX · up to 10 MB · AI auto-extracts profile
          </span>
        </>
      )}
      {message && <div className="cand-proofs-error">{message}</div>}
      {aiData && (
        <div
          style={{
            margin: "8px 0",
            padding: "10px 12px",
            borderRadius: "6px",
            background: "rgba(99,102,241,.08)",
            border: "1px solid rgba(99,102,241,.2)",
            fontSize: "12px",
            lineHeight: "1.5",
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              marginBottom: "6px",
            }}
          >
            <strong style={{ color: "#a5b4fc", fontSize: "12px" }}>
              📄 AI Resume Extraction
            </strong>
            <button
              type="button"
              onClick={() => setAiData(null)}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "rgba(148,163,184,.6)",
                fontSize: "13px",
              }}
            >
              ×
            </button>
          </div>
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "6px 14px",
              color: "rgba(226,232,240,.85)",
              fontSize: "12px",
            }}
          >
            {aiData.candidate_name && <span>👤 {aiData.candidate_name}</span>}
            {aiData.technology && <span>💻 {aiData.technology}</span>}
            {aiData.years_of_experience && (
              <span>📅 {aiData.years_of_experience} yrs</span>
            )}
            {aiData.phone && <span>📱 {aiData.phone}</span>}
            {aiData.current_company && <span>🏢 {aiData.current_company}</span>}
            {aiData.email && <span>✉ {aiData.email}</span>}
          </div>
          {aiData.skills && aiData.skills.length > 0 && (
            <div
              style={{
                marginTop: "4px",
                fontSize: "11px",
                color: "rgba(148,163,184,.7)",
              }}
            >
              Skills: {aiData.skills.slice(0, 6).join(", ")}
              {aiData.skills.length > 6 ? "…" : ""}
            </div>
          )}
          {onExtracted && (
            <button
              type="button"
              onClick={handleFillProfile}
              style={{
                marginTop: "8px",
                padding: "5px 12px",
                borderRadius: "5px",
                background: "#6366f1",
                color: "#fff",
                border: "none",
                fontSize: "12px",
                fontWeight: 500,
                cursor: "pointer",
              }}
            >
              Fill profile from resume
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ResumeCell({ candidate, onRefresh }) {
  const inputRef = w.useRef(null);
  const [busy, setBusy] = w.useState(false);
  const count =
    Number(candidate.resume_count) ||
    (Array.isArray(candidate.resumes)
      ? candidate.resumes.filter((r) => r && r.id).length
      : 0);

  async function upload(file) {
    if (!file || !candidate.id) return;
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const result = await (
        await fetch(`${ve}/candidates/${candidate.id}/resumes`, {
          method: "POST",
          body,
        })
      ).json();
      if (result.status !== "ok")
        throw new Error(result.message || "Upload failed");
      if (onRefresh) await onRefresh();
    } catch (err) {
      window.alert(err.message || "Resume upload failed");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  function openManager(ev) {
    ev.stopPropagation();
    ev.preventDefault();
    // Open resume manager modal
    const backdrop = document.createElement("div");
    backdrop.className = "cand-modal-backdrop cand-resume-manager";
    const panel = document.createElement("div");
    panel.className = "cand-modal cand-modal--resume";
    const close = () => backdrop.remove();
    backdrop.onclick = (event) => {
      if (event.target === backdrop) close();
    };
    backdrop.append(panel);
    document.body.append(backdrop);
    const render = async () => {
      panel.innerHTML =
        '<header class="cand-modal-header"><div><h3 class="cand-modal-title">Resume \u00b7 ' +
        candidate.name +
        '</h3><p class="cand-modal-sub">Manage saved resume versions</p></div><button type="button" class="cand-modal-close" aria-label="Close">\u00d7</button></header><div class="cand-modal-body cand-modal-body--stack"><p class="cand-exp-empty">Loading resumes\u2026</p></div>';
      panel.querySelector(".cand-modal-close").onclick = close;
      let details = candidate;
      try {
        const response = await fetch(`${ve}/candidates/${candidate.id}`, {
          credentials: "include",
        });
        const payload = await response.json();
        if (payload.status === "ok" && payload.candidate)
          details = payload.candidate;
      } catch (_) {}
      const resumes = Array.isArray(details.resumes) ? details.resumes : [];
      const body = panel.querySelector(".cand-modal-body");
      body.innerHTML = "";
      const actions = document.createElement("div");
      actions.className = "cand-resumes-modal-actions";
      const input = document.createElement("input");
      input.type = "file";
      input.hidden = true;
      input.accept = ".pdf,.doc,.docx";
      const uploadBtn = document.createElement("button");
      uploadBtn.type = "button";
      uploadBtn.className = "cand-btn cand-btn--primary";
      uploadBtn.textContent = "Upload new resume";
      uploadBtn.onclick = () => input.click();
      input.onchange = async () => {
        const file = input.files && input.files[0];
        if (!file) return;
        uploadBtn.disabled = true;
        uploadBtn.textContent = "Uploading\u2026";
        try {
          const fd = new FormData();
          fd.append("file", file);
          const result = await (
            await fetch(`${ve}/candidates/${candidate.id}/resumes`, {
              method: "POST",
              body: fd,
            })
          ).json();
          if (result.status !== "ok")
            throw new Error(result.message || "Upload failed");
          if (onRefresh) await onRefresh();
          await render();
        } catch (err) {
          window.alert(err.message);
        } finally {
          uploadBtn.disabled = false;
          uploadBtn.textContent = "Upload new resume";
          input.value = "";
        }
      };
      actions.append(input, uploadBtn);
      body.append(actions);
      if (!resumes.length) {
        const empty = document.createElement("p");
        empty.className = "cand-exp-empty";
        empty.textContent = "No resume uploaded yet.";
        body.append(empty);
        return;
      }
      const list = document.createElement("ul");
      list.className = "cand-resumes-list cand-resumes-list--modal";
      resumes.forEach((entry) => {
        const item = document.createElement("li");
        item.className = "cand-resume-item";
        const meta = document.createElement("div");
        meta.className = "cand-resume-meta";
        const name = document.createElement("div");
        name.className = "cand-resume-name";
        name.textContent =
          entry.note || entry.original_name || entry.filename || "Resume";
        const sub = document.createElement("div");
        sub.className = "cand-proof-sub";
        sub.textContent = entry.uploaded_at
          ? new Date(entry.uploaded_at).toLocaleString()
          : "";
        const rowActions = document.createElement("div");
        rowActions.className = "cand-resume-actions";
        const view = document.createElement("button");
        view.type = "button";
        view.className = "cand-btn cand-btn--ghost cand-btn--xs";
        view.textContent = "View";
        view.onclick = () => {
          const fileUrl = `${window.location.origin}/candidates/${candidate.id}/resumes/${entry.id}/preview`;
          const ext = (
            (entry.original_name || entry.filename || "").split(".").pop() || ""
          ).toLowerCase();
          if (ext === "pdf") {
            window.open(fileUrl, "_blank", "noopener");
          } else {
            window.open(
              `https://docs.google.com/gview?url=${encodeURIComponent(fileUrl)}&embedded=true`,
              "_blank",
              "noopener",
            );
          }
        };
        const rename = document.createElement("button");
        rename.type = "button";
        rename.className = "cand-btn cand-btn--ghost cand-btn--xs";
        rename.textContent = "Rename";
        rename.onclick = async () => {
          const note = window.prompt(
            "Resume name / note",
            entry.note || entry.original_name || "",
          );
          if (note === null) return;
          const result = await (
            await fetch(
              `${ve}/candidates/${candidate.id}/resumes/${entry.id}`,
              {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ note }),
              },
            )
          ).json();
          if (result.status !== "ok")
            return window.alert(result.message || "Rename failed");
          if (onRefresh) await onRefresh();
          await render();
        };
        const download = document.createElement("a");
        download.className = "cand-btn cand-btn--ghost cand-btn--xs";
        download.textContent = "Download";
        download.href = `${ve}/candidates/${candidate.id}/resumes/${entry.id}`;
        download.download = entry.original_name || entry.filename || "resume";
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className =
          "cand-btn cand-btn--ghost cand-btn--xs cand-btn--danger-ghost";
        remove.textContent = "Delete";
        remove.title = "Delete resume";
        remove.onclick = async () => {
          if (!window.confirm("Delete this resume version?")) return;
          const result = await (
            await fetch(
              `${ve}/candidates/${candidate.id}/resumes/${entry.id}`,
              { method: "DELETE" },
            )
          ).json();
          if (result.status !== "ok")
            return window.alert(result.message || "Delete failed");
          if (onRefresh) await onRefresh();
          await render();
        };
        meta.append(name, sub);
        rowActions.append(view, rename, download, remove);
        item.append(meta, rowActions);
        list.append(item);
      });
      body.append(list);
    };
    render();
  }

  return (
    <span
      className="cand-resume-cell-react"
      onMouseDown={(ev) => ev.stopPropagation()}
      onClick={(ev) => ev.stopPropagation()}
    >
      {count > 0 && (
        <button
          type="button"
          className="cand-resume-link"
          onMouseDown={(ev) => ev.stopPropagation()}
          onClick={openManager}
        >
          <span aria-hidden="true">📄</span> {count}{" "}
          {count === 1 ? "resume" : "resumes"}
        </button>
      )}
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.doc,.docx"
        hidden
        onChange={(ev) => upload(ev.target.files && ev.target.files[0])}
        disabled={busy}
      />
      <button
        type="button"
        className="cand-btn cand-btn--ghost cand-btn--xs"
        onMouseDown={(ev) => ev.stopPropagation()}
        onClick={
          count > 0
            ? openManager
            : (ev) => {
                ev.stopPropagation();
                ev.preventDefault();
                if (inputRef.current) inputRef.current.click();
              }
        }
        disabled={busy}
      >
        {busy ? "…" : count ? "Update" : "Upload resume"}
      </button>
    </span>
  );
}

function _Component23({
  phone: e,
  defaultCountry: t = "91",
  inline: r = false,
}) {
  const n = (e || "").trim();
  if (!n) {
    return <span className="cand-phone-empty">—</span>;
  }
  const [a, i] = w.useState(false);
  const [l, c] = w.useState("");
  const o = w.useRef(null);
  w.useEffect(() => {
    if (!a) {
      return;
    }
    function p(_) {
      if (o.current && !o.current.contains(_.target)) {
        i(false);
      }
    }
    function m(_) {
      if (_.key === "Escape") {
        i(false);
      }
    }
    document.addEventListener("mousedown", p);
    document.addEventListener("keydown", m);
    return () => {
      document.removeEventListener("mousedown", p);
      document.removeEventListener("keydown", m);
    };
  }, [a]);
  w.useEffect(() => {
    if (!l) {
      return;
    }
    const p = setTimeout(() => c(""), 1600);
    return () => clearTimeout(p);
  }, [l]);
  const u = n.replace(/[^\d+]/g, "");
  const d = u.startsWith("+") ? u : u.length === 10 ? `+${t}${u}` : `+${u}`;
  const f = d.replace(/^\+/, "");
  const h = d;
  const x = `https://wa.me/${f}`;
  async function v(p) {
    if (p != null) {
      p.stopPropagation();
    }
    try {
      await navigator.clipboard.writeText(n);
      c("Copied");
    } catch {
      try {
        const m = document.createElement("textarea");
        m.value = n;
        m.style.position = "fixed";
        m.style.opacity = "0";
        document.body.appendChild(m);
        m.select();
        document.execCommand("copy");
        document.body.removeChild(m);
        c("Copied");
      } catch {
        c("Copy failed");
      }
    }
    i(false);
  }
  function g(p) {
    p.stopPropagation();
  }
  return (
    <span
      className={`cand-phone-cell${r ? " cand-phone-cell--inline" : ""}`}
      ref={o}
      onClick={g}
    >
      <a
        href={x}
        target="_blank"
        rel="noopener noreferrer"
        className="cand-phone-trigger"
        title="Open WhatsApp chat"
        onClick={(p) => p.stopPropagation()}
      >
        <span className="cand-phone-icon" aria-hidden={true}>
          ☎
        </span>
        <span className="cand-phone-num">{n}</span>
      </a>
      {l && (
        <span className="cand-phone-toast" role="status">
          {l}
        </span>
      )}
    </span>
  );
}
const Cu = 20000;
const k_ = 15000;
const wi = 5000;
const ki = 9000;
const B8 = 10000;
const U8 = new Set([Cu, k_, wi, ki, 0]);
function Eu(e) {
  return e === "internal" || e === "non_domestic";
}
function N_(e) {
  if (e) {
    return k_;
  } else {
    return Cu;
  }
}
function os(e, t, r) {
  if (e === "round_wise") {
    if (Eu(r)) {
      return ki;
    } else {
      return wi;
    }
  } else {
    return N_(t);
  }
}
function z8(e) {
  if (
    (e == null ? undefined : e.service_type) === "round_wise" ||
    (e == null ? undefined : e.service_type) === "profile_service"
  ) {
    const t = e.interview_scope;
    return {
      service_type: e.service_type,
      interview_scope: Eu(t) ? "internal" : "external",
    };
  }
  const t = Number(e == null ? undefined : e.expected_payment) || 0;
  if (t === wi) {
    return {
      service_type: "round_wise",
      interview_scope: "external",
    };
  } else if (t === ki) {
    return {
      service_type: "round_wise",
      interview_scope: "internal",
    };
  } else {
    return {
      service_type: "profile_service",
      interview_scope: "external",
    };
  }
}
function $n(e) {
  const t = Number(e) || 0;
  if (t) {
    return `₹${t.toLocaleString("en-IN")}`;
  } else {
    return "₹0";
  }
}
function wl(e, t, r, bgv = false) {
  const n = Number(e) || 0;
  const a = Number(t) || 0;
  const i = Number(r) || 0;
  const l = n
    ? Math.min(n, Math.max(0, a - (bgv || a - i === 30000 ? 30000 : 0)))
    : 0;
  if (l <= 0) {
    return 0;
  }
  let c;
  if (i > 0 && l < i) {
    c = Math.max(0, 2 * l - i);
  } else {
    c = l;
  }
  return Math.floor(c * 0.5);
}
function Y8(e, t, r, bgv = false) {
  const n = Number(e) || 0;
  const a = Number(t) || 0;
  const i = Number(r) || 0;
  const l = n ? Math.min(n, Math.max(0, a - (bgv ? 30000 : 0))) : 0;
  if (l <= 0) {
    return 0;
  }
  if (i > 0 && l < i) {
    return Math.max(0, 2 * l - i);
  } else {
    return l;
  }
}
function b_(e) {
  const t =
    Number(e.expected_payment) ||
    os(e.service_type, e.consultancy, e.interview_scope);
  if (e.service_type === "round_wise") {
    return t;
  }
  return Math.min(B8, t);
}
function W8(e) {
  if (!e.slots_group_posted) {
    return "Confirm the slot screenshot was posted in the Interview slots WhatsApp group first.";
  }
  const t = (e.reference || "").trim();
  if (!t || t.toLowerCase() === "unknown") {
    return "Assign an owner (reference) before confirming the interview slot.";
  }
  const r = Number(e.payment) || 0;
  const n = b_(e);
  if (r < n) {
    return `Record at least ${$n(n)} received (currently ${$n(r)}).`;
  } else if ((e.date || "").trim()) {
    return null;
  } else {
    return "Set the interview date before confirming the slot.";
  }
}
// Display labels only. The stored values are the four in VALID_STAGES and are
// deliberately untouched: "fail" is what every revenue and slot query already
// excludes, so renaming the value would mean finding each of those or silently
// letting a rejected candidate back into revenue.
//
// Only in_progress appears in Pending Gmail and Pending Works; the other three
// are terminal and already filtered out by stage.
const V8 = [
  {
    value: "in_progress",
    label: "In progress",
  },
  {
    value: "completed",
    label: "Closed / Completed",
  },
  {
    // Stored as "fail". Read as a rejection of the candidate, not a system
    // failure, which is what "Failed" was being taken to mean.
    value: "fail",
    label: "Rejected",
  },
  {
    value: "dropped",
    label: "Dropped",
  },
];
const H8 = [
  "SAP BASIS",
  "SAP Sales",
  "SAP MM",
  "SAP HANA",
  "Salesforce",
  "ServiceNow",
  "React JS",
  "Angular",
  "Java Backend",
  "Node JS",
  "Python",
  "AWS Admin",
  "AWS Cloud",
  "AWS DevOps",
  "Azure DevOps",
  "Azure Admin",
  "Cloud",
  "Cloud DevOps",
  "DevOps",
  "Testing",
  "ETL",
  "Oracle Fusion (Tech Con)",
  "Oracle Fusion (Func)",
  "Data Engineer",
  "Data Analyst",
  "ML Engineer",
];
function G8() {
  return {
    name: "",
    stage: "in_progress",
    technology: "",
    task: "not_started",
    phone: "",
    email: "",
    reference: "",
    service_type: "profile_service",
    interview_scope: "external",
    consultancy: false,
    bgv_certificates: false,
    ctc_percentage: "",
    payment: "",
    expected_payment: String(Cu),
    follow_up: "",
    date: new Date().toISOString().slice(0, 10),
    closure_date: "",
    time: "",
    expenses: "",
    notes: "",
    slot_confirmed: false,
    slots_group_posted: false,
  };
}
function K8(e) {
  const t = !!e.consultancy;
  const { service_type: r, interview_scope: n } = z8(e);
  return {
    name: e.name || "",
    stage: e.stage || "in_progress",
    technology: e.technology || "",
    task: e.task || "not_started",
    phone: e.phone || "",
    email: e.email || "",
    reference: e.reference || "",
    service_type: r,
    interview_scope: n,
    consultancy: r === "round_wise" ? false : t,
    bgv_certificates: !!e.bgv_certificates,
    ctc_percentage: e.ctc_percentage || "",
    payment: e.payment ? String(e.payment) : "",
    expected_payment: e.expected_payment
      ? String(e.expected_payment)
      : String(os(r, t, n)),
    follow_up: e.follow_up || "",
    date: e.date || "",
    closure_date: e.closure_date || "",
    time: e.time || "",
    expenses: e.expenses || "",
    notes: e.notes || "",
    slot_confirmed: !!e.slot_confirmed,
    slots_group_posted: !!e.slots_group_posted,
    // Server-computed payment and referral figures. Carried into the draft so
    // the form shows the authoritative numbers the moment it opens, and keeps
    // showing them after a proof mutation refreshes the summary.
    payment_is_proof_derived: !!e.payment_is_proof_derived,
    expected_minimum: e.expected_minimum,
    verified_received: e.verified_received,
    verified_proof_total: e.verified_proof_total,
    verified_proof_count: e.verified_proof_count,
    above_minimum: e.above_minimum,
    balance_due: e.balance_due,
    payment_needs_reconciliation: !!e.payment_needs_reconciliation,
    payment_reconciliation_gap: e.payment_reconciliation_gap,
    referral_commission: e.referral_commission,
    service_expected: e.service_expected,
    service_received: e.service_received,
    service_outstanding: e.service_outstanding,
    bgv_expected: e.bgv_expected,
    bgv_received: e.bgv_received,
    bgv_outstanding: e.bgv_outstanding,
    referral_percentage: e.referral_percentage,
    referral_basis: e.referral_basis,
    referrer_complimentary_amount: e.referrer_complimentary_amount,
  };
}
function ReferencePicker({
  value,
  onChange,
  options = [],
  readOnly = false,
  placeholder,
  title,
}) {
  const [open, setOpen] = w.useState(false);
  const wrapRef = w.useRef(null);
  const filtered = w.useMemo(() => {
    const q = (value || "").trim().toLowerCase();
    const list = options.filter(Boolean);
    if (!q) {
      return list;
    }
    return list.filter((name) => name.toLowerCase().includes(q));
  }, [options, value]);
  w.useEffect(() => {
    function onDocDown(ev) {
      if (wrapRef.current && !wrapRef.current.contains(ev.target)) {
        setOpen(false);
      }
    }
    if (open) {
      document.addEventListener("mousedown", onDocDown);
      return () => document.removeEventListener("mousedown", onDocDown);
    }
  }, [open]);
  function pick(name) {
    onChange(name);
    setOpen(false);
  }
  const showMenu = open && !readOnly && filtered.length > 0;
  return (
    <div className="cand-ref-picker" ref={wrapRef}>
      <input
        className="cand-input"
        value={value}
        onChange={(ev) => {
          onChange(ev.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        readOnly={readOnly}
        title={title}
        autoComplete="off"
        role="combobox"
        aria-expanded={showMenu}
        aria-autocomplete="list"
      />
      {showMenu && (
        <ul className="cand-ref-menu" role="listbox">
          {filtered.map((name) => (
            <li key={name}>
              <button
                type="button"
                className="cand-ref-option"
                role="option"
                onMouseDown={(ev) => ev.preventDefault()}
                onClick={() => pick(name)}
              >
                {name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
export function CandidateEditModal({
  initial: e,
  onClose: t,
  onSave: r,
  handlerReference: n = null,
  lockReference: a = false,
  isAdmin: i = false,
  referenceOptions: refOpts = [],
}) {
  const { confirm: confirmModal } = nc();
  const [l, c] = w.useState(() => {
    if (e) {
      return K8(e);
    }
    const C = G8();
    if (n) {
      C.reference = n;
    }
    return C;
  });
  const [o, setProofs] = w.useState(() =>
    normalizePaymentProofs(e),
  );
  // Proof mutations return the recalculated row. Fold its payment figures into
  // the draft straight away so Received, the Paid/Partial badge, outstanding
  // and the follow-up requirement all reflect the verified proofs before Save.
  const u = w.useCallback((nextProofs, candidate, paymentSummary) => {
    setProofs(nextProofs);
    if (!candidate && !paymentSummary) return;
    c((draft) => {
      const next = { ...draft };
      if (paymentSummary) {
        next.payment = Number(paymentSummary.received_total) || 0;
        next.expected_minimum = Number(paymentSummary.expected_amount) || 0;
        next.verified_received = Number(paymentSummary.received_total) || 0;
        next.verified_proof_total =
          Number(paymentSummary.verified_proof_total) || 0;
        next.above_minimum = Number(paymentSummary.above_minimum_amount) || 0;
        next.balance_due = Number(paymentSummary.outstanding_amount) || 0;
        next.verified_proof_count =
          Number(paymentSummary.verified_proof_count) || 0;
        next.payment_is_proof_derived = !!paymentSummary.proof_derived;
        next.payment_needs_reconciliation =
          !!paymentSummary.needs_reconciliation;
        next.payment_reconciliation_gap =
          Number(paymentSummary.reconciliation_gap) || 0;
        next.referral_commission =
          Number(paymentSummary.referral_commission) || 0;
        next.referral_percentage =
          Number(paymentSummary.referral_percentage) || 0;
        next.referral_basis = Number(paymentSummary.referral_basis) || 0;
        next.referrer_complimentary_amount =
          Number(paymentSummary.referrer_complimentary_amount) || 0;
      } else if (candidate) {
        next.payment = Number(candidate.payment) || 0;
        next.expected_minimum = Number(candidate.expected_minimum) || 0;
        next.verified_received = Number(candidate.verified_received) || 0;
        next.verified_proof_total = Number(candidate.verified_proof_total) || 0;
        next.above_minimum = Number(candidate.above_minimum) || 0;
        next.balance_due = Number(candidate.balance_due) || 0;
        next.verified_proof_count = Number(candidate.verified_proof_count) || 0;
        next.payment_is_proof_derived = !!candidate.payment_is_proof_derived;
        next.payment_needs_reconciliation =
          !!candidate.payment_needs_reconciliation;
        next.payment_reconciliation_gap =
          Number(candidate.payment_reconciliation_gap) || 0;
        next.referral_commission = Number(candidate.referral_commission) || 0;
        next.referral_percentage = Number(candidate.referral_percentage) || 0;
        next.referral_basis = Number(candidate.referral_basis) || 0;
        next.referrer_complimentary_amount =
          Number(candidate.referrer_complimentary_amount) || 0;
      }
      return next;
    });
  }, []);
  const [d, f] = w.useState(false);
  const [h, x] = w.useState("");
  const [proofUploadBusy, setProofUploadBusy] = w.useState(false);
  const proofUploadControl = w.useRef(null);
  const v = w.useRef(null);
  const requestClose = w.useCallback(async () => {
    if (proofUploadBusy) {
      const leave = await confirmModal({
        title: "Payment screenshot upload in progress",
        message:
          "Leaving now will cancel the active payment screenshot upload. Continue?",
        confirmLabel: "Leave and cancel upload",
        cancelLabel: "Continue upload",
        variant: "warn",
      });
      if (!leave) return;
      proofUploadControl.current?.cancelAll();
    }
    if (t) t();
  }, [confirmModal, proofUploadBusy, t]);
  w.useEffect(() => {
    setProofs(normalizePaymentProofs(e));
  }, [e == null ? undefined : e.id]);
  w.useEffect(() => {
    var C;
    if ((C = v.current) != null) {
      C.focus();
    }
  }, []);
  w.useEffect(() => {
    function C(Y) {
      if (
        Y.key === "Escape" &&
        !document.querySelector(".cand-proof-lightbox")
      ) {
        requestClose();
      }
    }
    document.addEventListener("keydown", C);
    return () => document.removeEventListener("keydown", C);
  }, [requestClose]);
  function g(C, Y) {
    if (C === "stage" && Y === "dropped") {
      x("");
    }
    c((J) => ({
      ...J,
      [C]: Y,
    }));
  }
  function p(C) {
    const Y = Number(C.expected_payment) || 0;
    if (U8.has(Y)) {
      return String(
        os(C.service_type, C.consultancy, C.interview_scope) +
          (C.bgv_certificates ? 30000 : 0),
      );
    } else {
      return C.expected_payment;
    }
  }
  function m(C) {
    c((Y) => {
      const J = {
        ...Y,
        service_type: C,
        consultancy: C === "round_wise" ? false : Y.consultancy,
      };
      return {
        ...J,
        expected_payment: p(J),
      };
    });
  }
  function _(C) {
    c((Y) => {
      const J = {
        ...Y,
        interview_scope: C,
      };
      return {
        ...J,
        expected_payment: p(J),
      };
    });
  }
  function y(C) {
    c((Y) => {
      const J = {
        ...Y,
        consultancy: C,
      };
      return {
        ...J,
        expected_payment: p(J),
      };
    });
  }
  function B(C) {
    c((Y) => ({
      ...Y,
      bgv_certificates: C,
      expected_payment: String(
        os(Y.service_type, Y.consultancy, Y.interview_scope) + (C ? 30000 : 0),
      ),
    }));
  }
  w.useEffect(() => {
    const body = document.querySelector(".cand-modal .cand-modal-body");
    if (!body) return;
    // Skip if using two-panel layout (BGV is rendered in JSX directly)
    if (body.querySelector(".cand-panel--left")) return;
    body.querySelector(".cand-bgv-option")?.remove();
    const expected = Array.from(body.querySelectorAll("label")).find((node) =>
      node.textContent?.includes("Expected ₹"),
    );
    if (!expected) return;
    const field = document.createElement("label");
    field.className = `cand-field cand-field--span2 cand-consultancy-field cand-bgv-option${l.bgv_certificates ? " cand-consultancy-field--on" : ""}`;
    field.innerHTML =
      '<span class="cand-field-label">Additional services</span><div class="cand-consultancy-toggle"><input type="checkbox" id="cand-bgv-cb"><label for="cand-bgv-cb" class="cand-consultancy-label"><span class="cand-consultancy-pip"></span><span class="cand-consultancy-text"><strong>BGV certificates</strong><em>Separate ₹30,000 charge · added to the expected total</em></span></label></div>';
    const input = field.querySelector("#cand-bgv-cb");
    input.checked = !!l.bgv_certificates;
    input.onchange = () => B(input.checked);
    body.insertBefore(field, expected);
  }, [l.bgv_certificates]);
  // Safari on iPhone does not reliably expose an input's datalist. Keep the
  // desktop text field, but add a native select that CSS shows only on mobile.
  w.useEffect(() => {
    const input = document.querySelector(
      '.cand-modal-body input[list="cand-tech-list"]',
    );
    const field = input == null ? undefined : input.closest(".cand-field");
    if (!input || !field || field.querySelector(".cand-tech-mobile-select"))
      return;
    // Skip if using two-panel layout
    const body = document.querySelector(".cand-modal .cand-modal-body");
    if (body && body.querySelector(".cand-panel--left")) return;
    field.classList.add("cand-tech-field");

    const select = document.createElement("select");
    select.className = "cand-input cand-tech-mobile-select";
    select.setAttribute("aria-label", "Technology");
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "Select technology";
    select.appendChild(placeholder);
    Array.from(new Set([l.technology, ...H8].filter(Boolean))).forEach(
      (value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      },
    );
    select.value = l.technology || "";
    select.addEventListener("change", () => g("technology", select.value));
    input.insertAdjacentElement("afterend", select);
  }, [l.technology]);
  const k = Number(l.payment) || 0;
  const T =
    Number(l.expected_payment) ||
    os(l.service_type, l.consultancy, l.interview_scope);
  const P = os(l.service_type, l.consultancy, l.interview_scope);
  const F = Y8(k, T, P, !!l.bgv_certificates);
  // Referral share comes from the server: it is 50% of what was actually
  // received, less any BGV pass-through, and it excludes closure complimentary
  // amounts. `wl` is kept only for the read-only list view.
  const referralCommission =
    l.referral_commission != null
      ? Number(l.referral_commission) || 0
      : wl(k, T, P, !!l.bgv_certificates);
  const S = Math.max(0, T - k);
  const E = w.useMemo(
    () => (k <= 0 ? "unpaid" : k >= T ? "paid" : "partial"),
    [k, T],
  );
  const b = S > 0;
  const A = W8(l);
  const O = !A;
  const L = b_(l);
  async function M(C) {
    var Y;
    if ((Y = C == null ? undefined : C.preventDefault) != null) {
      Y.call(C);
    }
    if (proofUploadBusy) {
      x("Wait for the payment screenshot upload to finish before saving.");
      return;
    }
    if (!l.name.trim()) {
      x("Name is required");
      return;
    }
    const isDropped = l.stage === "dropped";
    if (!isDropped) {
      if (!l.technology || !l.technology.trim()) {
        x("Technology is required — select or type a tech stack.");
        return;
      }
      if (!l.phone || !l.phone.trim() || l.phone.trim().length < 8) {
        x("Phone number is required — enter a valid 10-digit number.");
        return;
      }
      if (!l.reference || !l.reference.trim()) {
        x("Reference is required — who referred this lead?");
        return;
      }
      if (l.service_type === "profile_service") {
        if (String(l.ctc_percentage).trim() === "") {
          x("% on CTC is required for profile-service candidates.");
          return;
        }
        const ctcPercentage = Number(l.ctc_percentage);
        if (!Number.isFinite(ctcPercentage) || ctcPercentage <= 0 || ctcPercentage > 100) {
          x("% on CTC must be greater than 0 and not more than 100.");
          return;
        }
      }
      if (b && !l.follow_up.trim()) {
        x(
          `₹${S.toLocaleString("en-IN")} balance pending — add a short follow-up / remark before saving.`,
        );
        return;
      }
      if (l.slot_confirmed && A && !i) {
        x(A);
        return;
      }
      // Active candidates with recorded payments require proof. Dropped records
      // are historical closures and remain saveable without completion evidence.
      const paymentAmt = l.payment === "" ? 0 : Number(l.payment);
      if (e && paymentAmt > 0 && (!o || o.length === 0)) {
        x(
          "Payment proof is required — upload a screenshot before saving when payment is recorded.",
        );
        return;
      }
    }
    f(true);
    x("");
    try {
      const J = {
        ...l,
        service_type: l.service_type,
        interview_scope:
          l.service_type === "round_wise" ? l.interview_scope : "",
        consultancy: l.service_type === "round_wise" ? false : !!l.consultancy,
        bgv_certificates: !!l.bgv_certificates,
        ctc_percentage: l.ctc_percentage === "" ? "" : Number(l.ctc_percentage),
        // A proof-derived total belongs to the proofs, so the draft never sends
        // one back. The server re-derives it regardless; omitting it here stops
        // a stale snapshot from racing a verification that landed mid-edit.
        payment: l.payment_is_proof_derived
          ? undefined
          : l.payment === ""
            ? 0
            : Number(l.payment),
        expected_payment:
          l.expected_payment === ""
            ? os(l.service_type, l.consultancy, l.interview_scope)
            : Number(l.expected_payment),
        follow_up: b ? l.follow_up.trim() : "",
        slot_confirmed: !!l.slot_confirmed,
        slots_group_posted: !!l.slots_group_posted,
        logged_date: e ? undefined : l.date || "",
      };
      await r(J);
    } catch (J) {
      x(J.message || "Save failed");
    } finally {
      f(false);
    }
  }
  return (
    <div
      className="cand-modal-backdrop"
      onClick={(C) =>
        C.target === C.currentTarget && requestClose()
      }
    >
      <form
        className="cand-modal"
        onSubmit={M}
        noValidate={l.stage === "dropped"}
        aria-busy={d || proofUploadBusy}
      >
        <header className="cand-modal-header">
          <h3 className="cand-modal-title">
            {e ? "Edit candidate" : "Add candidate"}
          </h3>
          <button
            type="button"
            className="cand-modal-close"
            onClick={requestClose}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        <ResumeAutoFill
          candidateId={e?.id}
          onExtracted={(data) => {
            if (data.candidate_name && (e || !l.name))
              g("name", data.candidate_name);
            if (data.technology && (e || !l.technology))
              g("technology", data.technology);
            if (data.phone && (e || !l.phone)) g("phone", data.phone);
            if (data.email && (e || !l.email)) g("email", data.email);
          }}
        />
        <div className="cand-modal-body">
          <div className="cand-panel cand-panel--left">
            <h4 className="cand-panel-title">CANDIDATE DETAILS</h4>
            <label className="cand-field">
              <span className="cand-field-label">Candidate name *</span>
              <input
                ref={v}
                className="cand-input"
                value={l.name}
                onChange={(C) => g("name", C.target.value)}
                placeholder="e.g. NIKHIL"
                required={true}
              />
            </label>
            <label className="cand-field">
              <span className="cand-field-label">Stage</span>
              <select
                className="cand-input"
                value={l.stage}
                onChange={(C) => g("stage", C.target.value)}
              >
                {V8.map((C) => (
                  <option value={C.value} key={C.value}>
                    {C.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="cand-field">
              <span className="cand-field-label">Technology</span>
              <select
                className="cand-input"
                value={H8.includes(l.technology) ? l.technology : "_other_"}
                onChange={(C) => {
                  if (C.target.value === "_other_") {
                    g("technology", "");
                  } else {
                    g("technology", C.target.value);
                  }
                }}
              >
                <option value="">— Select —</option>
                {H8.map((C) => (
                  <option value={C} key={C}>
                    {C}
                  </option>
                ))}
                <option value="_other_">Other…</option>
              </select>
              {!H8.includes(l.technology) && l.technology !== "" && (
                <input
                  className="cand-input"
                  value={l.technology}
                  onChange={(C) => g("technology", C.target.value)}
                  placeholder="Type technology"
                  style={{ marginTop: "4px" }}
                />
              )}
            </label>
            <label className="cand-field">
              <span className="cand-field-label">Phone</span>
              <input
                className="cand-input"
                type="tel"
                value={l.phone}
                onChange={(C) =>
                  g("phone", C.target.value.replace(/[^\d+]/g, ""))
                }
                placeholder="9876543210"
              />
            </label>
            <label className="cand-field">
              <span className="cand-field-label">Email</span>
              <input
                className="cand-input"
                type="email"
                data-candidate-email-field="true"
                value={l.email}
                onChange={(C) => g("email", C.target.value)}
                placeholder="candidate@gmail.com"
                autoComplete="email"
              />
            </label>
            <label className="cand-field">
              <span className="cand-field-label">Reference</span>
              <ReferencePicker
                value={l.reference}
                onChange={(C) => g("reference", C)}
                options={refOpts}
                readOnly={a}
                placeholder="Handler name"
                title={a ? "Your handler name is set automatically" : undefined}
              />
              <span className="cand-field-hint">Earns 50% commission, plus ₹5,000 when a Profile-service candidate is completed.</span>
            </label>
            <label className="cand-field">
              <span className="cand-field-label">Date</span>
              <input
                className="cand-input"
                type="date"
                value={l.date}
                onChange={(C) => g("date", C.target.value)}
              />
            </label>
            {l.stage === "completed" && (
              <label className="cand-field">
                <span className="cand-field-label">Closure date</span>
                <input
                  className="cand-input"
                  type="date"
                  value={l.closure_date || ""}
                  onChange={(C) => g("closure_date", C.target.value)}
                />
                <span className="cand-field-hint">
                  Recorded automatically when the stage was set to Completed. The
                  ₹5,000 profile-closure complimentary counts in this month —
                  correct the date if the profile actually closed on another day.
                </span>
              </label>
            )}
            {l.service_type === "profile_service" && (
              <label className="cand-field">
                <span className="cand-field-label">
                  % on CTC{l.stage === "dropped" ? "" : " *"}
                </span>
                <input
                  className="cand-input"
                  type="number"
                  min="0.01"
                  max="100"
                  step="0.01"
                  value={l.ctc_percentage}
                  onChange={(C) => g("ctc_percentage", C.target.value)}
                  placeholder="e.g. 8.33"
                  required={l.stage !== "dropped"}
                />
              </label>
            )}
            <div className="cand-field cand-service-field">
              <span className="cand-field-label">Service type</span>
              <div className="cand-service-options">
                <label
                  className={`cand-service-card${l.service_type === "profile_service" ? " cand-service-card--on" : ""}`}
                >
                  <input
                    type="radio"
                    name="cand-service-type"
                    checked={l.service_type === "profile_service"}
                    onChange={() => m("profile_service")}
                  />
                  <span className="cand-service-card-body">
                    <strong>Profile</strong>
                    <em>₹{Cu.toLocaleString("en-IN")}</em>
                  </span>
                </label>
                <label
                  className={`cand-service-card${l.service_type === "round_wise" ? " cand-service-card--on" : ""}`}
                >
                  <input
                    type="radio"
                    name="cand-service-type"
                    checked={l.service_type === "round_wise"}
                    onChange={() => m("round_wise")}
                  />
                  <span className="cand-service-card-body">
                    <strong>Round-wise</strong>
                    <em>
                      ₹{wi.toLocaleString("en-IN")}/₹
                      {ki.toLocaleString("en-IN")}
                    </em>
                  </span>
                </label>
              </div>
            </div>
            {l.service_type === "round_wise" && (
              <div className="cand-field cand-service-scope">
                <span className="cand-field-label">Scope</span>
                <div className="cand-service-scope-options">
                  <label
                    className={`cand-service-scope-pill${!Eu(l.interview_scope) ? " cand-service-scope-pill--on" : ""}`}
                  >
                    <input
                      type="radio"
                      name="cand-interview-scope"
                      checked={!Eu(l.interview_scope)}
                      onChange={() => _("external")}
                    />
                    External (regular round) · ₹{wi.toLocaleString("en-IN")}
                  </label>
                  <label
                    className={`cand-service-scope-pill${Eu(l.interview_scope) ? " cand-service-scope-pill--on" : ""}`}
                  >
                    <input
                      type="radio"
                      name="cand-interview-scope"
                      checked={Eu(l.interview_scope)}
                      onChange={() => _("internal")}
                    />
                    Internal (joined org) · ₹{ki.toLocaleString("en-IN")}
                  </label>
                </div>
              </div>
            )}
            {l.service_type === "profile_service" && (
              <label
                className={`cand-field cand-consultancy-field${l.consultancy ? " cand-consultancy-field--on" : ""}`}
              >
                <span className="cand-field-label">Channel</span>
                <div className="cand-consultancy-toggle">
                  <input
                    type="checkbox"
                    id="cand-consultancy-cb"
                    checked={!!l.consultancy}
                    onChange={(C) => y(C.target.checked)}
                  />
                  <label
                    htmlFor="cand-consultancy-cb"
                    className="cand-consultancy-label"
                  >
                    <span className="cand-consultancy-pip" aria-hidden={true} />
                    <span className="cand-consultancy-text">
                      <strong>
                        {l.consultancy ? "Consultancy" : "Direct"}
                      </strong>
                      <em>₹{N_(l.consultancy).toLocaleString("en-IN")}</em>
                    </span>
                  </label>
                </div>
              </label>
            )}
            <div
              className="cand-field cand-bgv-field"
              style={
                l.service_type === "round_wise"
                  ? { display: "none" }
                  : undefined
              }
            >
              <label
                className={`cand-consultancy-field${l.bgv_certificates ? " cand-consultancy-field--on" : ""}`}
              >
                <span className="cand-field-label">Additional services</span>
                <div className="cand-consultancy-toggle">
                  <input
                    type="checkbox"
                    checked={!!l.bgv_certificates}
                    onChange={(C) => B(C.target.checked)}
                  />
                  <span className="cand-consultancy-label">
                    <span className="cand-consultancy-pip" />
                    <span className="cand-consultancy-text">
                      <strong>BGV certificates</strong>
                      <em>₹30,000 added to expected</em>
                    </span>
                  </span>
                </div>
              </label>
            </div>
          </div>
          <div className="cand-panel cand-panel--right">
            <h4 className="cand-panel-title">PAYMENT & FOLLOW-UP</h4>
            <label className="cand-field">
              <span className="cand-field-label">Expected ₹</span>
              <input
                className="cand-input"
                type="number"
                min="0"
                step="500"
                value={l.expected_payment}
                onChange={(C) => g("expected_payment", C.target.value)}
                placeholder={String(
                  os(l.service_type, l.consultancy, l.interview_scope),
                )}
              />
            </label>
            <label className="cand-field">
              <span className="cand-field-label">
                Received ₹
                {l.payment_is_proof_derived && (
                  <span className="cand-field-required-tag">
                    from verified proofs
                  </span>
                )}
              </span>
              <input
                className="cand-input"
                type="number"
                min="0"
                step="500"
                value={l.payment}
                /* Read-only means the figure is the system's, not the
                 * operator's. It was still submitted on save, so opening a
                 * candidate and pressing Save wrote the displayed number back
                 * over the stored one -- which is how a recorded 20,000 became
                 * 0 once two unverifiable screenshots made the display 0. A
                 * derived value is never sent. */
                onChange={(C) => {
                  if (l.payment_is_proof_derived) return;
                  g("payment", C.target.value);
                }}
                placeholder="0"
                readOnly={!!l.payment_is_proof_derived}
                disabled={!!l.payment_is_proof_derived}
                title={
                  l.payment_is_proof_derived
                    ? "Calculated from verified payment proofs. Upload, reject or remove a proof to change it."
                    : undefined
                }
              />
            </label>
            {l.payment_is_proof_derived && (
              <div className="cand-field cand-receipt-breakdown">
                <span className="cand-receipt-line">
                  Minimum expected <strong>{$n(l.expected_minimum ?? T)}</strong>
                </span>
                <span className="cand-receipt-line">
                  Verified received <strong>{$n(l.verified_received ?? k)}</strong>
                </span>
                {(l.above_minimum ?? 0) > 0 && (
                  <span className="cand-receipt-line cand-receipt-line--over">
                    Above minimum <strong>{$n(l.above_minimum)}</strong>
                  </span>
                )}
                <span className="cand-receipt-line">
                  Outstanding <strong>{$n(l.balance_due ?? 0)}</strong>
                </span>
                <span className="cand-receipt-line">
                  Verified proofs <strong>{l.verified_proof_count ?? 0}</strong>
                </span>
              </div>
            )}
            {(l.bgv_expected ?? 0) > 0 && (
              <div className="cand-field cand-bgv-summary">
                <span className="cand-bgv-summary-text">
                  BGV Consultancy: {$n(l.bgv_received ?? 0)} collected of{" "}
                  {$n(l.bgv_expected)} — managed separately
                </span>
                <a
                  className="cand-bgv-summary-link"
                  href={`/bgv?candidate=${encodeURIComponent(l.name || "")}`}
                >
                  Open BGV case →
                </a>
              </div>
            )}
            {l.payment_needs_reconciliation && (
              <div className="cand-field cand-receipt-warning">
                Verified proofs account for {$n(l.verified_proof_total ?? 0)} of the{" "}
                {$n(l.payment)} recorded — {$n(l.payment_reconciliation_gap ?? 0)}{" "}
                unevidenced. Upload the missing receipt, or confirm the recorded
                amount before reducing it.
              </div>
            )}
            {/* Money recorded and no payment proof at all.
              *
              * The reconciliation warning above cannot cover this: it fires on a
              * shortfall between proofs and the recorded amount, and with no
              * proofs there is nothing to compare, so `needs_reconciliation`
              * stays false. The row then reads "✓ Paid" beside an empty proof
              * cell, which looks exactly like a proof that failed to load. It is
              * not rare -- 8 of 31 paid candidates are in this state -- so the
              * absence is stated rather than left to be inferred from blankness.
              *
              * The amount is still correct. receipt_summary falls back to the
              * recorded figure when no proof has been adjudicated, deliberately,
              * so that rows predating proof capture do not have real money
              * erased. This says the evidence is missing, not the payment. */}
            {/* Proofs are attached but none counted.
              *
              * "Payment proofs 2 / Verified proofs 0" with nothing else said is
              * indistinguishable from a broken pipeline, and was reported as
              * one. The proofs had in fact been read, fraud-checked and priced;
              * the engine withheld credit because the payee handle in the
              * screenshot is masked (PhonePe renders it XXXXXX4573@ybl), and a
              * mask matches no registry entry and looks like every other mask
              * from that bank. Refusing it is deliberate. Not saying so is not.
              *
              * The counts come from receipt_summary, so this states the engine's
              * own verdict rather than a second opinion about it. */}
            {(l.proof_count ?? 0) > 0 && (l.verified_proof_count ?? 0) === 0 && (
              <div className="cand-field cand-receipt-missing">
                {l.proof_count === 1
                  ? "1 payment proof uploaded"
                  : `${l.proof_count} payment proofs uploaded`}{" "}
                · Manual review required
                {(() => {
                  const counts = l.payment_proof_status_counts || {};
                  const named = Object.entries(counts)
                    .filter(([status, n]) => Number(n) > 0 && status !== "VERIFIED")
                    .map(([status]) => String(status).toLowerCase().replace(/_/g, " "))
                    .join(", ");
                  return named ? ` (${named})` : "";
                })()}
              </div>
            )}
            {(l.payment ?? 0) > 0 && (l.proof_count ?? 0) === 0 && (
              <div className="cand-field cand-receipt-missing">
                No payment proof on record. {$n(l.payment)} is the recorded
                figure; upload the receipt to evidence it.
              </div>
            )}
            <div className="cand-field">
              <span className={`cand-pay-status cand-pay-status--${E}`}>
                {E === "paid" && <s.Fragment>✓ Paid ({$n(k)})</s.Fragment>}
                {E === "partial" && (
                  <s.Fragment>
                    ● {$n(k)}/{$n(T)} · <strong>{$n(S)} pending</strong>
                  </s.Fragment>
                )}
                {E === "unpaid" && <s.Fragment>○ {$n(T)} pending</s.Fragment>}
              </span>
              {k > 0 && (l.reference || "").trim() && (
                <span className="cand-pay-handler-share">
                  {/* The server owns this figure. The browser used to recompute
                      it and disagreed in both directions — capping the basis at
                      the expected amount, then zeroing it out entirely for an
                      under-paid candidate. */}
                  ↻ {l.reference.trim()} earns{" "}
                  {$n(referralCommission)}
                </span>
              )}
            </div>
            {b && (
              <label className="cand-field cand-field--required">
                <span className="cand-field-label">
                  Follow-up / Remark
                  <span className="cand-field-required-tag">
                    balance {$n(S)}
                  </span>
                </span>
                <textarea
                  className="cand-input cand-input--textarea"
                  rows={2}
                  value={l.follow_up}
                  onChange={(C) => g("follow_up", C.target.value)}
                  placeholder="Why is balance pending?"
                  required={true}
                />
              </label>
            )}
            <div className="cand-field">
              <PaymentProofUploader
                candidateId={e == null ? undefined : e.id}
                proofs={o}
                onChange={u}
                onBusyChange={setProofUploadBusy}
                controlRef={proofUploadControl}
              />
            </div>
          </div>
        </div>
        {h && <div className="cand-modal-error">{h}</div>}
        <footer className="cand-modal-footer">
          <button
            type="button"
            className="cand-btn cand-btn--ghost"
            onClick={requestClose}
            disabled={d}
          >
            Cancel
          </button>
          <button
            type="submit"
            className="cand-btn cand-btn--primary"
            disabled={d || proofUploadBusy}
            title={
              proofUploadBusy
                ? "Wait for the payment screenshot upload to finish"
                : undefined
            }
          >
            {d
              ? "Saving…"
              : proofUploadBusy
                ? "Uploading proof…"
                : e
                  ? "Save changes"
                  : "Add candidate"}
          </button>
        </footer>
      </form>
    </div>
  );
}
function cr(e) {
  const t = Number(e) || 0;
  if (t === 0) {
    return "₹0";
  } else {
    return `₹${t.toLocaleString("en-IN")}`;
  }
}
function J8({
  stats: e,
  scopeLabel: t,
  onPayoutsClick: r,
  handlerView: n = false,
  handlerName: a = null,
  scopeReference: scopeRef = null,
}) {
  var d;
  var f;
  var h;
  if (!e) {
    return null;
  }
  const scopeKey = (scopeRef || (n && a ? a : null) || "").trim().toLowerCase();
  const scopedPerf = scopeKey
    ? (e.top_performers || []).find(
        (y) => (y.name || "").trim().toLowerCase() === scopeKey,
      )
    : null;
  const i = scopedPerf
    ? scopedPerf.count || 0
    : Object.values(e.by_stage || {}).reduce((x, v) => x + v, 0);
  const l = scopedPerf
    ? scopedPerf.completed || 0
    : ((d = e.by_stage) == null ? undefined : d.completed) || 0;
  const c = scopedPerf
    ? scopedPerf.in_progress || 0
    : ((f = e.by_stage) == null ? undefined : f.in_progress) || 0;
  const o = scopedPerf
    ? scopedPerf.fail || 0
    : ((h = e.by_stage) == null ? undefined : h.fail) || 0;
  const u = i > 0 ? Math.round((l / i) * 100) : 0;
  const clientCollections = scopedPerf
    ? scopedPerf.revenue_total || 0
    : (e.client_collections_total ?? e.revenue_total);
  const referralCommission = scopedPerf
    ? scopedPerf.commission_total || 0
    : (e.referral_commission_total ?? e.handler_commission_total ?? 0);
  const revenueTotal = clientCollections;
  const revenueCompleted = scopedPerf
    ? scopedPerf.revenue_completed || 0
    : e.revenue_completed;
  const companyRevenue = scopedPerf
    ? Math.max(
        0,
        (scopedPerf.revenue_total || 0) - (scopedPerf.commission_total || 0),
      )
    : (e.company_revenue_total ??
      Math.max(0, (e.revenue_total || 0) - referralCommission));
  const companyCompleted = scopedPerf
    ? scopedPerf.company_revenue_completed ||
      Math.max(
        0,
        (scopedPerf.revenue_completed || 0) -
          (scopedPerf.auto_earnings_completed || 0),
      )
    : (e.company_revenue_completed ??
      Math.max(0, (e.revenue_completed || 0) - referralCommission));
  const pendingTotal = scopedPerf
    ? scopedPerf.pending_total || 0
    : e.pending_total;
  const pendingCount = scopedPerf
    ? scopedPerf.pending_count || 0
    : e.pending_count;
  return (
    <div className="cand-stats">
      <div className="cand-stat-card">
        <div className="cand-stat-label">
          Total candidates{t && <span className="cand-stat-scope">{t}</span>}
        </div>
        <div className="cand-stat-value">{i}</div>
        <div className="cand-stat-sub">
          {l} done · {c} active · {o} failed
        </div>
        {(e.consultancy_count || 0) > 0 && (
          <div className="cand-stat-channel">
            <span
              className="cand-channel-pill cand-channel-pill--direct"
              title={`Direct leads · ₹${(e.default_expected_payment || 20000).toLocaleString("en-IN")} baseline`}
            >
              Direct <strong>{e.direct_count || 0}</strong>
            </span>
            <span
              className="cand-channel-pill cand-channel-pill--consultancy"
              title={`Consultancy leads · ₹${(e.consultancy_expected_payment || 15000).toLocaleString("en-IN")} baseline`}
            >
              Consultancy <strong>{e.consultancy_count || 0}</strong>
            </span>
          </div>
        )}
      </div>
      <div className="cand-stat-card">
        <div className="cand-stat-label">
          Total revenue{t && <span className="cand-stat-scope">{t}</span>}
        </div>
        <div className="cand-stat-value cand-stat-value--money">
          {cr(revenueTotal)}
        </div>
        <div className="cand-stat-sub">
          From completed: {cr(revenueCompleted)}
        </div>
      </div>
      <div className="cand-stat-card">
        <div className="cand-stat-label">
          Company revenue{t && <span className="cand-stat-scope">{t}</span>}
        </div>
        <div className="cand-stat-value cand-stat-value--money">
          {cr(companyRevenue)}
        </div>
        <div className="cand-stat-sub">
          After referral {cr(referralCommission)} · completed{" "}
          {cr(companyCompleted)}
        </div>
      </div>
      <div className="cand-stat-card">
        <div className="cand-stat-label">
          Conversion{t && <span className="cand-stat-scope">{t}</span>}
        </div>
        <div className="cand-stat-value">{u}%</div>
        <div className="cand-stat-sub">
          {l} of {i} reached completed
        </div>
      </div>
      <div
        className={`cand-stat-card${(pendingCount || 0) > 0 ? " cand-stat-card--alert" : ""}`}
      >
        <div className="cand-stat-label">
          Pending collections{t && <span className="cand-stat-scope">{t}</span>}
        </div>
        <div className="cand-stat-value cand-stat-value--money cand-stat-value--alert">
          {cr(pendingTotal)}
        </div>
        <div className="cand-stat-sub">
          {(pendingCount || 0) === 0 ? (
            <s.Fragment>
              All candidates paid the ₹
              {(e.default_expected_payment || 20000).toLocaleString("en-IN")}{" "}
              baseline.
            </s.Fragment>
          ) : (
            <s.Fragment>
              <strong>{pendingCount}</strong> short of baseline
              {(e.pending_no_remark || 0) > 0 && (
                <s.Fragment>
                  {" "}
                  ·{" "}
                  <strong className="cand-stat-warn">
                    {e.pending_no_remark}
                  </strong>{" "}
                  missing remark
                </s.Fragment>
              )}
            </s.Fragment>
          )}
        </div>
      </div>
      {(() => {
        const x = scopedPerf
          ? Number(scopedPerf.auto_earnings_total) || 0
          : (e.handler_auto_earnings_total ?? e.handler_earnings_total ?? 0);
        const v = scopedPerf
          ? Number(scopedPerf.paid_out_total) || 0
          : (e.handler_paid_out_total ?? e.handler_deductions_total ?? 0);
        const g = scopedPerf ? Number(scopedPerf.net_payable) || 0 : x - v;
        const salaryTotal = scopedPerf
          ? Number(scopedPerf.salary_total) || 0
          : e.handler_salary_total || 0;
        const commissionTotal = scopedPerf
          ? Number(scopedPerf.commission_total) || 0
          : (e.handler_commission_total ?? x - salaryTotal);
        const p = e.commission_pct || 50;
        const m = (e.top_performers || [])
          .map((y) => ({
            name: y.name,
            owe: Math.max(0, Number(y.net_payable) || 0),
            base: Number(y.auto_earnings_total) || 0,
            paid: Number(y.paid_out_total) || 0,
            salary: Number(y.salary_total) || 0,
            commission:
              Number(y.commission_total ?? y.auto_earnings_total) || 0,
          }))
          .filter((y) => y.owe > 0)
          .filter((y) => !scopeKey || y.name.trim().toLowerCase() === scopeKey)
          .sort((y, k) => k.owe - y.owe);
        const _ = (e.top_performers || [])
          .map((y) => ({
            name: y.name,
            over: Math.max(0, -(Number(y.net_payable) || 0)),
          }))
          .filter((y) => y.over > 0)
          .sort((y, k) => k.over - y.over);
        return (
          <button
            type="button"
            className={`cand-stat-card cand-stat-card--clickable cand-stat-card--payouts${g > 0 ? " cand-stat-card--owe" : g < 0 ? " cand-stat-card--alert" : ""}`}
            onClick={() => (r == null ? undefined : r())}
            title={
              n
                ? "View your earnings breakdown"
                : "Click to view payout board — admin password required"
            }
          >
            <div className="cand-stat-label">
              {n ? "My earnings" : "Handler payouts"}
              {t && <span className="cand-stat-scope">{t}</span>}
              <span className="cand-stat-arrow" aria-hidden={true}>
                →
              </span>
            </div>
            <div
              className={`cand-stat-value cand-stat-value--money ${g > 0 ? "cand-stat-value--earn" : g === 0 ? "" : "cand-stat-value--alert"}`}
            >
              {n ? (
                g > 0 ? (
                  `Pending ${cr(g)}`
                ) : g === 0 ? (
                  "Settled"
                ) : (
                  `Overpaid ${cr(Math.abs(g))}`
                )
              ) : (
                <s.Fragment>
                  {g > 0 ? "Owe " : g === 0 ? "Settled" : "Overpaid "}
                  {g !== 0 && cr(Math.abs(g))}
                </s.Fragment>
              )}
            </div>
            {m.length > 0 ? (
              <ul
                className="cand-payto-list"
                aria-label={
                  n ? "Your earnings breakdown" : "Handlers still owed money"
                }
              >
                {m.slice(0, 4).map((y) => (
                  <li className="cand-payto-row" key={y.name}>
                    {!n && <span className="cand-payto-action">Pay</span>}
                    <span className="cand-payto-name">
                      {n ? "Your share" : y.name}
                      {y.salary > 0 && (
                        <em
                          className="cand-payto-mix"
                          title={`Salary ${cr(y.salary)} + commission ${cr(y.commission)} − paid ${cr(y.paid)}`}
                        >
                          salary {cr(y.salary)} + comm. {cr(y.commission)}
                        </em>
                      )}
                    </span>
                    <span className="cand-payto-amount">{cr(y.owe)}</span>
                  </li>
                ))}
                {m.length > 4 && (
                  <li className="cand-payto-more">
                    + {m.length - 4} more · click for full list
                  </li>
                )}
              </ul>
            ) : g === 0 ? (
              <div className="cand-payto-empty cand-payto-empty--settled">
                ✓ Everyone is paid up.
              </div>
            ) : null}
            {_.length > 0 && (
              <ul
                className="cand-payto-list cand-payto-list--over"
                aria-label="Handlers who have been over-paid"
              >
                {_.slice(0, 3).map((y) => (
                  <li
                    className="cand-payto-row cand-payto-row--over"
                    key={y.name}
                  >
                    <span className="cand-payto-action">Recover from</span>
                    <span className="cand-payto-name">{y.name}</span>
                    <span className="cand-payto-amount">{cr(y.over)}</span>
                  </li>
                ))}
              </ul>
            )}
            <div className="cand-stat-sub">
              <strong className="cand-net-pos">{cr(x)}</strong> owed
              {salaryTotal > 0 ? (
                <span title="Owed = salaries + 50% commission + completed-profile complimentary amounts">
                  {" "}
                  ({cr(salaryTotal)} salary + {cr(commissionTotal)} comm.)
                </span>
              ) : (
                <s.Fragment> ({p}%)</s.Fragment>
              )}{" "}
              · <strong className="cand-net-neg">{cr(v)}</strong> paid out
            </div>
          </button>
        );
      })()}
      <div className="cand-stat-card cand-stat-card--list">
        <div className="cand-stat-label">
          Top technologies (company share)
          {t && <span className="cand-stat-scope">{t}</span>}
        </div>
        <ul className="cand-stat-list">
          {(e.top_technologies || []).slice(0, 5).map((x) => (
            <li key={x.name}>
              <span className="cand-stat-list-name">{x.name}</span>
              <span className="cand-stat-list-value">{cr(x.revenue)}</span>
            </li>
          ))}
          {(e.top_technologies || []).length === 0 && (
            <li className="cand-stat-list-empty">No data yet.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
function $a(e) {
  const t = Number(e) || 0;
  if (t === 0) {
    return "₹0";
  } else if (t < 100000) {
    return `₹${t.toLocaleString("en-IN")}`;
  } else {
    return `₹${(t / 100000).toFixed(t % 100000 === 0 ? 0 : 1)}L`;
  }
}
const Q8 = [
  {
    value: "revenue_completed",
    label: "Completed ₹",
  },
  {
    value: "company_revenue_completed",
    label: "Company ₹",
  },
  {
    value: "revenue_total",
    label: "Client ₹",
  },
  {
    value: "count",
    label: "Lead count",
  },
  {
    value: "conversion_pct",
    label: "Conversion %",
  },
];
function Z8(e, t) {
  if (t === "count") {
    return {
      primary: `${e.count}`,
      primaryLabel: e.count === 1 ? "lead" : "leads",
      secondary: e.revenue_total
        ? `₹${e.revenue_total.toLocaleString("en-IN")} pipeline`
        : null,
    };
  } else if (t === "conversion_pct") {
    return {
      primary: `${e.conversion_pct}%`,
      primaryLabel: "conversion",
      secondary: `${e.completed} of ${e.count} closed`,
    };
  } else if (t === "revenue_total") {
    return {
      primary: $a(e.revenue_total),
      primaryLabel: "client",
      secondary:
        (e.company_revenue_total || 0) > 0
          ? `${$a(e.company_revenue_total)} company`
          : null,
    };
  } else if (t === "company_revenue_completed") {
    const n =
      Number(e.company_revenue_completed) ||
      Math.max(
        0,
        (Number(e.revenue_completed) || 0) -
          (Number(e.auto_earnings_completed) || 0),
      );
    const a =
      Number(e.company_revenue_total) ||
      Math.max(
        0,
        (Number(e.revenue_total) || 0) - (Number(e.commission_total) || 0),
      );
    return {
      primary: $a(n),
      primaryLabel: "company closed",
      secondary: a > n ? `${$a(a)} company total` : null,
    };
  } else {
    return {
      primary: $a(e.revenue_completed),
      primaryLabel: "closed",
      secondary:
        e.revenue_total > e.revenue_completed
          ? `${$a(e.revenue_total - e.revenue_completed)} still in pipeline`
          : null,
    };
  }
}
function eR(e, t) {
  const r = [...(e || [])];
  r.sort((n, a) => {
    const i = Number(n == null ? undefined : n[t]) || 0;
    const l = Number(a == null ? undefined : a[t]) || 0;
    if (l !== i) {
      return l - i;
    } else {
      return (
        (Number(a == null ? undefined : a.revenue_total) || 0) -
        (Number(n == null ? undefined : n.revenue_total) || 0)
      );
    }
  });
  return r;
}
function _Component26({
  stats: e,
  allStats: allStats = null,
  month: t,
  onMonthChange: r,
  monthOptions: n,
  onExpensesChanged: a,
  onShowEarnings: i,
  onEditPayout: l,
  handlerView: c = false,
  handlerName: o = null,
}) {
  const [u, d] = w.useState("revenue_completed");
  const scopedPerformers = (e == null ? undefined : e.top_performers) || [];
  const allTimePerformers =
    (allStats == null ? undefined : allStats.top_performers) || [];
  // Never blank: if the selected month has no attributed performers, fall back to all-time.
  const usingFallback =
    scopedPerformers.length === 0 && allTimePerformers.length > 0;
  const f = w.useMemo(() => {
    const base = usingFallback ? allTimePerformers : scopedPerformers;
    if (!c || !o) {
      return base;
    }
    const _ = o.trim().toLowerCase();
    return base.filter((y) => (y.name || "").trim().toLowerCase() === _);
  }, [scopedPerformers, allTimePerformers, usingFallback, c, o]);
  const h = w.useMemo(() => eR(f, u), [f, u]);
  const x = w.useMemo(
    () =>
      Math.max(1, ...h.map((m) => Number(m == null ? undefined : m[u]) || 0)),
    [h, u],
  );
  const v = w.useMemo(() => {
    if (!t || t === "all") {
      return null;
    }
    const m = (n || []).find((_) => _.value === t);
    if (m) {
      return m.label.replace(" · this month", "");
    } else {
      return t;
    }
  }, [t, n]);
  const g = w.useMemo(
    () =>
      e
        ? {
            total: e.total || 0,
            client: (e.client_collections_total ?? e.revenue_total) || 0,
            company:
              e.company_revenue_total ??
              Math.max(
                0,
                (e.revenue_total || 0) -
                  (e.referral_commission_total ??
                    e.handler_commission_total ??
                    0),
              ),
            completed:
              e.company_revenue_completed ??
              Math.max(
                0,
                (e.revenue_completed || 0) - (e.handler_commission_total ?? 0),
              ),
            label: (e.total || 0) === 1 ? "candidate" : "candidates",
          }
        : null,
    [e],
  );
  const p = c ? "My performance" : "Top performers";
  if (!f.length && !v) {
    return (
      <section className="cand-top-perf">
        <header className="cand-top-perf-header">
          <h3 className="cand-top-perf-title">{p}</h3>
          <p className="cand-top-perf-sub">
            {c
              ? "No referred candidates yet for this period."
              : "No candidates yet — add some to see who's bringing in business."}
          </p>
        </header>
      </section>
    );
  } else {
    return (
      <section className="cand-top-perf">
        <header className="cand-top-perf-header">
          <div>
            <h3 className="cand-top-perf-title">
              {p}
              {v && <span className="cand-top-perf-scope-tag">{v}</span>}
            </h3>
            <p className="cand-top-perf-sub">
              {c ? (
                <s.Fragment>
                  {o ? <strong>{o}</strong> : "Your leads"}
                  {v ? (
                    <s.Fragment>
                      {" "}
                      · <strong>{v}</strong>
                    </s.Fragment>
                  ) : null}{" "}
                  — your revenue and commission only.
                </s.Fragment>
              ) : v ? (
                <s.Fragment>
                  Scoped to <strong>{v}</strong> —{" "}
                  {usingFallback ? (
                    <s.Fragment>
                      no closes yet · showing <strong>all-time</strong>{" "}
                      performers.
                    </s.Fragment>
                  ) : (
                    "ranked by completed revenue."
                  )}
                </s.Fragment>
              ) : (
                <s.Fragment>All time — ranked by completed revenue.</s.Fragment>
              )}
              {g && (
                <s.Fragment>
                  {" "}
                  · <strong>{g.total}</strong> {g.label} ·{" "}
                  <strong>{$a(g.company)}</strong> company
                  {g.client > g.company && (
                    <s.Fragment> · {$a(g.client)} client</s.Fragment>
                  )}
                  {g.completed > 0 && (
                    <s.Fragment>
                      {" "}
                      · <strong>{$a(g.completed)}</strong> company closed
                    </s.Fragment>
                  )}
                </s.Fragment>
              )}
            </p>
          </div>
          <div className="cand-top-perf-controls">
            {i && !c && (
              <button
                type="button"
                className="cand-btn cand-btn--primary cand-top-perf-earn-btn"
                onClick={i}
                title="Open a full board with every handler's salary, commission, paid out and net owed — with chart view"
              >
                <span
                  aria-hidden={true}
                  style={{
                    marginRight: 6,
                  }}
                >
                  📊
                </span>
                Total earnings
              </button>
            )}
            {r && (n == null ? undefined : n.length) > 0 && (
              <div className="cand-top-perf-control">
                <label className="cand-top-perf-sort-label">Month</label>
                <select
                  className="cand-input cand-input--compact"
                  value={t || "all"}
                  onChange={(m) => r(m.target.value)}
                  aria-label="Filter by month"
                >
                  {n.map((m) => (
                    <option value={m.value} key={m.value}>
                      {m.label}
                    </option>
                  ))}
                </select>
              </div>
            )}
            <div className="cand-top-perf-control">
              <label className="cand-top-perf-sort-label">Sort by</label>
              <select
                className="cand-input cand-input--compact"
                value={u}
                onChange={(m) => d(m.target.value)}
              >
                {Q8.map((m) => (
                  <option value={m.value} key={m.value}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </header>
        {h.length === 0 ? (
          <p className="cand-top-perf-empty">
            {v ? (
              <s.Fragment>
                No candidates in <strong>{v}</strong>. Try a different month or
                clear the filter.
              </s.Fragment>
            ) : (
              <s.Fragment>No candidates yet.</s.Fragment>
            )}
          </p>
        ) : (
          <ol className="cand-perf-list">
            {h.map((m, _) => {
              const y = Number(m == null ? undefined : m[u]) || 0;
              const k = y === 0 ? 2 : Math.max(2, Math.round((y / x) * 100));
              const T = _ + 1;
              const S =
                T === 1
                  ? "cand-perf-rank--1"
                  : T === 2
                    ? "cand-perf-rank--2"
                    : T === 3
                      ? "cand-perf-rank--3"
                      : "";
              const E = Z8(m, u);
              const b =
                y === 0 &&
                (u === "revenue_completed" ||
                  u === "revenue_total" ||
                  u === "company_revenue_completed");
              return (
                <li
                  className="cand-perf-row"
                  key={m.ref_key || m.name.toLowerCase()}
                >
                  <span className={`cand-perf-rank ${S}`} aria-hidden={true}>
                    {T}
                  </span>
                  <div className="cand-perf-body">
                    <div className="cand-perf-line1">
                      <span className="cand-perf-name" title={m.name}>
                        {m.name}
                      </span>
                      <span className="cand-perf-headline">
                        <span
                          className={`cand-perf-revenue${b ? " cand-perf-revenue--muted" : ""}`}
                        >
                          {E.primary}
                        </span>
                        <span className="cand-perf-headline-label">
                          {E.primaryLabel}
                        </span>
                        {E.secondary && (
                          <span className="cand-perf-headline-sub">
                            · {E.secondary}
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="cand-perf-bar-wrap" aria-hidden={true}>
                      <div
                        className="cand-perf-bar"
                        style={{
                          width: `${k}%`,
                        }}
                      />
                    </div>
                    <div className="cand-perf-line2">
                      <span className="cand-perf-stat">
                        <strong>{m.count}</strong> lead
                        {m.count === 1 ? "" : "s"}
                      </span>
                      <span className="cand-perf-stat cand-perf-stat--good">
                        {m.completed} done
                      </span>
                      {m.in_progress > 0 && (
                        <span className="cand-perf-stat cand-perf-stat--info">
                          {m.in_progress} active
                        </span>
                      )}
                      {m.fail > 0 && (
                        <span className="cand-perf-stat cand-perf-stat--bad">
                          {m.fail} failed
                        </span>
                      )}
                      <span className="cand-perf-stat cand-perf-stat--muted">
                        {m.conversion_pct}% conversion
                      </span>
                      <span
                        className="cand-perf-stat cand-perf-stat--company"
                        title="Client collections minus referral commission"
                      >
                        {$a(
                          m.company_revenue_total ??
                            Math.max(
                              0,
                              (m.revenue_total || 0) -
                                (m.commission_total || 0),
                            ),
                        )}{" "}
                        company
                      </span>
                      <span className="cand-perf-stat cand-perf-stat--money">
                        {$a(m.revenue_total)} client
                      </span>
                      {(m.salary_total || 0) > 0 && (
                        <span
                          className="cand-perf-stat cand-perf-stat--salary"
                          title={`Fixed base salary · ₹${(m.salary_monthly || 0).toLocaleString("en-IN")}/month`}
                        >
                          ₹{m.salary_total.toLocaleString("en-IN")} salary
                          <em className="cand-perf-stat-em">(base)</em>
                        </span>
                      )}
                      {(m.commission_total ?? m.auto_earnings_total ?? 0) >
                        0 && (
                        <span
                          className="cand-perf-stat cand-perf-stat--earning"
                          title={`Auto-computed: ${m.commission_pct || 50}% commission plus completed-profile complimentary amounts.`}
                        >
                          ₹
                          {(
                            m.commission_total ??
                            m.auto_earnings_total ??
                            0
                          ).toLocaleString("en-IN")}{" "}
                          earnings
                          {(m.complimentary_total || 0) > 0 && (
                            <em className="cand-perf-stat-em">
                              (incl. {$a(m.complimentary_total)} complimentary)
                            </em>
                          )}
                        </span>
                      )}
                      {(m.paid_out_total || 0) > 0 && (
                        <span
                          className="cand-perf-stat cand-perf-stat--deduction"
                          title="Money already paid out to / for this handler from the ledger"
                        >
                          −₹{m.paid_out_total.toLocaleString("en-IN")} paid
                        </span>
                      )}
                      {((m.auto_earnings_total || 0) > 0 ||
                        (m.paid_out_total || 0) > 0) && (
                        <span
                          className={`cand-perf-stat cand-perf-stat--net${(m.net_payable || 0) > 0 ? " cand-perf-stat--net-pos" : " cand-perf-stat--net-neg"}`}
                          title="Net owed = commission + complimentary amounts + salary − paid out. Positive means the operator still owes the handler."
                        >
                          {(m.net_payable || 0) > 0
                            ? "Owe"
                            : (m.net_payable || 0) === 0
                              ? "Settled"
                              : "Overpaid"}{" "}
                          {$a(Math.abs(m.net_payable || 0))}
                        </span>
                      )}
                      {!c && l && (
                        <button
                          type="button"
                          className="cand-perf-exp-btn"
                          onClick={() => l(m)}
                          title="Log a payout to / for this handler (admin password required)"
                        >
                          {(m.paid_out_count || m.expenses_count || 0) > 0
                            ? `Edit ${m.paid_out_count || m.expenses_count} payout${(m.paid_out_count || m.expenses_count) === 1 ? "" : "s"}`
                            : "+ Log payout"}
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              );
            })}
          </ol>
        )}
      </section>
    );
  }
}
const B0 = [
  {
    value: "commission",
    label: "Commission payout",
  },
  {
    value: "travel",
    label: "Travel / fuel",
  },
  {
    value: "food",
    label: "Food / meals",
  },
  {
    value: "gym",
    label: "Gym / health",
  },
  {
    value: "equipment",
    label: "Equipment",
  },
  {
    value: "marketing",
    label: "Marketing",
  },
  {
    value: "software",
    label: "Software / tools",
  },
  {
    value: "other",
    label: "Other",
  },
];
const Ex = Object.fromEntries(B0.map((e) => [e.value, e.label]));
function Jc(e) {
  const t = Number(e) || 0;
  if (t) {
    return `₹${t.toLocaleString("en-IN")}`;
  } else {
    return "₹0";
  }
}
function rR(e) {
  if (!e) {
    return "—";
  }
  try {
    const t = new Date(e);
    if (Number.isNaN(t.getTime())) {
      return e;
    } else {
      return t.toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      });
    }
  } catch {
    return e;
  }
}
function _Component29({
  handlerNames: e = [],
  topPerformers: tp = [],
  ownedSummary: t,
  month: selectedMonth,
  onClose: r,
  onChanged: n,
}) {
  return (
    <PayoutModal
      handlerNames={e}
      topPerformers={tp}
      ownedSummary={t}
      initialMonth={selectedMonth}
      onClose={r}
      onChanged={n}
      apiBase={ve}
      categories={B0}
      categoryLabels={Ex}
      formatCurrency={Jc}
      formatDate={rR}
    />
  );
}
function At(e) {
  const t = Number(e) || 0;
  if (t) {
    if (Math.abs(t) < 100000) {
      return `₹${t.toLocaleString("en-IN")}`;
    } else {
      return `₹${(t / 100000).toFixed(t % 100000 === 0 ? 0 : 1)}L`;
    }
  } else {
    return "₹0";
  }
}
function _Component28({ stats: e, scopeLabel: t, onClose: r, onManage: n }) {
  const [a, i] = w.useState("table");
  w.useEffect(() => {
    function p(m) {
      if (m.key === "Escape") {
        if (r != null) {
          r();
        }
      }
    }
    document.addEventListener("keydown", p);
    return () => document.removeEventListener("keydown", p);
  }, [r]);
  const l = w.useMemo(() => {
    const p = ((e == null ? undefined : e.top_performers) || []).map((m) => {
      const _ = Number(m.salary_total) || 0;
      const y = Number(m.commission_total ?? m.auto_earnings_total) || 0;
      const k = Number(m.auto_earnings_total) || 0;
      const T = Number(m.paid_out_total) || 0;
      const S = Number(m.net_payable) || 0;
      const E = Number(m.revenue_total) || 0;
      const b = Number(m.company_revenue_total) || Math.max(0, E - y);
      return {
        name: m.name,
        leads: Number(m.count) || 0,
        completed: Number(m.completed) || 0,
        revenue: E,
        company: b,
        salary: _,
        commission: y,
        owed: k,
        paid: T,
        net: S,
      };
    });
    p.sort((m, _) =>
      m.net > 0 && _.net <= 0
        ? -1
        : _.net > 0 && m.net <= 0
          ? 1
          : m.net > 0 && _.net > 0
            ? _.net - m.net
            : m.net === 0 && _.net === 0
              ? _.owed - m.owed
              : _.net - m.net,
    );
    return p;
  }, [e]);
  const c = (e == null ? undefined : e.handler_auto_earnings_total) ?? 0;
  const o = (e == null ? undefined : e.handler_salary_total) ?? 0;
  const u = (e == null ? undefined : e.handler_commission_total) ?? c - o;
  const d = (e == null ? undefined : e.handler_paid_out_total) ?? 0;
  const f = (e == null ? undefined : e.net_handler_payout) ?? c - d;
  const h = (e == null ? undefined : e.commission_pct) || 50;
  const A =
    (e == null ? undefined : e.company_revenue_total) ??
    Math.max(
      0,
      ((e == null ? undefined : e.client_collections_total) ??
        (e == null ? undefined : e.revenue_total) ??
        0) - u,
    );
  const O = (e == null ? undefined : e.company_revenue_completed) ?? 0;
  const L = l.reduce((p, m) => p + m.company, 0);
  const x = l.filter((p) => p.net > 0).length;
  const v = l.filter((p) => p.net === 0).length;
  const g = l.filter((p) => p.net < 0).length;
  return (
    <div
      className="cand-modal-backdrop"
      onClick={(p) =>
        p.target === p.currentTarget && (r == null ? undefined : r())
      }
    >
      <div
        className="cand-modal cand-modal--xl cand-earn-modal"
        role="dialog"
        aria-modal="true"
      >
        <header className="cand-modal-header">
          <div>
            <h3 className="cand-modal-title">
              Everyone's earnings
              {t && <span className="cand-modal-scope"> · {t}</span>}
            </h3>
            <p className="cand-modal-sub cand-payout-bar">
              <span className="cand-payout-chunk">
                <strong>{l.length}</strong>{" "}
                {l.length === 1 ? "handler" : "handlers"}
              </span>
              <span
                className="cand-payout-chunk cand-payout-chunk--earn"
                title={`Salary + ${h}% commission + completed-profile complimentary amounts`}
              >
                <span className="cand-payout-pip" /> Owed{" "}
                <strong>{At(c)}</strong>
                {o > 0 && (
                  <em className="cand-earn-mix">
                    {" "}
                    ({At(o)} salary + {At(u)} comm.)
                  </em>
                )}
              </span>
              <span
                className="cand-payout-chunk cand-payout-chunk--ded"
                title="Every row already entered in the payout ledger"
              >
                <span className="cand-payout-pip" /> Paid out{" "}
                <strong>{At(d)}</strong>
              </span>
              <span
                className={`cand-payout-chunk ${f > 0 ? "cand-payout-chunk--net-pos" : f === 0 ? "cand-payout-chunk--net-zero" : "cand-payout-chunk--net-neg"}`}
              >
                <span className="cand-payout-pip" />
                {f > 0 ? "Still owe " : f === 0 ? "Settled " : "Overpaid by "}
                <strong>{At(Math.abs(f))}</strong>
              </span>
              <span
                className="cand-payout-chunk cand-payout-chunk--company"
                title="Client collections minus referral commission — what the company keeps"
              >
                <span className="cand-payout-pip" /> Company revenue{" "}
                <strong>{At(A)}</strong>
                {O > 0 && <em className="cand-earn-mix"> ({At(O)} closed)</em>}
              </span>
            </p>
          </div>
          <button
            type="button"
            className="cand-modal-close"
            onClick={r}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        <div className="cand-earn-summary">
          <span className="cand-earn-summary-pill cand-earn-summary-pill--owe">
            {x} to pay
          </span>
          <span className="cand-earn-summary-pill cand-earn-summary-pill--settled">
            {v} settled
          </span>
          {g > 0 && (
            <span className="cand-earn-summary-pill cand-earn-summary-pill--over">
              {g} over-paid
            </span>
          )}
          <span className="cand-earn-summary-spacer" />
          <div
            className="cand-earn-viewtoggle"
            role="tablist"
            aria-label="View as"
          >
            <button
              type="button"
              role="tab"
              aria-selected={a === "table"}
              className={`cand-earn-viewtoggle-btn${a === "table" ? " is-active" : ""}`}
              onClick={() => i("table")}
            >
              <span aria-hidden={true}>☰</span> Table
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={a === "chart"}
              className={`cand-earn-viewtoggle-btn${a === "chart" ? " is-active" : ""}`}
              onClick={() => i("chart")}
            >
              <span aria-hidden={true}>📊</span> Chart
            </button>
          </div>
        </div>
        {a === "chart" ? <_Component24 rows={l} pct={h} /> : null}
        <div className="cand-earn-tablewrap" hidden={a !== "table"}>
          {l.length === 0 ? (
            <div className="cand-exp-empty">
              No handlers yet — assign at least one candidate to a reference and
              they'll appear here.
            </div>
          ) : (
            <table className="cand-earn-table">
              <thead>
                <tr>
                  <th className="cand-earn-col-rank">#</th>
                  <th className="cand-earn-col-name">Handler</th>
                  <th className="cand-earn-col-leads" title="Leads / completed">
                    Leads
                  </th>
                  <th
                    className="cand-earn-col-money"
                    title="Client cash minus referral commission"
                  >
                    Company
                  </th>
                  <th
                    className="cand-earn-col-money"
                    title="Fixed monthly base salary"
                  >
                    Salary
                  </th>
                  <th
                    className="cand-earn-col-money"
                    title={`${h}% commission plus completed-profile complimentary amounts`}
                  >
                    Earnings
                  </th>
                  <th className="cand-earn-col-money" title="Salary + earnings">
                    Owed
                  </th>
                  <th
                    className="cand-earn-col-money"
                    title="From the payout ledger"
                  >
                    Paid out
                  </th>
                  <th className="cand-earn-col-status">Status</th>
                </tr>
              </thead>
              <tbody>
                {l.map((p, m) => {
                  const _ =
                    p.net > 0
                      ? "cand-earn-row--owe"
                      : p.net === 0
                        ? "cand-earn-row--settled"
                        : "cand-earn-row--over";
                  return (
                    <tr className={`cand-earn-row ${_}`} key={p.name}>
                      <td className="cand-earn-col-rank">{m + 1}</td>
                      <td className="cand-earn-col-name">
                        <span className="cand-earn-name">{p.name}</span>
                        {p.revenue > 0 && (
                          <span
                            className="cand-earn-rev"
                            title="Total client cash this handler generated"
                          >
                            {At(p.revenue)} client
                          </span>
                        )}
                      </td>
                      <td className="cand-earn-col-leads">
                        <span className="cand-earn-leads">
                          {p.leads}
                          {p.completed > 0 && (
                            <em className="cand-earn-leads-done">
                              · {p.completed} done
                            </em>
                          )}
                        </span>
                      </td>
                      <td className="cand-earn-col-money">
                        {p.company > 0 ? (
                          <span className="cand-earn-chip cand-earn-chip--company">
                            {At(p.company)}
                          </span>
                        ) : (
                          <span className="cand-earn-dash">—</span>
                        )}
                      </td>
                      <td className="cand-earn-col-money">
                        {p.salary > 0 ? (
                          <span className="cand-earn-chip cand-earn-chip--salary">
                            {At(p.salary)}
                          </span>
                        ) : (
                          <span className="cand-earn-dash">—</span>
                        )}
                      </td>
                      <td className="cand-earn-col-money">
                        {p.commission > 0 ? (
                          <span className="cand-earn-chip cand-earn-chip--commission">
                            {At(p.commission)}
                          </span>
                        ) : (
                          <span className="cand-earn-dash">—</span>
                        )}
                      </td>
                      <td className="cand-earn-col-money cand-earn-col-money--owed">
                        <strong>{At(p.owed)}</strong>
                      </td>
                      <td className="cand-earn-col-money">
                        {p.paid > 0 ? (
                          <span className="cand-earn-chip cand-earn-chip--paid">
                            {At(p.paid)}
                          </span>
                        ) : (
                          <span className="cand-earn-dash">—</span>
                        )}
                      </td>
                      <td className="cand-earn-col-status">
                        {p.net > 0 ? (
                          <span className="cand-earn-status cand-earn-status--owe">
                            Pay <strong>{At(p.net)}</strong>
                          </span>
                        ) : p.net === 0 ? (
                          <span className="cand-earn-status cand-earn-status--settled">
                            ✓ Settled
                          </span>
                        ) : (
                          <span className="cand-earn-status cand-earn-status--over">
                            Recover <strong>{At(Math.abs(p.net))}</strong>
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr className="cand-earn-foot">
                  <td colSpan={3}>Total</td>
                  <td className="cand-earn-col-money">
                    <span className="cand-earn-chip cand-earn-chip--company">
                      {At(A || L)}
                    </span>
                  </td>
                  <td className="cand-earn-col-money">
                    {o > 0 ? (
                      <span className="cand-earn-chip cand-earn-chip--salary">
                        {At(o)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="cand-earn-col-money">
                    <span className="cand-earn-chip cand-earn-chip--commission">
                      {At(u)}
                    </span>
                  </td>
                  <td className="cand-earn-col-money cand-earn-col-money--owed">
                    <strong>{At(c)}</strong>
                  </td>
                  <td className="cand-earn-col-money">
                    {d > 0 ? (
                      <span className="cand-earn-chip cand-earn-chip--paid">
                        {At(d)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="cand-earn-col-status">
                    <span
                      className={`cand-earn-status ${f > 0 ? "cand-earn-status--owe" : f === 0 ? "cand-earn-status--settled" : "cand-earn-status--over"}`}
                    >
                      {f > 0 ? (
                        <s.Fragment>
                          Pay <strong>{At(f)}</strong>
                        </s.Fragment>
                      ) : f === 0 ? (
                        "✓ Settled"
                      ) : (
                        <s.Fragment>
                          Recover <strong>{At(Math.abs(f))}</strong>
                        </s.Fragment>
                      )}
                    </span>
                  </td>
                </tr>
              </tfoot>
            </table>
          )}
        </div>
        <footer className="cand-earn-footer">
          <p className="cand-earn-foot-help">
            Owed amounts are auto-computed from candidate payments ({h}% with a
            shortfall penalty when the client paid below the prescribed tariff)
            plus any fixed monthly salary. Use the ledger below to log actual
            payouts.
          </p>
          <div className="cand-earn-foot-actions">
            <button
              type="button"
              className="cand-btn cand-btn--ghost"
              onClick={r}
            >
              Close
            </button>
            {n && (
              <button
                type="button"
                className="cand-btn cand-btn--primary"
                onClick={() => {
                  if (r != null) {
                    r();
                  }
                  n();
                }}
              >
                Manage payouts ledger →
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  );
}
function _Component24({ rows: e, pct: t }) {
  const r = Math.max(1, ...e.map((n) => Math.max(n.owed, n.paid)));
  if (e.length) {
    return (
      <div
        className="cand-earn-chart"
        role="img"
        aria-label="Earnings by handler"
      >
        <div className="cand-earn-chart-legend">
          <span className="cand-earn-chart-legend-item">
            <span className="cand-earn-chart-swatch cand-earn-chart-swatch--salary" />
            Salary (base)
          </span>
          <span className="cand-earn-chart-legend-item">
            <span className="cand-earn-chart-swatch cand-earn-chart-swatch--commission" />
            Commission ({t}%)
          </span>
          <span className="cand-earn-chart-legend-item">
            <span className="cand-earn-chart-swatch cand-earn-chart-swatch--paid" />
            Already paid out
          </span>
        </div>
        <ul className="cand-earn-chart-list">
          {e.map((n) => {
            const a = (n.owed / r) * 100;
            const i = (n.paid / r) * 100;
            const l = n.owed > 0 ? (n.salary / n.owed) * a : 0;
            const c = n.owed > 0 ? (n.commission / n.owed) * a : 0;
            return (
              <li className="cand-earn-chart-row" key={n.name}>
                <div className="cand-earn-chart-name">
                  <strong>{n.name}</strong>
                  <span
                    className={`cand-earn-chart-net ${n.net > 0 ? "cand-earn-chart-net--owe" : n.net === 0 ? "cand-earn-chart-net--settled" : "cand-earn-chart-net--over"}`}
                  >
                    {n.net > 0
                      ? `Pay ${At(n.net)}`
                      : n.net === 0
                        ? "✓ Settled"
                        : `Recover ${At(Math.abs(n.net))}`}
                  </span>
                </div>
                <div className="cand-earn-chart-bars">
                  <div
                    className="cand-earn-chart-bar"
                    aria-label="Owed breakdown"
                  >
                    <span className="cand-earn-chart-bar-label">Owed</span>
                    <div className="cand-earn-chart-bar-track">
                      {l > 0 && (
                        <span
                          className="cand-earn-chart-bar-fill cand-earn-chart-bar-fill--salary"
                          style={{
                            width: `${l}%`,
                          }}
                          title={`Salary ${At(n.salary)}`}
                        />
                      )}
                      {c > 0 && (
                        <span
                          className="cand-earn-chart-bar-fill cand-earn-chart-bar-fill--commission"
                          style={{
                            width: `${c}%`,
                          }}
                          title={`Commission ${At(n.commission)}`}
                        />
                      )}
                    </div>
                    <span className="cand-earn-chart-bar-value">
                      {At(n.owed)}
                    </span>
                  </div>
                  <div className="cand-earn-chart-bar" aria-label="Paid out">
                    <span className="cand-earn-chart-bar-label cand-earn-chart-bar-label--muted">
                      Paid
                    </span>
                    <div className="cand-earn-chart-bar-track">
                      {i > 0 && (
                        <span
                          className="cand-earn-chart-bar-fill cand-earn-chart-bar-fill--paid"
                          style={{
                            width: `${i}%`,
                          }}
                          title={`Paid out ${At(n.paid)}`}
                        />
                      )}
                    </div>
                    <span className="cand-earn-chart-bar-value cand-earn-chart-bar-value--muted">
                      {At(n.paid)}
                    </span>
                  </div>
                </div>
              </li>
            );
          })}
        </ul>
      </div>
    );
  } else {
    return (
      <div className="cand-earn-chart cand-earn-chart--empty">
        Nothing to plot yet — add candidates to populate this chart.
      </div>
    );
  }
}
function jx(e) {
  const t = Number(e) || 0;
  if (t < 1024) {
    return `${t} B`;
  } else if (t < 1048576) {
    return `${(t / 1024).toFixed(0)} KB`;
  } else {
    return `${(t / 1048576).toFixed(1)} MB`;
  }
}
function Sx(e) {
  if (!e) {
    return "";
  }
  try {
    return fmtIstDt(e);
  } catch {
    return "";
  }
}
function paymentProofAssetUrl(proof) {
  const url = String(proof?.url || "").trim();
  if (!url) return "";
  return /^(?:https?:)?\/\//i.test(url) || /^data:/i.test(url) ? url : `${ve}${url}`;
}
export function PaymentProofsModal({ candidate: e, onClose: t, onEdit: r }) {
  const [n, a] = w.useState(null);
  const [previewZoom, setPreviewZoom] = w.useState(1);
  const candidateId = String(e?.id || e?.candidate_id || e?.candidateId || "");
  const initialProofs = w.useMemo(() => normalizePaymentProofs(e), [e]);
  const [candidate, setCandidate] = w.useState(e);
  const [proofs, setProofs] = w.useState(initialProofs);
  const [loading, setLoading] = w.useState(Boolean(candidateId));
  const [loadError, setLoadError] = w.useState("");
  const [reloadKey, setReloadKey] = w.useState(0);
  const previewIndex = n ? proofs.findIndex((proof) => proof.id === n.id) : -1;
  const showPreviewAt = w.useCallback(
    (index) => {
      if (!proofs.length) return;
      const normalizedIndex = (index + proofs.length) % proofs.length;
      a(proofs[normalizedIndex]);
      setPreviewZoom(1);
    },
    [proofs],
  );

  w.useEffect(() => {
    const controller = new AbortController();
    let current = true;
    setCandidate(e);
    setProofs(initialProofs);
    setLoadError("");
    if (!candidateId) {
      setLoading(false);
      setLoadError("Unable to load payment proofs. Please try again.");
      return () => controller.abort();
    }
    setLoading(true);
    fetch(`${ve}/candidates/${encodeURIComponent(candidateId)}`, {
      credentials: "include",
      cache: "no-store",
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok || payload?.status !== "ok" || !payload?.candidate) {
          throw new Error(payload?.message || `Request failed (${response.status})`);
        }
        return payload;
      })
      .then((payload) => {
        if (!current) return;
        setCandidate(payload.candidate);
        setProofs(normalizePaymentProofs(payload));
      })
      .catch((error) => {
        if (!current || error?.name === "AbortError") return;
        setLoadError("Unable to load payment proofs. Please try again.");
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [candidateId, e, initialProofs, reloadKey]);

  w.useEffect(() => {
    function u(d) {
      if (d.key === "Escape") {
        if (n) {
          a(null);
        } else if (t != null) {
          t();
        }
      } else if (n && proofs.length > 1 && d.key === "ArrowLeft") {
        d.preventDefault();
        showPreviewAt(previewIndex - 1);
      } else if (n && proofs.length > 1 && d.key === "ArrowRight") {
        d.preventDefault();
        showPreviewAt(previewIndex + 1);
      }
    }
    document.addEventListener("keydown", u);
    return () => document.removeEventListener("keydown", u);
  }, [n, previewIndex, proofs.length, showPreviewAt, t]);
  const l = Number(candidate?.expected_payment) || 20000;
  const c = Number(candidate?.payment) || 0;
  const o = Math.max(0, l - c);
  return (
    <div
      className="cand-modal-backdrop"
      onClick={(u) =>
        u.target === u.currentTarget && (t == null ? undefined : t())
      }
    >
      <div className="cand-modal cand-modal--wide">
        <header className="cand-modal-header">
          <div>
            <h3 className="cand-modal-title">
              Payment proofs ·{" "}
              <span className="cand-handler-name">
                {candidate?.name || e?.name}
              </span>
            </h3>
            <p className="cand-modal-sub cand-payout-bar">
              <span className="cand-payout-chunk cand-payout-chunk--earn">
                <span className="cand-payout-pip" /> Received{" "}
                <strong>₹{c.toLocaleString("en-IN")}</strong>
              </span>
              <span className="cand-payout-chunk">
                of <strong>₹{l.toLocaleString("en-IN")}</strong> expected
              </span>
              {o > 0 && (
                <span className="cand-payout-chunk cand-payout-chunk--net-neg">
                  <span className="cand-payout-pip" /> Balance{" "}
                  <strong>₹{o.toLocaleString("en-IN")}</strong>
                </span>
              )}
              {loading && proofs.length === 0 ? (
                <span className="cand-payout-chunk">Loading payment proofsâ€¦</span>
              ) : (
                <span className="cand-payout-chunk">
                  <strong>{proofs.length}</strong> screenshot
                  {proofs.length === 1 ? "" : "s"} on file
                </span>
              )}
            </p>
          </div>
          <button
            type="button"
            className="cand-modal-close"
            onClick={t}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        <div className="cand-modal-body cand-modal-body--stack">
          {loadError && (
            <div className="cand-exp-empty">
              <span>{loadError}</span>{" "}
              <button
                type="button"
                className="cand-btn cand-btn--ghost cand-btn--xs"
                onClick={() => setReloadKey((value) => value + 1)}
              >
                Retry
              </button>
            </div>
          )}
          {loading && proofs.length === 0 ? (
            <div className="cand-exp-empty">Loading payment proofsâ€¦</div>
          ) : !loadError && proofs.length === 0 ? (
            <div className="cand-exp-empty">
              No payment screenshots attached to this candidate yet.
            </div>
          ) : proofs.length > 0 ? (
            <ul className="cand-proofs-grid">
              {proofs.map((u) => (
                <li className="cand-proof-card" key={u.id}>
                  <button
                    type="button"
                    className="cand-proof-thumb"
                    onClick={() => {
                      a(u);
                      setPreviewZoom(1);
                    }}
                    aria-label={`Preview ${u.note || "payment proof"}`}
                  >
                    <img
                      src={paymentProofAssetUrl(u)}
                      alt={u.note || "payment proof"}
                      loading="lazy"
                    />
                  </button>
                  <div className="cand-proof-meta">
                    <div className="cand-proof-note cand-proof-note--readonly">
                      {u.note || <em>no caption</em>}
                    </div>
                    <div className="cand-proof-sub">
                      <span>{Sx(u.uploaded_at)}</span>
                      <span>·</span>
                      <span>{jx(u.size)}</span>
                    </div>
                    <a
                      href={paymentProofAssetUrl(u)}
                      download={u.original_name || u.filename}
                      className="cand-btn cand-btn--ghost cand-btn--xs cand-proof-download"
                      onClick={(d) => d.stopPropagation()}
                    >
                      ⤓ Download
                    </a>
                  </div>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        <footer className="cand-modal-footer">
          {r && (
            <button
              type="button"
              className="cand-btn cand-btn--ghost"
              onClick={() => {
                if (t != null) {
                  t();
                }
                r(candidate || e);
              }}
              title="Open the full candidate edit form (lets you add or delete proofs)"
            >
              Edit candidate →
            </button>
          )}
          <button
            type="button"
            className="cand-btn cand-btn--primary"
            onClick={t}
          >
            Close
          </button>
        </footer>
        {n && (
          <div
            className="cand-proof-lightbox"
            onClick={() => a(null)}
            role="dialog"
            aria-modal="true"
            aria-label="Payment proof preview"
          >
            <button
              type="button"
              className="cand-proof-lightbox-close"
              onClick={() => a(null)}
              aria-label="Close preview"
            >
              ×
            </button>
            {proofs.length > 1 && (
              <>
                <button
                  type="button"
                  className="cand-proof-lightbox-nav cand-proof-lightbox-nav--prev"
                  onClick={(event) => {
                    event.stopPropagation();
                    showPreviewAt(previewIndex - 1);
                  }}
                  aria-label="Previous payment proof"
                >
                  ‹
                </button>
                <button
                  type="button"
                  className="cand-proof-lightbox-nav cand-proof-lightbox-nav--next"
                  onClick={(event) => {
                    event.stopPropagation();
                    showPreviewAt(previewIndex + 1);
                  }}
                  aria-label="Next payment proof"
                >
                  ›
                </button>
              </>
            )}
            <img
              src={paymentProofAssetUrl(n)}
              alt={n.note || "payment proof"}
              onClick={(u) => u.stopPropagation()}
              style={{ transform: `scale(${previewZoom})` }}
            />
            <div
              className="cand-proof-lightbox-tools"
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                className="cand-btn cand-btn--ghost cand-btn--xs"
                onClick={() => setPreviewZoom((value) => Math.max(0.5, value - 0.25))}
                aria-label="Zoom out"
              >
                −
              </button>
              <span>{Math.round(previewZoom * 100)}%</span>
              <button
                type="button"
                className="cand-btn cand-btn--ghost cand-btn--xs"
                onClick={() => setPreviewZoom((value) => Math.min(3, value + 0.25))}
                aria-label="Zoom in"
              >
                +
              </button>
              {proofs.length > 1 && (
                <span>
                  {previewIndex + 1} / {proofs.length}
                </span>
              )}
            </div>
            <div
              className="cand-proof-lightbox-caption"
              onClick={(u) => u.stopPropagation()}
            >
              {n.note && <strong>{n.note}</strong>}
              <span>
                {Sx(n.uploaded_at)} · {jx(n.size)}
              </span>
              <a
                href={paymentProofAssetUrl(n)}
                download={n.original_name || n.filename}
                className="cand-btn cand-btn--ghost cand-btn--xs"
              >
                Download
              </a>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
const _Component31 = PaymentProofsModal;
function _Component32({
  open: e,
  title: t,
  message: r,
  onVerified: n,
  onCancel: a,
}) {
  const [i, l] = w.useState("");
  const [c, o] = w.useState("");
  const [u, d] = w.useState(false);
  const f = w.useRef(null);
  w.useEffect(() => {
    if (!e) {
      return;
    }
    l("");
    o("");
    const x = setTimeout(() => {
      var v;
      if ((v = f.current) == null) {
        return undefined;
      } else {
        return v.focus();
      }
    }, 50);
    return () => clearTimeout(x);
  }, [e]);
  if (!e) {
    return null;
  }
  async function h(x) {
    var v;
    if ((v = x == null ? undefined : x.preventDefault) != null) {
      v.call(x);
    }
    if (!i.trim()) {
      o("Enter the admin password");
      return;
    }
    d(true);
    o("");
    try {
      const g = await fetch(`${ve}/auth/verify-admin`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          password: i,
        }),
      });
      const p = await g.json().catch(() => ({}));
      if (!g.ok) {
        o(p.detail || p.message || "Incorrect password");
        return;
      }
      if (n != null) {
        n();
      }
    } catch (g) {
      o(g.message || "Could not verify password");
    } finally {
      d(false);
    }
  }
  return (
    <div
      className="modal-backdrop confirm-backdrop"
      onClick={u ? undefined : a}
      role="presentation"
    >
      <div
        className="cand-modal cand-modal--narrow"
        role="dialog"
        aria-modal="true"
        aria-labelledby="admin-pw-title"
        onClick={(x) => x.stopPropagation()}
      >
        <header className="cand-modal-header">
          <h3 className="cand-modal-title" id="admin-pw-title">
            {t || "Admin password required"}
          </h3>
          <p className="cand-modal-sub">
            {r || "Enter the main dashboard admin password to continue."}
          </p>
        </header>
        <form className="cand-modal-body" onSubmit={h}>
          <label className="cand-field">
            <span className="cand-field-label">Password</span>
            <input
              ref={f}
              className="cand-input"
              type="password"
              autoComplete="current-password"
              value={i}
              onChange={(x) => l(x.target.value)}
              disabled={u}
            />
          </label>
          {c && (
            <p className="cand-error" role="alert">
              {c}
            </p>
          )}
          <footer className="cand-modal-footer">
            <button
              type="button"
              className="cand-btn cand-btn--ghost"
              onClick={a}
              disabled={u}
            >
              Cancel
            </button>
            <button
              type="submit"
              className="cand-btn cand-btn--primary"
              disabled={u}
            >
              {u ? "Checking…" : "Unlock"}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
const E_ = [
  {
    value: "commission",
    label: "Commission payout",
  },
  {
    value: "travel",
    label: "Travel / fuel",
  },
  {
    value: "food",
    label: "Food / meals",
  },
  {
    value: "gym",
    label: "Gym / health",
  },
  {
    value: "equipment",
    label: "Equipment",
  },
  {
    value: "marketing",
    label: "Marketing",
  },
  {
    value: "software",
    label: "Software / tools",
  },
  {
    value: "other",
    label: "Other",
  },
];
const Tx = Object.fromEntries(E_.map((e) => [e.value, e.label]));
function Qc(e) {
  const t = Number(e) || 0;
  if (t) {
    return `₹${t.toLocaleString("en-IN")}`;
  } else {
    return "₹0";
  }
}
function oR(e) {
  if (!e) {
    return "—";
  }
  try {
    const t = new Date(e);
    if (Number.isNaN(t.getTime())) {
      return e;
    } else {
      return t.toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      });
    }
  } catch {
    return e;
  }
}
function _Component30({ handler: e, onClose: t, onChanged: r }) {
  var J;
  const [n, a] = w.useState([]);
  const [i, l] = w.useState(0);
  const [c, o] = w.useState(true);
  const [u, d] = w.useState("");
  const [f, h] = w.useState("all");
  const [x, v] = w.useState([]);
  const [g, p] = w.useState(null);
  const [m, _] = w.useState(() => ({
    reference: (e == null ? undefined : e.name) || "",
    amount: "",
    category: "commission",
    note: "",
    date: new Date().toISOString().slice(0, 10),
  }));
  const [y, k] = w.useState(false);
  const T = w.useCallback(async () => {
    if (e != null && e.name) {
      o(true);
      d("");
      try {
        const G = new URLSearchParams();
        G.set("reference", e.name);
        if (f !== "all") {
          G.set("month", f);
        }
        const ee = await (
          await fetch(`${ve}/handler-expenses?${G.toString()}`)
        ).json();
        if (ee.status === "ok") {
          a(ee.expenses || []);
          l(ee.total || 0);
          v(ee.available_months || []);
        } else {
          d(ee.message || "Failed to load");
        }
      } catch (G) {
        d(G.message || "Network error");
      } finally {
        o(false);
      }
    }
  }, [e == null ? undefined : e.name, f]);
  w.useEffect(() => {
    T();
  }, [T]);
  w.useEffect(() => {
    function G(ce) {
      if (ce.key === "Escape") {
        if (t != null) {
          t();
        }
      }
    }
    document.addEventListener("keydown", G);
    return () => document.removeEventListener("keydown", G);
  }, [t]);
  function S() {
    p(null);
    _({
      reference: (e == null ? undefined : e.name) || "",
      amount: "",
      category: "commission",
      note: "",
      date: new Date().toISOString().slice(0, 10),
    });
  }
  function E(G) {
    p(G);
    _({
      reference: G.reference || (e == null ? undefined : e.name) || "",
      amount: String(G.amount || ""),
      category: G.category || "other",
      note: G.note || "",
      date: G.date || new Date().toISOString().slice(0, 10),
    });
  }
  async function b(G) {
    var ee;
    if ((ee = G == null ? undefined : G.preventDefault) != null) {
      ee.call(G);
    }
    if (!m.reference.trim()) {
      d("Handler name is required");
      return;
    }
    const ce = Number(m.amount);
    if (!Number.isFinite(ce) || ce <= 0) {
      d("Amount must be greater than zero");
      return;
    }
    k(true);
    d("");
    try {
      const B = {
        reference: m.reference.trim(),
        amount: ce,
        category: m.category,
        note: m.note.trim(),
        date: m.date,
      };
      const Z = g ? `${ve}/handler-expenses/${g.id}` : `${ve}/handler-expenses`;
      const U = await (
        await fetch(Z, {
          method: g ? "PATCH" : "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(B),
        })
      ).json();
      if (U.status !== "ok") {
        d(U.message || "Save failed");
        return;
      }
      S();
      T();
      if (r != null) {
        r();
      }
    } catch (B) {
      d(B.message || "Network error");
    } finally {
      k(false);
    }
  }
  async function A(G) {
    if (
      window.confirm(
        `Delete this ₹${G.amount.toLocaleString("en-IN")} ${Tx[G.category] || G.category} expense?`,
      )
    ) {
      try {
        const ee = await (
          await fetch(`${ve}/handler-expenses/${G.id}`, {
            method: "DELETE",
          })
        ).json();
        if (ee.status === "ok") {
          T();
          if (r != null) {
            r();
          }
        } else {
          d(ee.message || "Delete failed");
        }
      } catch (ce) {
        d(ce.message || "Network error");
      }
    }
  }
  const O = w.useMemo(
    () => [
      {
        value: "all",
        label: "All time",
      },
      ...x.map((G) => ({
        value: G.value,
        label: G.is_current ? `${G.label} · this month` : G.label,
      })),
    ],
    [x],
  );
  const L = w.useMemo(
    () => n.reduce((G, ce) => G + (Number(ce.amount) || 0), 0),
    [n],
  );
  const M =
    Number(e == null ? undefined : e.auto_earnings_total) ||
    Number(e == null ? undefined : e.earnings_total) ||
    0;
  const C = Number(e == null ? undefined : e.commission_pct) || 50;
  const Y = M - L;
  return (
    <div
      className="cand-modal-backdrop"
      onClick={(G) =>
        G.target === G.currentTarget && (t == null ? undefined : t())
      }
    >
      <div className="cand-modal cand-modal--wide">
        <header className="cand-modal-header">
          <div>
            <h3 className="cand-modal-title">
              Payout ledger ·{" "}
              <span className="cand-handler-name">
                {(e == null ? undefined : e.name) || "Handler"}
              </span>
            </h3>
            <p className="cand-modal-sub cand-payout-bar">
              {(e == null ? undefined : e.count) != null && (
                <span className="cand-payout-chunk">
                  <strong>{e.count}</strong> lead{e.count === 1 ? "" : "s"}
                </span>
              )}
              <span
                className="cand-payout-chunk cand-payout-chunk--earn"
                title={`${C}% with shortfall penalty when client paid below prescribed tariff`}
              >
                <span className="cand-payout-pip" /> Owed ({C}%){" "}
                <strong>{Qc(M)}</strong>
              </span>
              <span
                className="cand-payout-chunk cand-payout-chunk--ded"
                title="Sum of every row in this ledger"
              >
                <span className="cand-payout-pip" /> Paid out{" "}
                <strong>{Qc(L)}</strong>
              </span>
              <span
                className={`cand-payout-chunk ${Y > 0 ? "cand-payout-chunk--net-pos" : Y === 0 ? "cand-payout-chunk--net-zero" : "cand-payout-chunk--net-neg"}`}
              >
                <span className="cand-payout-pip" />
                {Y > 0 ? "Still owe " : Y === 0 ? "Settled " : "Overpaid by "}
                <strong>{Qc(Math.abs(Y))}</strong>
              </span>
              {f !== "all" && (
                <span className="cand-payout-chunk cand-payout-chunk--muted">
                  scope:{" "}
                  {((J = x.find((G) => G.value === f)) == null
                    ? undefined
                    : J.label) || f}
                </span>
              )}
            </p>
          </div>
          <button
            type="button"
            className="cand-modal-close"
            onClick={t}
            aria-label="Close"
          >
            ×
          </button>
        </header>
        <div className="cand-modal-body cand-modal-body--stack">
          <form className="cand-exp-form cand-exp-form--payout" onSubmit={b}>
            <label className="cand-field cand-exp-field--amount">
              <span className="cand-field-label">
                Amount (₹) *
                <span className="cand-exp-kind-tag cand-exp-kind-tag--payout">
                  subtracted from what's owed
                </span>
              </span>
              <input
                className="cand-input"
                type="number"
                min="0"
                step="100"
                value={m.amount}
                onChange={(G) =>
                  _((ce) => ({
                    ...ce,
                    amount: G.target.value,
                  }))
                }
                placeholder="5000"
                required={true}
              />
            </label>
            <label className="cand-field cand-exp-field--cat">
              <span className="cand-field-label">Category</span>
              <select
                className="cand-input"
                value={m.category}
                onChange={(G) =>
                  _((ce) => ({
                    ...ce,
                    category: G.target.value,
                  }))
                }
              >
                {E_.map((G) => (
                  <option value={G.value} key={G.value}>
                    {G.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="cand-field cand-exp-field--date">
              <span className="cand-field-label">Date</span>
              <input
                className="cand-input"
                type="date"
                value={m.date}
                onChange={(G) =>
                  _((ce) => ({
                    ...ce,
                    date: G.target.value,
                  }))
                }
              />
            </label>
            <label className="cand-field cand-exp-field--note">
              <span className="cand-field-label">Note</span>
              <input
                className="cand-input"
                value={m.note}
                onChange={(G) =>
                  _((ce) => ({
                    ...ce,
                    note: G.target.value,
                  }))
                }
                placeholder="e.g. May referral bonus · taxi to client meeting"
              />
            </label>
            <div className="cand-exp-form-actions">
              {g && (
                <button
                  type="button"
                  className="cand-btn cand-btn--ghost"
                  onClick={S}
                >
                  Cancel edit
                </button>
              )}
              <button
                type="submit"
                className="cand-btn cand-btn--primary"
                disabled={y}
              >
                {y ? "Saving…" : g ? "Save changes" : "+ Log payout"}
              </button>
            </div>
          </form>
          {u && <div className="cand-modal-error">{u}</div>}
          <div className="cand-exp-list-header">
            <span className="cand-field-label">
              Ledger<span className="cand-exp-count">{n.length}</span>
            </span>
            <select
              className="cand-input cand-input--compact"
              value={f}
              onChange={(G) => h(G.target.value)}
              aria-label="Filter by month"
            >
              {O.map((G) => (
                <option value={G.value} key={G.value}>
                  {G.label}
                </option>
              ))}
            </select>
          </div>
          {c ? (
            <div className="cand-exp-empty">Loading…</div>
          ) : n.length === 0 ? (
            <div className="cand-exp-empty">
              No expenses logged{f !== "all" ? " for this month" : ""}. Use the
              form above to add the first one.
            </div>
          ) : (
            <ul className="cand-exp-list">
              {n.map((G) => (
                <li
                  className={`cand-exp-row${(g == null ? undefined : g.id) === G.id ? " cand-exp-row--editing" : ""}`}
                  key={G.id}
                >
                  <div className="cand-exp-row-main">
                    <span className="cand-exp-amount">{Qc(G.amount)}</span>
                    <span
                      className={`cand-exp-cat cand-exp-cat--${G.category}`}
                    >
                      {Tx[G.category] || G.category}
                    </span>
                    <span className="cand-exp-date">{oR(G.date)}</span>
                  </div>
                  {G.note && <div className="cand-exp-note">{G.note}</div>}
                  <div className="cand-exp-row-actions">
                    <button
                      type="button"
                      className="cand-btn cand-btn--ghost cand-btn--xs"
                      onClick={() => E(G)}
                      title="Edit"
                    >
                      ✎
                    </button>
                    <button
                      type="button"
                      className="cand-btn cand-btn--ghost cand-btn--xs cand-btn--danger-ghost"
                      onClick={() => A(G)}
                      title="Delete"
                    >
                      🗑
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
        <footer className="cand-modal-footer">
          <button
            type="button"
            className="cand-btn cand-btn--ghost"
            onClick={t}
          >
            Close
          </button>
        </footer>
      </div>
    </div>
  );
}
const dR = [
  {
    value: "all",
    label: "All stages",
  },
  {
    value: "in_progress",
    label: "In progress",
  },
  {
    value: "completed",
    label: "Closed / Completed",
  },
  {
    value: "fail",
    label: "Rejected",
  },
  {
    value: "dropped",
    label: "Dropped",
  },
];
function fR(e) {
  return (
    {
      completed: {
        label: "Closed / Completed",
        cls: "cand-badge--good",
      },
      in_progress: {
        label: "In progress",
        cls: "cand-badge--info",
      },
      fail: {
        label: "Rejected",
        cls: "cand-badge--bad",
      },
      dropped: {
        label: "Dropped",
        cls: "cand-badge--muted",
      },
    }[e] || {
      label: e || "—",
      cls: "cand-badge--muted",
    }
  );
}
function Cx(e) {
  const t = Number(e) || 0;
  if (t) {
    return `₹${t.toLocaleString("en-IN")}`;
  } else {
    return "—";
  }
}
function Ax(e) {
  const t = Number(e) || 0;
  if (t) {
    if (t < 1000) {
      return `₹${t}`;
    } else if (t < 100000) {
      return `₹${(t / 1000).toFixed(t % 1000 === 0 ? 0 : 1)}k`;
    } else {
      return `₹${(t / 100000).toFixed(t % 100000 === 0 ? 0 : 1)}L`;
    }
  } else {
    return "₹0";
  }
}
function _Component27({ row: e, onViewProofs: t }) {
  const r = Number(e.expected_payment) || 20000;
  const n = Number(e.payment) || 0;
  const a = Math.max(0, r - n);
  const i =
    e.payment_status || (n <= 0 ? "unpaid" : n >= r ? "paid" : "partial");
  const l = normalizePaymentProofs(e).length;
  const c =
    l > 0 ? (
      <button
        type="button"
        className="cand-pay-proofs cand-pay-proofs--btn"
        onClick={(o) => {
          o.stopPropagation();
          if (t != null) {
            t(e);
          }
        }}
        title={`View ${l} payment screenshot${l === 1 ? "" : "s"}`}
      >
        <span aria-hidden={true}>📎</span> {l}
        <span className="cand-pay-proofs-cta">View</span>
      </button>
    ) : null;
  if (i === "paid") {
    return (
      <div className="cand-cell-money cand-pay">
        <span className="cand-pay-amount">{Cx(n)}</span>
        <span className="cand-pay-pillrow">
          <span className="cand-pay-pill cand-pay-pill--paid">Paid</span>
          {c}
        </span>
      </div>
    );
  } else {
    return (
      <div className="cand-cell-money cand-pay">
        <span className="cand-pay-amount">
          {n > 0 ? Cx(n) : <span className="cand-pay-zero">₹0</span>}
          <span className="cand-pay-expected"> / {Ax(r)}</span>
        </span>
        <span className="cand-pay-pillrow">
          <span
            className={`cand-pay-pill cand-pay-pill--${i === "unpaid" ? "unpaid" : "partial"}`}
            title={e.follow_up || ""}
          >
            {Ax(a)} due
          </span>
          {c}
        </span>
      </div>
    );
  }
}
function pR(e) {
  if (!e) {
    return "—";
  }
  try {
    const t = new Date(e);
    if (Number.isNaN(t.getTime())) {
      return e;
    } else {
      return t.toLocaleDateString("en-IN", {
        day: "2-digit",
        month: "short",
        year: "numeric",
      });
    }
  } catch {
    return e;
  }
}
function mR() {
  const e = new Date();
  return `${e.getUTCFullYear()}-${String(e.getUTCMonth() + 1).padStart(2, "0")}`;
}
function CandidatesPanelImpl() {
  const { role: e, reference: t, enabled: r } = wu();
  // Handlers can see their own earnings, never the payout board for everyone.
  const n = e === "handler";
  const a = !r || e === "admin";
  const [i, l] = w.useState([]);
  const [c, o] = w.useState(null);
  const [u, d] = w.useState(null);
  const [f, h] = w.useState(true);
  const [x, v] = w.useState("");
  const [g, p] = w.useState("all");
  const [m, _] = w.useState(() => mR());
  const [y, k] = w.useState(false);
  const [service, setService] = w.useState("all");
  const [T, S] = w.useState("all");
  const [E, b] = w.useState("");
  const [A, O] = w.useState("");
  const [L, M] = w.useState(false);
  const [C, Y] = w.useState(null);
  const [J, G] = w.useState(false);
  const [ce, ee] = w.useState(false);
  const [B, Z] = w.useState(null);
  const [candTab, setCandTab] = w.useState("candidates");
  const [P, j] = w.useState(null);
  const [ro, setRo] = w.useState(false);
  const [showExpenditure, setShowExpenditure] = w.useState(false);
  const { confirm: U } = nc();
  const { gate: W, closeGate: H, runProtected: re } = cR();
  const ue = () =>
    re(() => G(true), {
      title: "Manage expenses",
      message:
        "Enter the admin password to view or edit handler payouts and expenses.",
    });
  const pe = () => {
    if (n) {
      ee(true);
      return;
    }
    re(() => ee(true), {
      title: "Handler payouts",
      message:
        "Enter the admin password to open the full earnings and payout board.",
    });
  };
  w.useEffect(() => {
    if (n && t) {
      S(t);
    }
  }, [n, t]);
  w.useEffect(() => {
    const ge = setTimeout(() => O(E.trim()), 250);
    return () => clearTimeout(ge);
  }, [E]);
  const fe = w.useCallback(async () => {
    h(true);
    v("");
    try {
      const ge = new URLSearchParams();
      if (g !== "all") {
        ge.set("stage", g);
      }
      if (m !== "all") {
        ge.set("month", m);
      }
      if (y) {
        ge.set("pending_only", "1");
      }
      if (service !== "all") {
        ge.set("service_type", service);
      }
      if (T !== "all") {
        ge.set("reference", T);
      }
      if (A) {
        ge.set("search", A);
      }
      const Ge = new URLSearchParams();
      if (m !== "all") {
        Ge.set("month", m);
      }
      if (T !== "all") {
        Ge.set("reference", T);
      }
      if (service !== "all") {
        Ge.set("service_type", service);
      }
      const Ze = [
        fetch(`${ve}/candidates?${ge.toString()}`),
        fetch(`${ve}/candidates/stats?${Ge.toString()}`),
      ];
      if (m !== "all" && a) {
        const allMonthParams = new URLSearchParams();
        if (T !== "all") {
          allMonthParams.set("reference", T);
        }
        if (service !== "all") {
          allMonthParams.set("service_type", service);
        }
        Ze.push(fetch(`${ve}/candidates/stats?${allMonthParams.toString()}`));
      }
      const [Be, Xe, je] = await Promise.all(Ze);
      const Tt = await Be.json();
      const ot = await Xe.json();
      const xt = je ? await je.json() : ot;
      if (Tt.status === "ok") {
        l(Tt.candidates || []);
      } else {
        v(Tt.message || "Failed to load candidates");
      }
      if (ot.status === "ok") {
        o(ot.stats);
      }
      if ((xt == null ? undefined : xt.status) === "ok") {
        d(xt.stats);
      }
    } catch (ge) {
      v(ge.message || "Network error");
    } finally {
      h(false);
    }
  }, [g, m, y, service, T, A, a]);
  w.useEffect(() => {
    fe();
  }, [fe]);
  w.useEffect(() => {
    if (
      !a ||
      !window.__TA_AI_RECRUITMENT_ENABLED__ ||
      candTab !== "candidates" ||
      f
    )
      return;
    const buttons = [];
    requestAnimationFrame(() => {
      document
        .querySelectorAll(".cand-page .cand-table tbody tr[data-cid]")
        .forEach((row) => {
          const actions = row.querySelector(".cand-cell-actions");
          if (!actions || actions.querySelector("[data-candidate-mailbox]"))
            return;
          const button = document.createElement("button");
          button.type = "button";
          button.className = "cand-btn cand-btn--ghost cand-btn--xs";
          button.textContent = "AI Mail";
          button.dataset.candidateMailbox = "1";
          button.title = "Open candidate mailbox and recruitment timeline";
          button.onclick = (event) => {
            event.stopPropagation();
            sessionStorage.setItem(
              "ai-mail-candidate-id",
              row.dataset.cid || "",
            );
            window.dispatchEvent(
              new CustomEvent("teleautomation:navigate", {
                detail: { view: "ai-recruitment" },
              }),
            );
          };
          actions.prepend(button);
          buttons.push(button);
        });
    });
    return () => buttons.forEach((button) => button.remove());
  }, [a, candTab, f, i]);
  w.useEffect(() => {
    // Service filter is now rendered in React — no DOM manipulation needed
  }, [service]);
  w.useEffect(() => {
    if (!c) return;
    const statsRoot = document.querySelector(".cand-page .cand-stats");
    if (!statsRoot) return;
    const money = (value) => `₹${(Number(value) || 0).toLocaleString("en-IN")}`;
    const makeLine = (parent, left, right = "") => {
      const line = document.createElement("div");
      line.className = "cand-breakdown-line";
      const name = document.createElement("span");
      name.textContent = left;
      const value = document.createElement("strong");
      value.textContent = right;
      line.append(name, value);
      parent.append(line);
    };
    const openBreakdown = async (label) => {
      // Render breakdown inline below the stats cards instead of a popup
      if (!statsRoot || !statsRoot.parentElement) return;
      let inlineContainer = statsRoot.parentElement.querySelector(
        ".cand-breakdown-inline",
      );
      if (!inlineContainer) {
        inlineContainer = document.createElement("div");
        inlineContainer.className = "cand-breakdown-inline";
        statsRoot.after(inlineContainer);
      }
      inlineContainer.dataset.label = label;
      inlineContainer.innerHTML = `<h3 class="cand-breakdown-inline__title">${label} breakdown<span class="cand-breakdown-inline__sub">${m === "all" ? "All time" : m}${T !== "all" ? " · " + T : ""}</span></h3><div class="cand-breakdown-inline__content"></div>`;
      const body = inlineContainer.querySelector(
        ".cand-breakdown-inline__content",
      );
      // Stage-based breakdowns (Total candidates, Conversion) use already-loaded
      // stats — no fetch needed, so they render instantly without a loading flash.
      const needsFetch = [
        "Company revenue",
        "Total revenue",
        "Pending collections",
      ].includes(label);
      let rows = [];
      if (needsFetch) {
        body.innerHTML = '<p class="cand-exp-empty">Loading…</p>';
        try {
          const params = new URLSearchParams();
          if (m !== "all") params.set("month", m);
          if (T !== "all") params.set("reference", T);
          if (service !== "all") params.set("service_type", service);
          const result = await (
            await fetch(`${ve}/candidates?${params.toString()}`, {
              credentials: "include",
            })
          ).json();
          rows = result.status === "ok" ? result.candidates || [] : [];
        } catch (_) {}
      }
      body.innerHTML = "";
      const total = document.createElement("div");
      total.className = "cand-breakdown-total";
      const lines = document.createElement("div");
      lines.className = "cand-breakdown-lines";
      if (label === "Company revenue") {
        const received = rows.reduce(
          (sum, row) => sum + (Number(row.payment) || 0),
          0,
        );
        const handlerEarnings = rows.reduce(
          (sum, row) => sum + (Number(row.total_handler_earnings) || 0),
          0,
        );
        const company = received - handlerEarnings;
        total.textContent = `${money(received)} client collections − ${money(handlerEarnings)} handler earnings = ${money(company)} company revenue`;
        rows
          .filter((row) => Number(row.payment) > 0)
          .forEach((row) =>
            makeLine(
              lines,
              `${row.name} · ${money(row.payment)} received − ${money(row.total_handler_earnings)} handler earnings`,
              money(
                (Number(row.payment) || 0) -
                  (Number(row.total_handler_earnings) || 0),
              ),
            ),
          );
      } else if (label === "Total revenue") {
        const received = rows.reduce(
          (sum, row) => sum + (Number(row.payment) || 0),
          0,
        );
        total.textContent = `${money(received)} received from ${rows.filter((row) => Number(row.payment) > 0).length} candidate${rows.filter((row) => Number(row.payment) > 0).length === 1 ? "" : "s"}`;
        rows
          .filter((row) => Number(row.payment) > 0)
          .forEach((row) => makeLine(lines, row.name, money(row.payment)));
      } else if (label === "Pending collections") {
        const pending = rows.filter((row) => Number(row.balance_due) > 0);
        total.textContent = `${money(pending.reduce((sum, row) => sum + (Number(row.balance_due) || 0), 0))} still pending from ${pending.length} candidate${pending.length === 1 ? "" : "s"}`;
        pending.forEach((row) =>
          makeLine(
            lines,
            `${row.name} · expected ${money(row.expected_payment)} · received ${money(row.payment)}`,
            money(row.balance_due),
          ),
        );
      } else if (label === "Conversion") {
        const stages = ["completed", "in_progress", "fail", "dropped"];
        total.textContent = `${c.by_stage?.completed || 0} completed of ${c.total || 0} candidates`;
        stages.forEach((stage) =>
          makeLine(
            lines,
            stage.replace("_", " "),
            String(c.by_stage?.[stage] || 0),
          ),
        );
      } else if (label === "Total candidates") {
        total.textContent = `${c.total || 0} candidate${(c.total || 0) === 1 ? "" : "s"} in this view`;
        ["completed", "in_progress", "fail", "dropped"].forEach((stage) =>
          makeLine(
            lines,
            stage.replace("_", " "),
            String(c.by_stage?.[stage] || 0),
          ),
        );
      } else if (label.startsWith("Top technologies")) {
        total.textContent = "Company share by technology";
        (c.top_technologies || []).forEach((item) =>
          makeLine(lines, item.name, money(item.revenue)),
        );
      }
      body.append(total, lines);
      if (!lines.children.length) {
        const empty = document.createElement("p");
        empty.className = "cand-exp-empty";
        empty.textContent = "No matching records for this view.";
        body.append(empty);
      }
    };
    const cards = Array.from(
      statsRoot.querySelectorAll(
        ".cand-stat-card:not(.cand-stat-card--payouts)",
      ),
    );
    cards.forEach((card) => {
      const label = card
        .querySelector(".cand-stat-label")
        ?.childNodes[0]?.textContent?.trim();
      if (!label) return;
      if (candTab === "overview") {
        card.classList.add("cand-stat-card--clickable");
        card.title = `View ${label.toLowerCase()} calculation`;
        card.onclick = () => openBreakdown(label);
      } else {
        card.classList.remove("cand-stat-card--clickable");
        card.title = "";
        card.onclick = null;
      }
    });
    // Auto-show breakdown inline on overview — preserve the user's selected
    // card across stats refreshes instead of snapping back to the default.
    if (candTab === "overview") {
      const existing =
        statsRoot.parentElement &&
        statsRoot.parentElement.querySelector(".cand-breakdown-inline");
      const prevLabel =
        existing && existing.dataset ? existing.dataset.label : "";
      openBreakdown(prevLabel || "Total candidates");
    } else {
      // Remove inline breakdown when not on overview tab
      document
        .querySelectorAll(".cand-page .cand-breakdown-inline")
        .forEach((el) => el.remove());
    }
    return () => {
      cards.forEach((card) => {
        card.onclick = null;
      });
      document
        .querySelectorAll(".cand-page .cand-breakdown-inline")
        .forEach((el) => el.remove());
    };
  }, [c, m, T, candTab]);
  w.useEffect(() => {
    if (f) return;
    // Peek at intent without consuming
    let intentRaw;
    try {
      intentRaw = sessionStorage.getItem("cand-open-pending");
    } catch {}
    if (!intentRaw) return;
    let intent;
    try {
      intent = JSON.parse(intentRaw);
    } catch {
      return;
    }

    // Ensure we're on "candidates" tab and "all" month filter
    if (candTab !== "candidates") {
      setCandTab("candidates");
      return;
    }
    if (m !== "all") {
      _("all");
      return;
    }

    // Wait for data to load
    if (!i.length) return;

    const targetName = String(intent.candidate_name || "")
      .trim()
      .toLowerCase();
    const targetId = String(intent.candidate_id || "");
    const target =
      i.find((row) => String(row.id) === targetId) ||
      i.find(
        (row) =>
          String(row.name || "")
            .trim()
            .toLowerCase() === targetName,
      );
    if (!target) return;

    // Found it — consume the intent
    try {
      sessionStorage.removeItem("cand-open-pending");
    } catch {}

    // Mail monitoring never triggers payment automatically. This intent only
    // opens the existing candidate editor so an operator can follow up.
    if (intent.action === "payment-follow-up") {
      I(target);
      requestAnimationFrame(() =>
        document
          .querySelector(".cand-panel--right")
          ?.scrollIntoView({ behavior: "smooth", block: "center" }),
      );
      return;
    }

    // Scroll to the row in the table and highlight it
    requestAnimationFrame(() => {
      const rows = document.querySelectorAll(".cand-page .cand-table tbody tr");
      for (const row of rows) {
        if (row.textContent?.toLowerCase().includes(targetName)) {
          row.scrollIntoView({ behavior: "smooth", block: "center" });
          row.style.transition = "box-shadow 0.3s, background 0.3s";
          row.style.boxShadow =
            "0 0 0 2px #fbbf24, 0 0 16px rgba(251,191,36,0.5)";
          row.style.background = "rgba(251,191,36,0.1)";
          setTimeout(() => {
            row.style.boxShadow = "";
            row.style.background = "";
          }, 4000);
          break;
        }
      }
    });
  }, [i, f, m, candTab]);
  w.useEffect(() => {
    const timer = setTimeout(() => {
      const table = document.querySelector(".cand-page .cand-table");
      if (!table) return;
      const header = table.querySelector("thead tr");
      // Remove old DOM-added resume header if present (React now renders it)
      const oldResumeHeader = header?.querySelector("[data-resume-column]");
      if (oldResumeHeader) oldResumeHeader.remove();
      const serviceHeader = Array.from(
        header?.querySelectorAll("th") || [],
      ).find(
        (cell) =>
          cell.textContent.trim() === "Slot" ||
          cell.textContent.trim() === "Service type",
      );
      if (serviceHeader) {
        serviceHeader.textContent = "Service type";
        serviceHeader.title = "Profile-wise or round-wise support";
      }
      const rows = Array.from(table.querySelectorAll("tbody tr"));
      const sortedCandidates = i.slice().sort((a2, b2) => {
        const da = a2.logged_date || a2.date || "";
        const db = b2.logged_date || b2.date || "";
        return db.localeCompare(da);
      });
      rows.forEach((row, index) => {
        // Resume column is now rendered by React — only do non-resume DOM patches here
        // Remove any old DOM-added resume cells
        row
          .querySelectorAll(".cand-cell-resume:not([data-react])")
          .forEach((el) => {
            // Only remove if it's a DOM-added one (React ones have the react cell inside)
            if (!el.querySelector(".cand-resume-cell-react")) el.remove();
          });
        const rowCid =
          row.getAttribute("data-cid") ||
          (row.querySelector(".cand-cid") || {}).textContent ||
          "";
        const candidate = rowCid
          ? i.find((c) => c && c.id === rowCid) ||
            sortedCandidates.find((c) => c && c.id === rowCid)
          : sortedCandidates[index];
        if (!candidate || !candidate.id) return;
        const nameCell = row.querySelector(".cand-cell-name");
        nameCell?.querySelector(".cand-row-complete")?.remove();
        if (candidate.details_complete && nameCell) {
          const complete = document.createElement("span");
          complete.className = "cand-row-complete";
          complete.title = "All required candidate details are entered";
          complete.setAttribute("aria-label", "Details complete");
          complete.textContent = "✓";
          nameCell.append(complete);
        }
        // Hide service badge from name cell (legacy DOM badges)
        const allBadges = row.querySelectorAll(
          ".cand-cell-name .cand-channel-tag, .cand-cell-name .cand-service-badge",
        );
        allBadges.forEach((b) => (b.style.display = "none"));
      });
    }, 0);
    return () => clearTimeout(timer);
  }, [i, f]);
  const q = () => {
    Y(null);
    M(true);
  };
  const I = async (ge) => {
    let candidate = ge;
    try {
      const response = await fetch(`${ve}/candidates/${ge.id}`, {
        credentials: "include",
        cache: "no-store",
      });
      const payload = await response.json();
      if (payload.status === "ok" && payload.candidate) {
        candidate = payload.candidate;
      }
    } catch (_) {
      // The table row remains a safe fallback if the detail refresh fails.
    }
    Y(candidate);
    M(true);
  };
  const Oe = () => {
    M(false);
    Y(null);
  };
  async function Re(ge) {
    const Ge = C ? `${ve}/candidates/${C.id}` : `${ve}/candidates`;
    const Xe = await (
      await fetch(Ge, {
        method: C ? "PATCH" : "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(ge),
      })
    ).json();
    if (Xe.status !== "ok") {
      throw new Error(Xe.message || "Save failed");
    }
    Oe();
    fe();
  }
  async function Pe(ge) {
    if (
      !(await U({
        title: `Delete ${ge.name}?`,
        message:
          "This removes the candidate row permanently. Cannot be undone.",
        confirmLabel: "Delete",
        variant: "danger",
      }))
    ) {
      return;
    }
    const Be = await (
      await fetch(`${ve}/candidates/${ge.id}`, {
        method: "DELETE",
      })
    ).json();
    if (Be.status === "ok") {
      fe();
    } else {
      v(Be.message || "Delete failed");
    }
  }
  const De = i.length;
  const ye = w.useMemo(
    () =>
      c ? Object.values(c.by_stage || {}).reduce((ge, Ge) => ge + Ge, 0) : 0,
    [c],
  );
  const Le = w.useMemo(() => {
    var Ge;
    const ge = ((Ge = u || c) == null ? undefined : Ge.available_months) || [];
    return [
      {
        value: "all",
        label: "All time",
      },
      ...ge.map((Ze) => ({
        value: Ze.value,
        label: Ze.is_current ? `${Ze.label} · this month` : Ze.label,
      })),
    ];
  }, [u, c]);
  const $e = w.useMemo(() => {
    if (m === "all") {
      return null;
    }
    const ge = Le.find((Ge) => Ge.value === m);
    if (ge) {
      return ge.label.replace(" · this month", "");
    } else {
      return m;
    }
  }, [m, Le]);
  const st = w.useMemo(() => {
    const ge = ((c == null ? undefined : c.handler_references) || []).map(
      (Be) => ({
        name: Be.name,
        count: Be.total_count || 0,
      }),
    );
    ge.sort((Be, Xe) =>
      Xe.count !== Be.count
        ? Xe.count - Be.count
        : Be.name.localeCompare(Xe.name),
    );
    return [
      {
        value: "all",
        label: "All Referrers",
      },
      ...ge.map((Be) => ({
        value: Be.name,
        label: Be.name,
      })),
    ];
  }, [c, m]);
  return (
    <div className="cand-page">
      <header className="cand-header">
        <div className="cand-header-titles">
          <h2 className="cand-title">Candidates</h2>
        </div>
      </header>
      <nav
        className="cand-tabs"
        role="tablist"
        aria-label="Candidates sections"
      >
        <button
          type="button"
          role="tab"
          aria-selected={candTab === "overview"}
          className={`cand-tabs__btn${candTab === "overview" ? " cand-tabs__btn--active" : ""}`}
          onClick={() => setCandTab("overview")}
        >
          Overview
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={candTab === "candidates"}
          className={`cand-tabs__btn${candTab === "candidates" ? " cand-tabs__btn--active" : ""}`}
          onClick={() => setCandTab("candidates")}
        >
          Candidates
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={candTab === "performers"}
          className={`cand-tabs__btn${candTab === "performers" ? " cand-tabs__btn--active" : ""}`}
          onClick={() => setCandTab("performers")}
        >
          Earnings
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={candTab === "pending"}
          className={`cand-tabs__btn${candTab === "pending" ? " cand-tabs__btn--active" : ""}`}
          onClick={() => setCandTab("pending")}
        >
          Pending Works
        </button>
      </nav>
      <div
        className="cand-toolbar"
        role="region"
        aria-label="Candidate filters"
      >
        <input
          className="cand-input cand-input--search"
          placeholder="Search name, tech, reference, phone, notes…"
          value={E}
          onChange={(ge) => b(ge.target.value)}
        />
        <select
          className="cand-input"
          value={service}
          onChange={(ge) => setService(ge.target.value)}
          aria-label="Filter by service type"
        >
          <option value="all">All types</option>
          <option value="profile_service">Profile</option>
          <option value="round_wise">Round-wise</option>
        </select>
        <select
          className="cand-input"
          value={m}
          onChange={(ge) => _(ge.target.value)}
          aria-label="Filter by month"
        >
          {Le.map((ge) => (
            <option value={ge.value} key={ge.value}>
              {ge.label.replace(" · this month", "")}
            </option>
          ))}
        </select>
        <select
          className="cand-input"
          value={g}
          onChange={(ge) => p(ge.target.value)}
        >
          {dR.map((ge) => (
            <option value={ge.value} key={ge.value}>
              {ge.label}
            </option>
          ))}
        </select>
        {a && (
          <select
            className={`cand-input${T !== "all" ? " cand-input--active" : ""}`}
            value={T}
            onChange={(ge) => S(ge.target.value)}
            aria-label="Filter by handler / reference"
            title="Show only candidates referred by this handler"
          >
            {st.map((ge) => (
              <option value={ge.value} key={ge.value}>
                {ge.label}
              </option>
            ))}
          </select>
        )}
        <label
          className={`cand-toggle${y ? " cand-toggle--on" : ""}${(c == null ? undefined : c.pending_count) > 0 ? " cand-toggle--has-pending" : ""}`}
          title="Show only candidates with a pending balance"
        >
          <input
            type="checkbox"
            checked={y}
            onChange={(ge) => k(ge.target.checked)}
          />
          <span>Pending only</span>
          {(c == null ? undefined : c.pending_count) > 0 && (
            <span className="cand-toggle-badge">{c.pending_count}</span>
          )}
        </label>
        <button
          type="button"
          className="cand-btn cand-btn--ghost cand-btn--sm"
          onClick={() => setRo(true)}
          title="View all in-progress candidates grouped by technology"
        >
          ☷ Active list
        </button>
        <button
          type="button"
          className="cand-btn cand-btn--ghost cand-btn--sm"
          onClick={() => triggerRosterDownload({ month: "all", reference: T })}
          title="Download CSV of all active (in-progress) candidates"
        >
          ⇩ Download active CSV
        </button>
        {a && (
          <button
            type="button"
            className="cand-btn cand-btn--ghost cand-btn--sm"
            onClick={() => setShowExpenditure(true)}
            title="View total company expenditure — handler payouts + operational costs"
          >
            📊 Total expenditure
          </button>
        )}
        <button
          type="button"
          className="cand-btn cand-btn--primary cand-btn--sm"
          onClick={q}
        >
          + Add candidate
        </button>
      </div>
      {candTab === "overview" && c && (
        <>
          <J8
            stats={c}
            scopeLabel={$e}
            onPayoutsClick={pe}
            handlerView={n}
            handlerName={t}
            scopeReference={T !== "all" ? T : n ? t : null}
          />
        </>
      )}
      {candTab === "performers" && c && (
        <EarningsBreakdown
          stats={c}
          allStats={u}
          month={m}
          monthOptions={Le}
          onAddExpense={a ? ue : undefined}
          handlerView={n}
          handlerName={t}
          formatCurrency={Jc}
          apiBase={ve}
          onViewPaymentProofs={(candidate) => Z(candidate)}
        />
      )}
      {x && <div className="cand-error">{x}</div>}
      {candTab === "pending" && (
        <PendingWorksTab
          onOpenCandidate={() => {
            // The intent is already stashed; switching to the table lets the
            // existing effect find the row and open its editor.
            setCandTab("candidates");
          }}
        />
      )}
      {candTab === "candidates" && (
        <div className="cand-table-wrap">
          <table className="cand-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Service type</th>
                <th>Technology</th>
                <th>Stage</th>
                <th>Payment</th>
                <th>Date</th>
                <th>Phone</th>
                {a && <th>Reference</th>}
                <th>Resume</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {f && i.length === 0 ? (
                <tr>
                  <td colSpan={a ? 9 : 8} className="cand-table-empty">
                    Loading…
                  </td>
                </tr>
              ) : i.length === 0 ? (
                <tr>
                  <td colSpan={a ? 9 : 8} className="cand-table-empty">
                    No candidates match these filters.{" "}
                    <button type="button" className="cand-link" onClick={q}>
                      Add one
                    </button>
                    .
                  </td>
                </tr>
              ) : (
                i
                  .slice()
                  .sort((a2, b2) => {
                    const da = a2.logged_date || a2.date || "";
                    const db = b2.logged_date || b2.date || "";
                    return db.localeCompare(da);
                  })
                  .map((ge) => {
                    const Ge = fR(ge.stage);
                    return (
                      <tr
                        className={`cand-row${ge.needs_followup ? " cand-row--pending" : ""}`}
                        onClick={() => I(ge)}
                        key={ge.id}
                        data-cid={ge.id}
                      >
                        <td className="cand-cell-name">
                          <span className="cand-name">{ge.name}</span>
                          <span className="cand-cid" hidden>
                            {ge.id}
                          </span>
                          {ge.notes && (
                            <span className="cand-cell-note" title={ge.notes}>
                              · {ge.notes.slice(0, 30)}
                              {ge.notes.length > 30 ? "…" : ""}
                            </span>
                          )}
                          {ge.follow_up && (
                            <span
                              className="cand-cell-followup"
                              title={ge.follow_up}
                            >
                              <span aria-hidden={true}>⟳</span>{" "}
                              {ge.follow_up.slice(0, 60)}
                              {ge.follow_up.length > 60 ? "…" : ""}
                            </span>
                          )}
                        </td>
                        <td>
                          {ge.service_type === "round_wise" ? (
                            <span className="cand-channel-tag cand-channel-tag--roundwise">
                              Round-wise
                            </span>
                          ) : (
                            <span className="cand-channel-tag cand-channel-tag--profile">
                              Profile-wise
                            </span>
                          )}
                        </td>
                        <td>{ge.technology || "—"}</td>
                        <td>
                          <span className={`cand-badge ${Ge.cls}`}>
                            {Ge.label}
                          </span>
                        </td>
                        <td>
                          <_Component27 row={ge} onViewProofs={Z} />
                        </td>
                        <td className="cand-cell-mono">
                          {pR(ge.logged_date || ge.created_at || ge.date)}
                        </td>
                        <td
                          className="cand-cell-mono cand-cell-phone"
                          onClick={(Ze) => Ze.stopPropagation()}
                        >
                          <_Component23 phone={ge.phone} />
                        </td>
                        {a && (
                          <td className="cand-cell-ref">
                            {ge.reference || "—"}
                          </td>
                        )}
                        <td
                          className="cand-cell-resume"
                          onClick={(Ze) => {
                            Ze.stopPropagation();
                            Ze.nativeEvent.stopImmediatePropagation();
                          }}
                        >
                          <ResumeCell candidate={ge} onRefresh={fe} />
                        </td>
                        <td
                          className="cand-cell-actions"
                          onClick={(Ze) => Ze.stopPropagation()}
                        >
                          <button
                            type="button"
                            className="cand-btn cand-btn--ghost cand-btn--xs"
                            onClick={() => I(ge)}
                            title="Edit"
                          >
                            ✎
                          </button>
                          {a && (
                            <button
                              type="button"
                              className="cand-btn cand-btn--ghost cand-btn--xs cand-btn--danger-ghost"
                              onClick={() => Pe(ge)}
                              title="Delete"
                            >
                              🗑
                            </button>
                          )}
                        </td>
                      </tr>
                    );
                  })
              )}
            </tbody>
          </table>
        </div>
      )}
      {L && (
        <CandidateEditModal
          initial={C}
          handlerReference={n ? t : null}
          lockReference={n}
          isAdmin={a}
          referenceOptions={(
            (c == null ? undefined : c.handler_references) || []
          )
            .map((ge) => ge.name)
            .filter(Boolean)}
          onClose={Oe}
          onSave={Re}
        />
      )}
      {ce && (
        <_Component28
          stats={c}
          scopeLabel={$e}
          onClose={() => ee(false)}
          onManage={a ? ue : undefined}
        />
      )}
      {J && a && (
        <_Component29
          handlerNames={((c == null ? undefined : c.top_performers) || [])
            .map((ge) => ge.name)
            .filter(Boolean)}
          topPerformers={(c == null ? undefined : c.top_performers) || []}
          month={m}
          ownedSummary={{
            owed:
              (c == null ? undefined : c.handler_auto_earnings_total) ??
              (c == null ? undefined : c.handler_earnings_total) ??
              0,
            paid:
              (c == null ? undefined : c.handler_paid_out_total) ??
              (c == null ? undefined : c.handler_deductions_total) ??
              0,
            net: (c == null ? undefined : c.net_handler_payout) ?? 0,
          }}
          onClose={() => G(false)}
          onChanged={fe}
        />
      )}
      {P && a && (
        <_Component30 handler={P} onClose={() => j(null)} onChanged={fe} />
      )}
      {B && (
        <_Component31
          candidate={B}
          onClose={() => Z(null)}
          onEdit={(ge) => I(ge)}
        />
      )}
      <CandidatesActiveRoster
        open={ro}
        onClose={() => setRo(false)}
        reference={T}
      />
      {showExpenditure && (
        <CompanyExpenditure
          onClose={() => setShowExpenditure(false)}
          apiBase={ve}
        />
      )}
      <_Component32
        open={!!W}
        title={W == null ? undefined : W.title}
        message={W == null ? undefined : W.message}
        onVerified={W == null ? undefined : W.onVerified}
        onCancel={H}
      />
    </div>
  );
}

// Memoized: this panel takes no props, so memoizing prevents the parent's
// frequent WebSocket-driven re-renders from reconciling the table and tearing
// down the imperatively-injected DOM (Resume column, service filter, inline
// breakdown, complete badges) — which caused the table to flicker / "come and go".
export const CandidatesPanel = React.memo(CandidatesPanelImpl);
