/**
 * Candidate Edit: the payment section, across its variants.
 *
 *  1. Received ₹ is plainly read-only whenever the system owns the figure.
 *  2. % on CTC stays on Profile service, and only there.
 *  3. A proof card shows its amount, verification status, date and UTR.
 *  4. A lost screenshot says so instead of a broken image; one payment is one card.
 *  5. While a screenshot is being read, no premature pending/outstanding warning.
 *  6. Deleting a proof asks first, and says what is being deleted.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

class FakeXMLHttpRequest {
  static instances = [];
  constructor() {
    this.upload = {};
    this.status = 0;
    this.responseText = "";
    FakeXMLHttpRequest.instances.push(this);
  }
  open(method, url) {
    this.method = method;
    this.url = url;
  }
  send(body) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
}

const PROFILE = {
  id: "cand-ui-1",
  name: "Test Edit Person",
  phone: "9000000701",
  technology: "Java",
  reference: "Pavan Kalyan",
  stage: "in_progress",
  service_type: "profile_service",
  ctc_percentage: 12,
  expected_payment: 20000,
  payment: 0,
  payment_proofs: [],
  expected_minimum: 20000,
  verified_received: 0,
  verified_proof_count: 0,
  balance_due: 20000,
  payment_is_proof_derived: false,
  referral_commission: 0,
  referral_percentage: 50,
};

const ROUND = {
  ...PROFILE,
  id: "cand-ui-2",
  service_type: "round_wise",
  interview_scope: "external",
  ctc_percentage: "",
  expected_payment: 5000,
  expected_minimum: 5000,
  balance_due: 5000,
};

const verifiedProof = (id = "p1", extra = {}) => ({
  id,
  attachment_type: "payment_proof",
  url: `/candidates/cand-ui-1/attachments/payment_proof/${id}`,
  original_name: `${id}.jpg`,
  size: 61112,
  uploaded_at: "2026-09-28T08:22:46+00:00",
  verification_state: "VERIFIED_COMPANY_PAYMENT",
  verified_amount: 20000,
  utr_number: "935144418237",
  transaction_id: "T2609021122389997329605",
  ...extra,
});

const DERIVED = {
  payment: 20000,
  verified_received: 20000,
  verified_proof_total: 20000,
  verified_proof_count: 1,
  proof_count: 1,
  balance_due: 0,
  payment_is_proof_derived: true,
  payment_proofs: [verifiedProof()],
};

function renderModal(candidate, extra = {}) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={{ ...PROFILE, ...candidate }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        isAdmin={true}
        {...extra}
      />
    </ConfirmProvider>,
  );
}

const receivedInput = () => screen.getByText(/^Received ₹/).closest("label").querySelector("input");

beforeEach(() => {
  FakeXMLHttpRequest.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:p"), revokeObjectURL: vi.fn() });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("1. Received ₹ is clearly read-only when the system owns it", () => {
  const VARIANTS = [
    ["profile service, proof-derived", PROFILE, DERIVED, true],
    ["round-wise, proof-derived", ROUND, { ...DERIVED, expected_minimum: 5000 }, true],
    ["profile service, files lost (not derived)", PROFILE, { payment: 20000, payment_unevidenced: true, payment_proof_files_lost: true, proof_count: 1, payment_proofs: [{ id: "old", attachment_type: "payment_proof", url: "/x/old", original_name: "old.jpg" }] }, false],
    ["profile service, nothing recorded", PROFILE, {}, false],
  ];

  for (const [name, base, patch, locked] of VARIANTS) {
    it(`${name}: ${locked ? "locked" : "editable, and says it is manual"}`, () => {
      renderModal({ ...base, ...patch });
      const input = receivedInput();
      if (locked) {
        expect(input).toBeDisabled();
        expect(input).toHaveAttribute("readonly");
        expect(input).toHaveAttribute("aria-readonly", "true");
        expect(input.className).toContain("cand-input--readonly");
        expect(screen.getByText("from verified proofs")).toBeInTheDocument();
        expect(screen.getByText(/Calculated from verified payment proofs/)).toBeInTheDocument();
      } else {
        expect(input).not.toBeDisabled();
        expect(input.className).not.toContain("cand-input--readonly");
        expect(screen.getByText(/Entered manually/)).toBeInTheDocument();
      }
    });
  }

  it("typing into a locked Received changes nothing", () => {
    renderModal({ ...PROFILE, ...DERIVED });
    fireEvent.change(receivedInput(), { target: { value: "99999" } });
    expect(receivedInput()).toHaveValue(20000);
  });
});

describe("2. % on CTC stays on Profile service", () => {
  const ctc = () => screen.queryByText(/% on CTC/i);

  it("is shown, required, for a Profile candidate", () => {
    renderModal({ ...PROFILE });
    expect(ctc()).toBeInTheDocument();
    expect(ctc().textContent).toContain("*");
    expect(ctc().closest("label").querySelector("input")).toHaveValue(12);
  });

  it("is still shown beside a locked, proof-derived Received", () => {
    renderModal({ ...PROFILE, ...DERIVED });
    expect(ctc()).toBeInTheDocument();
  });

  it("is not shown for a round-wise candidate", () => {
    renderModal({ ...ROUND });
    expect(ctc()).toBeNull();
  });

  it("is optional for a dropped Profile candidate", () => {
    renderModal({ ...PROFILE, stage: "dropped" });
    expect(ctc()).toBeInTheDocument();
    expect(ctc().textContent).not.toContain("*");
  });

  it("is available when adding a new Profile candidate", () => {
    render(
      <ConfirmProvider>
        <CandidateEditModal initial={null} onClose={vi.fn()} onSave={vi.fn()} isAdmin={true} />
      </ConfirmProvider>,
    );
    expect(ctc()).toBeInTheDocument();
  });
});

describe("3. A proof card shows amount, status, date and UTR", () => {
  it("for a verified proof", () => {
    renderModal({ ...PROFILE, ...DERIVED });
    const card = document.querySelector('[data-proof-id="p1"]');
    expect(within(card).getByText("₹20,000")).toBeInTheDocument();
    expect(within(card).getByText("Verified")).toBeInTheDocument();
    expect(within(card).getByText("UTR")).toBeInTheDocument();
    expect(within(card).getByText("935144418237")).toBeInTheDocument();
    expect(within(card).getByText("Uploaded")).toBeInTheDocument();
    expect(card.textContent).toMatch(/2026|Sep/);
  });

  it("for a proof the engine could not place: no invented amount, and the reason shown", () => {
    renderModal({
      ...PROFILE,
      payment_proofs: [verifiedProof("p2", { verification_state: "UNKNOWN_RECEIVER", verified_amount: 0 })],
      proof_count: 1,
    });
    const card = document.querySelector('[data-proof-id="p2"]');
    expect(within(card).getByText("Amount not read")).toBeInTheDocument();
    expect(within(card).getByText("Receiver not recognised")).toBeInTheDocument();
  });

  it("for a legacy proof nobody reviewed", () => {
    renderModal({
      ...PROFILE,
      payment_proofs: [{ id: "old", attachment_type: "payment_proof", url: "/x/old", uploaded_at: "2026-06-01T00:00:00+00:00" }],
      proof_count: 1,
    });
    const card = document.querySelector('[data-proof-id="old"]');
    expect(within(card).getByText("Not reviewed")).toBeInTheDocument();
    expect(within(card).getByText("Amount not read")).toBeInTheDocument();
  });

  it("labels a transaction id stored in the UTR field as one", () => {
    renderModal({ ...PROFILE, payment_proofs: [verifiedProof("p3", { utr_number: "T2609131812234060873256", transaction_id: "" })], proof_count: 1 });
    const card = document.querySelector('[data-proof-id="p3"]');
    expect(within(card).getByText("Transaction ID")).toBeInTheDocument();
  });
});

describe("4. Lost screenshots and duplicate displays", () => {
  it("a screenshot that fails to load says so instead of a broken image", () => {
    renderModal({ ...PROFILE, ...DERIVED });
    const card = document.querySelector('[data-proof-id="p1"]');
    fireEvent.error(card.querySelector("img"));
    expect(within(card).getByText("Screenshot unavailable")).toBeInTheDocument();
    expect(card.querySelector("img")).toBeNull();
    expect(within(card).getByText("₹20,000")).toBeInTheDocument();
  });

  it("a proof flagged as having a missing file never tries to load it", () => {
    renderModal({
      ...PROFILE,
      payment_proofs: [verifiedProof("p4", { file_availability: "MISSING_FILE" })],
      proof_count: 1,
    });
    const card = document.querySelector('[data-proof-id="p4"]');
    expect(card.querySelector("img")).toBeNull();
    expect(within(card).getByText("Screenshot unavailable")).toBeInTheDocument();
  });

  it("one payment uploaded twice is one card, with the copy reachable", () => {
    const twin = verifiedProof("p5", { uploaded_at: "2026-09-29T09:00:00+00:00" });
    renderModal({ ...PROFILE, ...DERIVED, payment_proofs: [verifiedProof("p1"), twin], proof_count: 2 });
    expect(document.querySelectorAll(".cand-proof-card")).toHaveLength(1);
    expect(document.querySelector(".cand-proofs-count").textContent).toBe("1");
    expect(screen.getByText(/1 duplicate upload of a payment shown above is hidden/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show duplicates" }));
    expect(document.querySelectorAll(".cand-proof-card")).toHaveLength(2);
    expect(screen.getByText("Duplicate upload")).toBeInTheDocument();
  });

  it("two different payments of the same amount stay two cards", () => {
    const other = verifiedProof("p6", { utr_number: "111122223333", transaction_id: "T9" });
    renderModal({ ...PROFILE, ...DERIVED, payment_proofs: [verifiedProof("p1"), other], proof_count: 2 });
    expect(document.querySelectorAll(".cand-proof-card")).toHaveLength(2);
    expect(screen.queryByText(/duplicate upload/)).toBeNull();
  });
});

describe("5. Verification in progress", () => {
  function startUpload(container) {
    const file = new File(["payment"], "receipt.png", { type: "image/png" });
    const input = Array.from(container.querySelectorAll('input[type="file"]')).find(
      (node) => node.getAttribute("accept") === "image/*",
    );
    fireEvent.change(input, { target: { files: [file] } });
    return file;
  }

  it("replaces the pending warnings while a screenshot is read, then restores the truth", async () => {
    const { container } = renderModal({ ...PROFILE, payment: 0, balance_due: 20000 });
    expect(screen.getByText(/20,000 pending/)).toBeInTheDocument();

    const file = startUpload(container);
    await waitFor(() => expect(FakeXMLHttpRequest.instances.length).toBeGreaterThan(0));

    expect(screen.getAllByText(/Verification in progress/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/20,000 pending/)).toBeNull();
    expect(screen.queryByText(/proof missing/i)).toBeNull();
    expect(screen.queryByText(/Outstanding/)).toBeNull();
    expect(receivedInput()).toBeDisabled();
    expect(screen.getByText(/Locked while the screenshot is being verified/)).toBeInTheDocument();

    const xhr = FakeXMLHttpRequest.instances[FakeXMLHttpRequest.instances.length - 1];
    act(() => xhr.upload.onload());
    xhr.status = 200;
    xhr.responseText = JSON.stringify({
      status: "ok",
      candidate: { ...PROFILE, ...DERIVED, payment_proofs: [verifiedProof("p1", { original_name: file.name, size: file.size })] },
      payment_summary: { received_total: 20000, expected_amount: 20000, outstanding_amount: 0, above_minimum_amount: 0, verified_proof_count: 1, proof_count: 1, proof_derived: true, verified_proof_total: 20000, payment_status: "PAID" },
    });
    act(() => xhr.onload());

    await waitFor(() => expect(screen.queryByText(/Verification in progress/)).toBeNull());
    expect(receivedInput()).toHaveValue(20000);
    expect(document.querySelector(".cand-pay-status--paid")).toBeTruthy();
  });

  it("does not claim verification when nothing is being uploaded", () => {
    renderModal({ ...PROFILE, ...DERIVED });
    expect(screen.queryByText(/Verification in progress/)).toBeNull();
  });
});

describe("6. Deleting a proof asks first", () => {
  const withProof = () => renderModal({ ...PROFILE, ...DERIVED });
  const deleteResponse = { status: "ok", candidate: { ...PROFILE }, payment_summary: { received_total: 0, expected_amount: 20000, outstanding_amount: 20000, above_minimum_amount: 0, verified_proof_count: 0, proof_count: 0, proof_derived: false, payment_status: "UNPAID" } };
  // The confirm dialog's own buttons (the edit form has a Cancel of its own).
  const dialogButton = (label) =>
    Array.from(document.querySelectorAll(".cm-btn")).find((b) => b.textContent.trim() === label);

  it("shows what is being deleted and deletes nothing until confirmed", async () => {
    withProof();
    fireEvent.click(screen.getByRole("button", { name: /Delete proof of ₹20,000/ }));
    expect(await screen.findByText("Delete this payment proof?")).toBeInTheDocument();
    expect(document.body.textContent).toContain("₹20,000 · UTR 935144418237");
    expect(document.body.textContent).toContain("recalculates the received total");
    expect(document.body.textContent).toContain("cannot be undone");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("cancelling leaves the proof and sends nothing", async () => {
    withProof();
    fireEvent.click(screen.getByRole("button", { name: /Delete proof of/ }));
    await screen.findByText("Delete this payment proof?");
    fireEvent.click(dialogButton("Cancel"));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(fetch).not.toHaveBeenCalled();
    expect(document.querySelector('[data-proof-id="p1"]')).toBeTruthy();
  });

  it("confirming sends the delete for that proof", async () => {
    fetch.mockResolvedValue({ json: async () => deleteResponse });
    withProof();
    fireEvent.click(screen.getByRole("button", { name: /Delete proof of/ }));
    await screen.findByText("Delete this payment proof?");
    fireEvent.click(dialogButton("Delete proof"));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    const [url, options] = fetch.mock.calls[0];
    expect(url).toContain("/candidates/cand-ui-1/proofs/p1");
    expect(options.method).toBe("DELETE");
  });

  it("asks with the browser's own confirm if the app dialog is not mounted, and respects a no", async () => {
    withProof();
    window.__TA_CONFIRM_VALUE__ = undefined; // the app dialog is unavailable
    const ask = vi.fn(() => false);
    vi.stubGlobal("confirm", ask);
    fireEvent.click(screen.getByRole("button", { name: /Delete proof of/ }));
    await waitFor(() => expect(ask).toHaveBeenCalledTimes(1));
    expect(ask.mock.calls[0][0]).toContain("₹20,000 · UTR 935144418237");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("deletes through the browser's confirm when that is what the person accepts", async () => {
    fetch.mockResolvedValue({ json: async () => deleteResponse });
    withProof();
    window.__TA_CONFIRM_VALUE__ = undefined;
    vi.stubGlobal("confirm", vi.fn(() => true));
    fireEvent.click(screen.getByRole("button", { name: /Delete proof of/ }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
    expect(fetch.mock.calls[0][1].method).toBe("DELETE");
  });

  it("the delete control is always visible, not hover-only", () => {
    withProof();
    const control = document.querySelector('[data-proof-id="p1"] .cand-proof-delete');
    expect(control).toBeTruthy();
    expect(control).toHaveAttribute("aria-label");
  });
});
