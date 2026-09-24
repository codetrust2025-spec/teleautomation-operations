import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal, _Component27 as CandidatePaymentCell } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const BASE_CANDIDATE = {
  id: "e6971117e0",
  name: "CHINTHALA PAVAN",
  phone: "9381886003",
  technology: "ServiceNow",
  reference: "Pavan Kalyan",
  stage: "in_progress",
  service_type: "profile_service",
  expected_payment: 20000,
  payment: 0,
  payment_proofs: [],
  expected_minimum: 20000,
  verified_received: 0,
  verified_proof_count: 0,
  balance_due: 20000,
  payment_is_proof_derived: false,
  payment_unevidenced: false,
  referral_commission: 0,
  referral_percentage: 50,
};

function renderModal(candidateProps, onSave = vi.fn()) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={{ ...BASE_CANDIDATE, ...candidateProps }}
        onClose={vi.fn()}
        onSave={onSave}
        isAdmin={true}
      />
    </ConfirmProvider>,
  );
}

function proofRow(id, name = "receipt.png", amount = 20000) {
  return {
    id,
    attachment_type: "payment_proof",
    original_name: name,
    size: 102400,
    url: `/candidates/e6971117e0/proofs/${id}`,
    verification_state: "VERIFIED_COMPANY_PAYMENT",
    verified_amount: amount,
  };
}

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

beforeEach(() => {
  FakeXMLHttpRequest.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXMLHttpRequest);
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: vi.fn(() => "blob:payment-proof"),
    revokeObjectURL: vi.fn(),
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CandidateEditModal payment status presentation", () => {
  it("State 1: Verified proof + full amount renders green ✓ Paid (₹20,000)", () => {
    const { container } = renderModal({
      payment: 20000,
      payment_proofs: [proofRow("p1", "proof.png", 20000)],
      verified_proof_count: 1,
      verified_proof_total: 20000,
      verified_received: 20000,
      payment_is_proof_derived: true,
      payment_unevidenced: false,
    });

    const statusEl = container.querySelector(".cand-pay-status");
    expect(statusEl).toBeTruthy();
    expect(statusEl.className).toContain("cand-pay-status--paid");
    expect(statusEl.textContent).toContain("✓ Paid (₹20,000)");
    expect(container.querySelector(".cand-pay-status--unevidenced")).toBeNull();
  });

  it("State 2: Full amount recorded but no proof renders amber ⚠ Recorded — proof missing (₹20,000)", () => {
    const { container } = renderModal({
      payment: 20000,
      payment_proofs: [],
      proof_count: 0,
      verified_proof_count: 0,
      payment_unevidenced: true,
      payment_is_proof_derived: false,
    });

    const statusEl = container.querySelector(".cand-pay-status");
    expect(statusEl).toBeTruthy();
    expect(statusEl.className).toContain("cand-pay-status--unevidenced");
    expect(statusEl.textContent).toContain("⚠ Recorded — proof missing (₹20,000)");
    // Must NEVER show green Paid
    expect(container.querySelector(".cand-pay-status--paid")).toBeNull();
    expect(statusEl.textContent).not.toContain("✓ Paid");
  });

  it("State 3: Partial amount recorded but no proof renders amber ⚠ Recorded — proof missing (₹10,000 / ₹20,000)", () => {
    const { container } = renderModal({
      payment: 10000,
      payment_proofs: [],
      proof_count: 0,
      verified_proof_count: 0,
      payment_unevidenced: true,
      payment_is_proof_derived: false,
    });

    const statusEl = container.querySelector(".cand-pay-status");
    expect(statusEl).toBeTruthy();
    expect(statusEl.className).toContain("cand-pay-status--unevidenced");
    expect(statusEl.textContent).toContain("⚠ Recorded — proof missing (₹10,000 / ₹20,000)");
    expect(container.querySelector(".cand-pay-status--paid")).toBeNull();
  });

  it("State 4: No payment received renders ○ ₹20,000 pending", () => {
    const { container } = renderModal({
      payment: 0,
      payment_proofs: [],
      proof_count: 0,
      verified_proof_count: 0,
      payment_unevidenced: false,
      payment_is_proof_derived: false,
    });

    const statusEl = container.querySelector(".cand-pay-status");
    expect(statusEl).toBeTruthy();
    expect(statusEl.className).toContain("cand-pay-status--unpaid");
    expect(statusEl.textContent).toContain("○ ₹20,000 pending");
    expect(container.querySelector(".cand-pay-status--paid")).toBeNull();
    expect(container.querySelector(".cand-pay-status--unevidenced")).toBeNull();
  });

  it("State 5: Never shows ✓ Paid when payment_unevidenced is true", () => {
    const { container } = renderModal({
      payment: 25000,
      expected_payment: 20000,
      payment_proofs: [],
      proof_count: 0,
      payment_unevidenced: true,
      payment_is_proof_derived: false,
    });

    expect(container.querySelector(".cand-pay-status--paid")).toBeNull();
    expect(container.querySelector(".cand-pay-status--unevidenced")).toBeTruthy();
    expect(container.querySelector(".cand-pay-status").textContent).not.toContain("✓ Paid");
  });

  it("State 6: Live proof upload flips unevidenced manual entry to verified Paid", async () => {
    // Start with manually recorded ₹20,000 without proof (CHINTHALA PAVAN's exact initial state)
    const { container } = renderModal({
      payment: 20000,
      payment_proofs: [],
      proof_count: 0,
      payment_unevidenced: true,
      payment_is_proof_derived: false,
    });

    expect(container.querySelector(".cand-pay-status--unevidenced")).toBeTruthy();
    expect(container.querySelector(".cand-pay-status--paid")).toBeNull();

    // Now upload a verified proof of ₹20,000
    const file = new File(["receipt"], "receipt.png", { type: "image/png" });
    const input = Array.from(
      container.querySelectorAll('input[type="file"]'),
    ).find((node) => node.getAttribute("accept") === "image/*");
    expect(input).toBeTruthy();

    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(FakeXMLHttpRequest.instances.length).toBeGreaterThan(0));
    const xhr = FakeXMLHttpRequest.instances[FakeXMLHttpRequest.instances.length - 1];
    act(() => xhr.upload.onload());
    xhr.status = 200;
    xhr.responseText = JSON.stringify({
      status: "ok",
      candidate: {
        ...BASE_CANDIDATE,
        payment: 20000,
        payment_proofs: [proofRow("p1", file.name, 20000)],
        payment_is_proof_derived: true,
        payment_unevidenced: false,
      },
      payment_summary: {
        verified_proof_total: 20000,
        received_total: 20000,
        expected_amount: 20000,
        outstanding_amount: 0,
        above_minimum_amount: 0,
        verified_proof_count: 1,
        payment_status: "PAID",
        proof_derived: true,
        unevidenced: false,
        proof_count: 1,
        needs_reconciliation: false,
        reconciliation_gap: 0,
        referrer: "Pavan Kalyan",
        referral_percentage: 50,
        referral_commission: 10000,
        referral_basis: 20000,
        referrer_complimentary_amount: 0,
      },
    });
    act(() => xhr.onload());

    // Once proof verifies, badge must flip to ✓ Paid (green)
    await waitFor(() =>
      expect(container.querySelector(".cand-pay-status--paid")).toBeTruthy(),
    );
    expect(container.querySelector(".cand-pay-status--unevidenced")).toBeNull();
    expect(container.querySelector(".cand-pay-status").textContent).toContain("✓ Paid (₹20,000)");
  });
});

describe("CandidatePaymentCell (Candidates list/card table cell)", () => {
  it("renders amber ⚠ Recorded — proof missing for full amount with no proof", () => {
    const row = {
      payment: 20000,
      expected_payment: 20000,
      payment_proofs: [],
      payment_unevidenced: true,
      payment_status: "paid",
    };
    const { container } = render(<CandidatePaymentCell row={row} />);
    const pill = container.querySelector(".cand-pay-pill");
    expect(pill).toBeTruthy();
    expect(pill.className).toContain("cand-pay-pill--unevidenced");
    expect(pill.textContent).toContain("⚠ Recorded — proof missing");
    // Must NOT have cand-pay-pill--paid
    expect(container.querySelector(".cand-pay-pill--paid")).toBeNull();
    expect(container.querySelector(".cand-pay-amount").textContent).toBe("₹20,000");
  });

  it("renders amber ⚠ Recorded — proof missing for partial amount with no proof", () => {
    const row = {
      payment: 15000,
      expected_payment: 20000,
      payment_proofs: [],
      payment_unevidenced: true,
      payment_status: "partial",
    };
    const { container } = render(<CandidatePaymentCell row={row} />);
    const pill = container.querySelector(".cand-pay-pill");
    expect(pill).toBeTruthy();
    expect(pill.className).toContain("cand-pay-pill--unevidenced");
    expect(pill.textContent).toContain("⚠ Recorded — proof missing");
    expect(container.querySelector(".cand-pay-amount").textContent).toContain("₹15,000");
    expect(container.querySelector(".cand-pay-amount").textContent).toContain("/ ₹20k");
  });

  it("renders green Paid pill when verified proof exists for full amount", () => {
    const row = {
      payment: 20000,
      expected_payment: 20000,
      payment_proofs: [proofRow("p1", "proof.png", 20000)],
      payment_unevidenced: false,
      payment_status: "paid",
    };
    const { container } = render(<CandidatePaymentCell row={row} />);
    const pill = container.querySelector(".cand-pay-pill");
    expect(pill).toBeTruthy();
    expect(pill.className).toContain("cand-pay-pill--paid");
    expect(pill.textContent).toBe("Paid");
    expect(container.querySelector(".cand-pay-proofs")).toBeTruthy();
    expect(container.querySelector(".cand-pay-proofs").textContent).toContain("1");
  });

  it("renders due pill when zero payment received", () => {
    const row = {
      payment: 0,
      expected_payment: 20000,
      payment_proofs: [],
      payment_unevidenced: false,
      payment_status: "unpaid",
    };
    const { container } = render(<CandidatePaymentCell row={row} />);
    const pill = container.querySelector(".cand-pay-pill");
    expect(pill).toBeTruthy();
    expect(pill.className).toContain("cand-pay-pill--unpaid");
    expect(pill.textContent).toContain("₹20k due");
    expect(container.querySelector(".cand-pay-zero").textContent).toBe("₹0");
  });
});
