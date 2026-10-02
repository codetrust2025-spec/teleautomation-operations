const PROOF_COLLECTION_KEYS = [
  "payment_proofs",
  "paymentProofs",
  "paymentScreenshots",
  "payment_screenshots",
  "proofImages",
  "proof_images",
  "paymentAttachments",
  "payment_attachments",
  "screenshot_urls",
  "paymentEvidence",
  "payment_evidence",
  "transactionProofs",
  "transaction_proofs",
  "proofs",
  "screenshots",
];

const CONTAINER_KEYS = [
  "candidate",
  "data",
  "result",
  "response",
  "payment",
  "payments",
  "transaction",
  "transactions",
];

const URL_KEYS = [
  "url",
  "fileUrl",
  "file_url",
  "imageUrl",
  "image_url",
  "screenshotUrl",
  "screenshot_url",
  "path",
  "file_path",
  "storagePath",
  "storage_path",
  "attachmentUrl",
  "attachment_url",
  "signedUrl",
  "signed_url",
  "downloadUrl",
  "download_url",
];

function firstValue(source, keys) {
  for (const key of keys) {
    const value = source?.[key];
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return "";
}

function isDeleted(proof) {
  const status = String(proof?.status || proof?.state || "").trim().toLowerCase();
  return Boolean(
    proof?.deleted ||
      proof?.is_deleted ||
      proof?.isDeleted ||
      proof?.deleted_at ||
      proof?.deletedAt ||
      ["deleted", "removed"].includes(status),
  );
}

function looksLikePaymentAttachment(proof) {
  const type = String(
    proof?.attachment_type || proof?.attachmentType || proof?.type || proof?.kind || "",
  )
    .trim()
    .toLowerCase();
  if (!type) return true;
  return [
    "payment_proof",
    "payment-proof",
    "payment proof",
    "payment_receipt",
    "payment-receipt",
    "receipt",
  ].includes(type);
}

function normalizeUrl(value) {
  const url = String(value || "").trim().replaceAll("\\", "/");
  if (!url) return "";
  return url;
}

function normalizeOne(raw, inherited = {}) {
  if (typeof raw === "string") raw = { url: raw };
  if (!raw || typeof raw !== "object" || Array.isArray(raw) || isDeleted(raw)) {
    return null;
  }
  if (!looksLikePaymentAttachment(raw)) return null;

  const url = normalizeUrl(firstValue(raw, URL_KEYS));
  const id = String(
    firstValue(raw, ["id", "proof_id", "proofId", "attachment_id", "attachmentId"]) ||
      url,
  ).trim();
  if (!id && !url) return null;

  return {
    ...raw,
    id,
    candidateId: String(
      firstValue(raw, ["candidateId", "candidate_id"]) ||
        inherited.candidateId ||
        "",
    ),
    candidate_id: String(
      firstValue(raw, ["candidate_id", "candidateId"]) ||
        inherited.candidateId ||
        "",
    ),
    paymentId: String(
      firstValue(raw, ["paymentId", "payment_id", "payment_record_id"]) ||
        inherited.paymentId ||
        "",
    ),
    payment_id: String(
      firstValue(raw, ["payment_id", "paymentId", "payment_record_id"]) ||
        inherited.paymentId ||
        "",
    ),
    url,
    uploadedAt: firstValue(raw, [
      "uploadedAt",
      "uploaded_at",
      "createdAt",
      "created_at",
      "date",
    ]),
    uploaded_at: firstValue(raw, [
      "uploaded_at",
      "uploadedAt",
      "created_at",
      "createdAt",
      "date",
    ]),
    amount: Number(firstValue(raw, ["amount", "verified_amount", "payment_amount"])) || 0,
    utr: String(firstValue(raw, ["utr", "utr_number", "utrNumber"]) || ""),
    status: String(firstValue(raw, ["status", "payment_status"]) || ""),
  };
}

/**
 * Convert current and legacy candidate/payment response shapes into one
 * canonical payment-proof list. This is the only source for table counts and
 * modal previews.
 */
export function normalizePaymentProofs(source) {
  const candidates = [];
  const visited = new Set();

  function collect(value, inherited = {}, directArray = false) {
    if (value == null) return;
    if (Array.isArray(value)) {
      if (directArray) {
        for (const item of value) candidates.push([item, inherited]);
      } else {
        for (const item of value) collect(item, inherited, false);
      }
      return;
    }
    if (typeof value !== "object" || visited.has(value)) return;
    visited.add(value);

    const nextInherited = {
      candidateId:
        firstValue(value, ["candidateId", "candidate_id"]) ||
        inherited.candidateId ||
        "",
      paymentId:
        firstValue(value, ["paymentId", "payment_id", "payment_record_id"]) ||
        inherited.paymentId ||
        "",
    };

    if (
      !directArray &&
      URL_KEYS.some((key) => value[key]) &&
      looksLikePaymentAttachment(value)
    ) {
      candidates.push([value, nextInherited]);
    }
    for (const key of PROOF_COLLECTION_KEYS) {
      if (Array.isArray(value[key])) collect(value[key], nextInherited, true);
    }
    if (Array.isArray(value.attachments)) {
      for (const attachment of value.attachments) {
        if (looksLikePaymentAttachment(attachment)) {
          candidates.push([attachment, nextInherited]);
        }
      }
    }
    for (const key of CONTAINER_KEYS) {
      if (value[key] !== undefined) collect(value[key], nextInherited, false);
    }
  }

  if (Array.isArray(source)) collect(source, {}, true);
  else collect(source);

  const result = [];
  const seen = new Set();
  for (const [raw, inherited] of candidates) {
    const proof = normalizeOne(raw, inherited);
    if (!proof) continue;
    const key = proof.id ? `id:${proof.id}` : `url:${proof.url.toLowerCase()}`;
    const urlKey = proof.url ? `url:${proof.url.toLowerCase()}` : "";
    if (seen.has(key) || (urlKey && seen.has(urlKey))) continue;
    seen.add(key);
    if (urlKey) seen.add(urlKey);
    result.push(proof);
  }
  return result;
}

// --- presentation of one proof (the Candidate Edit proof cards) ------------------

const STATUS_BY_STATE = {
  VERIFIED_COMPANY_PAYMENT: { key: "verified", label: "Verified", tone: "ok" },
  VERIFIED_REFERRER_PAYMENT: { key: "verified", label: "Verified", tone: "ok" },
  UNKNOWN_RECEIVER: { key: "review", label: "Receiver not recognised", tone: "warn" },
  PENDING_MANUAL_REVIEW: { key: "review", label: "Needs review", tone: "warn" },
  INCOMPLETE_PAYMENT_EVIDENCE: { key: "review", label: "Needs review", tone: "warn" },
  AMOUNT_EXTRACTION_REVIEW_REQUIRED: { key: "review", label: "Needs review", tone: "warn" },
  EXTRACTED: { key: "review", label: "Needs review", tone: "warn" },
  UPLOADED: { key: "pending", label: "Reading screenshot", tone: "muted" },
  EXTRACTION_IN_PROGRESS: { key: "pending", label: "Reading screenshot", tone: "muted" },
  EXTRACTION_FAILED: { key: "failed", label: "Could not read", tone: "bad" },
  DUPLICATE_PAYMENT: { key: "duplicate", label: "Duplicate payment", tone: "bad" },
  FAILED_PAYMENT: { key: "rejected", label: "Payment failed", tone: "bad" },
  REJECTED: { key: "rejected", label: "Rejected", tone: "bad" },
  REVERSED: { key: "rejected", label: "Reversed", tone: "bad" },
};

const NOT_REVIEWED = { key: "legacy", label: "Not reviewed", tone: "muted" };

/** What a reviewer should read off a proof card: status, amount, reference. */
export function proofPresentation(proof) {
  const state = String(proof?.verification_state || "").trim().toUpperCase();
  const status =
    STATUS_BY_STATE[state] ||
    (state ? { key: "review", label: "Needs review", tone: "warn" } : NOT_REVIEWED);
  const amount = Number(proof?.verified_amount ?? proof?.amount) || 0;

  const utr = String(proof?.utr_number || proof?.utr || "").trim();
  const txn = String(proof?.transaction_id || "").trim();
  const ref = String(proof?.reference_number || "").trim();
  // Some stored records hold the provider's transaction id in the UTR field.
  const looksLikeTxnId = (value) => /^T\d{12,}$/i.test(value);
  let reference = { label: "UTR", value: "" };
  if (utr && !looksLikeTxnId(utr)) reference = { label: "UTR", value: utr };
  else if (txn) reference = { label: "Transaction ID", value: txn };
  else if (utr) reference = { label: "Transaction ID", value: utr };
  else if (ref) reference = { label: "Reference", value: ref };

  return {
    status,
    amount,
    amountText: amount > 0 ? `₹${amount.toLocaleString("en-IN")}` : "Amount not read",
    reference,
  };
}

function identityKeys(proof) {
  const keys = [];
  for (const [kind, value] of [
    ["utr", proof?.utr_number || proof?.utr],
    ["txn", proof?.transaction_id],
    ["ref", proof?.reference_number],
    ["sha", proof?.sha256],
  ]) {
    const v = String(value || "").trim().toLowerCase().replace(/\s+/g, "");
    if (v) keys.push(`${kind}:${v}`);
  }
  // The same transaction id is sometimes stored in the UTR field of one copy.
  const utr = String(proof?.utr_number || "").trim().toLowerCase();
  const txn = String(proof?.transaction_id || "").trim().toLowerCase();
  if (utr) keys.push(`txn:${utr}`);
  if (txn) keys.push(`utr:${txn}`);
  return keys;
}

function preferred(a, b) {
  const rank = (p) =>
    (proofPresentation(p).status.key === "verified" ? 2 : 0) + (p?.file_availability ? 0 : 1);
  const diff = rank(b) - rank(a);
  if (diff) return diff > 0 ? b : a;
  return String(a?.uploaded_at || "") <= String(b?.uploaded_at || "") ? a : b;
}

/**
 * One card per underlying payment.
 *
 * A candidate's slot rows each carry copies of a proof, and the same
 * screenshot can be uploaded twice; two cards for one transaction read as two
 * payments. Copies are grouped by transaction identity (UTR, transaction id,
 * reference or screenshot checksum), never by amount or date, and the best
 * representative is shown: verified over unreviewed, a stored file over a lost
 * one, then the earliest. Nothing is deleted or altered; the rest are returned
 * as `hidden` so they remain reachable.
 */
export function dedupeProofsForDisplay(proofs) {
  const list = Array.isArray(proofs) ? proofs : [];
  const groups = [];
  const owner = new Map();
  for (const proof of list) {
    const keys = identityKeys(proof);
    const index = keys.map((k) => owner.get(k)).find((i) => i !== undefined);
    if (index === undefined) {
      groups.push([proof]);
      keys.forEach((k) => owner.set(k, groups.length - 1));
    } else {
      groups[index].push(proof);
      keys.forEach((k) => owner.set(k, index));
    }
  }
  const shown = [];
  const hidden = [];
  for (const group of groups) {
    const best = group.reduce(preferred);
    shown.push(best);
    for (const proof of group) if (proof !== best) hidden.push(proof);
  }
  const order = new Map(list.map((p, i) => [p, i]));
  shown.sort((a, b) => order.get(a) - order.get(b));
  return { shown, hidden };
}
