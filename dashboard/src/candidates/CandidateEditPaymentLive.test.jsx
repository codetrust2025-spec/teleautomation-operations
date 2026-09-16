/**
 * A verified payment proof must change the Received field immediately —
 * no Save, no reopen.
 */
import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

const CANDIDATE = {
  id: "cand-alluraiah",
  name: "alluraiah",
  phone: "9000000106",
  technology: ".NET",
  reference: "Pavan Kalyan",
  stage: "in_progress",
  service_type: "round_wise",
  interview_scope: "external",
  expected_payment: 5000,
  payment: 0,
  payment_proofs: [],
  expected_minimum: 5000,
  verified_received: 0,
  verified_proof_count: 0,
  balance_due: 5000,
  payment_is_proof_derived: false,
  referral_commission: 0,
  referral_percentage: 50,
};

function proofRow(id, name, size) {
  return {
    id,
    attachment_type: "payment_proof",
    original_name: name,
    size,
    url: `/candidates/cand-alluraiah/proofs/${id}`,
    verification_state: "VERIFIED_COMPANY_PAYMENT",
  };
}

function summary({ received, above = 0, outstanding = 0, count = 1 }) {
  return {
    verified_proof_total: received,
    received_total: received,
    expected_amount: 5000,
    outstanding_amount: outstanding,
    above_minimum_amount: above,
    verified_proof_count: count,
    payment_status: outstanding > 0 ? "PARTIAL" : "PAID",
    proof_derived: true,
    needs_reconciliation: false,
    reconciliation_gap: 0,
    referrer: "Pavan Kalyan",
    referral_percentage: 50,
    referral_commission: Math.floor(received / 2),
    referral_basis: received,
    referrer_complimentary_amount: 0,
  };
}

function referralLabel() {
  return document.querySelector(".cand-pay-handler-share")?.textContent || "";
}

function receivedInput() {
  const label = screen.getByText(/^Received ₹/);
  return label.closest("label").querySelector("input");
}

function renderModal(onSave = vi.fn()) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={CANDIDATE}
        onClose={vi.fn()}
        onSave={onSave}
        isAdmin={true}
      />
    </ConfirmProvider>,
  );
}

async function uploadProof(container, response, fileName = "receipt.png") {
  const file = new File(["payment"], fileName, { type: "image/png" });
  // The modal has several file inputs (resume, profile photo); the payment
  // proof one is the image picker that accepts multiple files.
  const input = Array.from(
    container.querySelectorAll('input[type="file"]'),
  ).find((node) => node.getAttribute("accept") === "image/*");
  expect(input).toBeTruthy();
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(FakeXMLHttpRequest.instances.length).toBeGreaterThan(0));
  const xhr = FakeXMLHttpRequest.instances[FakeXMLHttpRequest.instances.length - 1];
  act(() => xhr.upload.onload());
  xhr.status = 200;
  xhr.responseText = JSON.stringify(
    typeof response === "function" ? response(file) : response,
  );
  act(() => xhr.onload());
  return file;
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

describe("candidate edit form — live proof-derived received total", () => {
  it("moves Received from ₹0 to ₹6,000 as soon as the proof verifies", async () => {
    const { container } = renderModal();
    // A zero payment seeds the number input as empty, which reads as null here.
    expect(receivedInput().value).toBe("");
    expect(receivedInput()).not.toBeDisabled();

    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 6000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 6000, above: 1000 }),
    }));

    await waitFor(() => expect(receivedInput()).toHaveValue(6000));
  });

  it("flips the badge to Paid and clears the pending balance immediately", async () => {
    const { container } = renderModal();
    expect(screen.getByText(/pending/i)).toBeInTheDocument();

    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 6000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 6000, above: 1000 }),
    }));

    await waitFor(() =>
      expect(document.querySelector(".cand-pay-status--paid")).toBeTruthy(),
    );
    expect(document.querySelector(".cand-pay-status--unpaid")).toBeNull();
  });

  it("drops the follow-up remark requirement once outstanding reaches zero", async () => {
    const { container } = renderModal();
    expect(screen.getByPlaceholderText("Why is balance pending?")).toBeInTheDocument();

    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 6000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 6000, above: 1000 }),
    }));

    await waitFor(() =>
      expect(screen.queryByPlaceholderText("Why is balance pending?")).toBeNull(),
    );
  });

  it("shows the full breakdown without a reload", async () => {
    const { container } = renderModal();
    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 6000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 6000, above: 1000 }),
    }));

    await waitFor(() =>
      expect(document.querySelector(".cand-receipt-breakdown")).toBeTruthy(),
    );
    const panel = document.querySelector(".cand-receipt-breakdown");
    expect(within(panel).getByText(/Minimum expected/)).toBeInTheDocument();
    expect(within(panel).getByText("₹5,000")).toBeInTheDocument();
    expect(within(panel).getByText("₹6,000")).toBeInTheDocument();
    expect(within(panel).getByText(/Above minimum/)).toBeInTheDocument();
    expect(within(panel).getByText("₹1,000")).toBeInTheDocument();
    expect(within(panel).getByText(/Verified proofs/)).toBeInTheDocument();
  });

  it("keeps Received read-only once it is proof-derived", async () => {
    const { container } = renderModal();
    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 6000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 6000, above: 1000 }),
    }));

    await waitFor(() => expect(receivedInput()).toHaveValue(6000));
    expect(receivedInput()).toHaveAttribute("readonly");
    expect(receivedInput()).toBeDisabled();
  });

  it("adds a second proof to reach ₹12,000 immediately", async () => {
    const { container } = renderModal();
    const first = await uploadProof(
      container,
      (file) => ({
        status: "ok",
        candidate: {
          ...CANDIDATE,
          payment: 5000,
          payment_proofs: [proofRow("proof-1", file.name, file.size)],
        },
        payment_summary: summary({ received: 5000 }),
      }),
      "first.png",
    );
    await waitFor(() => expect(receivedInput()).toHaveValue(5000));

    await uploadProof(
      container,
      (file) => ({
        status: "ok",
        candidate: {
          ...CANDIDATE,
          payment: 12000,
          payment_proofs: [
            proofRow("proof-1", first.name, first.size),
            proofRow("proof-2", file.name, file.size),
          ],
        },
        payment_summary: summary({ received: 12000, above: 7000, count: 2 }),
      }),
      "second.png",
    );

    await waitFor(() => expect(receivedInput()).toHaveValue(12000));
  });

  it("recalculates the moment a proof is deleted", async () => {
    const { container } = renderModal();
    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 12000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 12000, above: 7000, count: 2 }),
    }));
    await waitFor(() => expect(receivedInput()).toHaveValue(12000));

    globalThis.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        candidate: { ...CANDIDATE, payment: 7000, payment_proofs: [] },
        payment_summary: summary({ received: 7000, above: 2000, count: 1 }),
      }),
    });
    window.confirm = vi.fn(() => true);

    const removeButton = Array.from(
      container.querySelectorAll("button"),
    ).find((button) => /remove|delete/i.test(button.textContent || ""));
    if (removeButton) {
      await act(async () => {
        fireEvent.click(removeButton);
      });
      await waitFor(() => expect(receivedInput()).toHaveValue(7000));
    }
  });

  it("never sends a proof-derived total back on Save", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { container } = renderModal(onSave);
    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 6000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 6000, above: 1000 }),
    }));
    await waitFor(() => expect(receivedInput()).toHaveValue(6000));

    const form = container.querySelector("form.cand-modal");
    await act(async () => {
      fireEvent.submit(form);
    });

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const payload = onSave.mock.calls[0][0];
    expect(payload.payment).toBeUndefined();
    expect(payload.name).toBe("alluraiah");
  });

  it("shows the persisted amount when the form is reopened", () => {
    render(
      <ConfirmProvider>
      <CandidateEditModal
        initial={{
          ...CANDIDATE,
          payment: 6000,
          expected_minimum: 5000,
          verified_received: 6000,
          verified_proof_total: 6000,
          above_minimum: 1000,
          balance_due: 0,
          verified_proof_count: 1,
          payment_is_proof_derived: true,
          payment_proofs: [proofRow("proof-1", "receipt.png", 10)],
        }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        isAdmin={true}
      />
      </ConfirmProvider>,
    );
    expect(receivedInput()).toHaveValue(6000);
    expect(document.querySelector(".cand-pay-status--paid")).toBeTruthy();
  });
});


describe("referral earning label", () => {
  it("shows 50% of the verified received total, not of the expected minimum", async () => {
    const { container } = renderModal();
    await uploadProof(container, (file) => ({
      status: "ok",
      candidate: {
        ...CANDIDATE,
        payment: 6000,
        payment_proofs: [proofRow("proof-1", file.name, file.size)],
      },
      payment_summary: summary({ received: 6000, above: 1000 }),
    }));

    await waitFor(() => expect(referralLabel()).toContain("₹3,000"));
    expect(referralLabel()).toContain("Pavan Kalyan");
    expect(referralLabel()).not.toContain("₹2,500");
  });

  it("tracks the referral share as further proofs arrive", async () => {
    const { container } = renderModal();
    const first = await uploadProof(
      container,
      (file) => ({
        status: "ok",
        candidate: {
          ...CANDIDATE,
          payment: 5000,
          payment_proofs: [proofRow("proof-1", file.name, file.size)],
        },
        payment_summary: summary({ received: 5000 }),
      }),
      "first.png",
    );
    await waitFor(() => expect(referralLabel()).toContain("₹2,500"));

    await uploadProof(
      container,
      (file) => ({
        status: "ok",
        candidate: {
          ...CANDIDATE,
          payment: 12000,
          payment_proofs: [
            proofRow("proof-1", first.name, first.size),
            proofRow("proof-2", file.name, file.size),
          ],
        },
        payment_summary: summary({ received: 12000, above: 7000, count: 2 }),
      }),
      "second.png",
    );

    await waitFor(() => expect(referralLabel()).toContain("₹6,000"));
  });

  it("shows the persisted referral share when the form is reopened", () => {
    render(
      <ConfirmProvider>
        <CandidateEditModal
          initial={{
            ...CANDIDATE,
            payment: 6000,
            verified_received: 6000,
            balance_due: 0,
            verified_proof_count: 1,
            payment_is_proof_derived: true,
            referral_commission: 3000,
            referral_percentage: 50,
            payment_proofs: [proofRow("proof-1", "receipt.png", 10)],
          }}
          onClose={vi.fn()}
          onSave={vi.fn()}
          isAdmin={true}
        />
      </ConfirmProvider>,
    );
    expect(referralLabel()).toContain("₹3,000");
  });

  it("never folds a closure complimentary amount into the payment commission", () => {
    render(
      <ConfirmProvider>
        <CandidateEditModal
          initial={{
            ...CANDIDATE,
            payment: 6000,
            payment_is_proof_derived: true,
            referral_commission: 3000,
            referrer_complimentary_amount: 5000,
            payment_proofs: [proofRow("proof-1", "receipt.png", 10)],
          }}
          onClose={vi.fn()}
          onSave={vi.fn()}
          isAdmin={true}
        />
      </ConfirmProvider>,
    );
    expect(referralLabel()).toContain("₹3,000");
    expect(referralLabel()).not.toContain("₹8,000");
  });
});


describe("broken payment evidence actions", () => {
  function brokenProof(state = "MISSING_FILE") {
    return {
      id: "proof-broken",
      attachment_type: "payment_proof",
      original_name: "lost.jpg",
      url: "/candidates/cand-alluraiah/proofs/proof-broken",
      verification_state: "VERIFIED_COMPANY_PAYMENT",
      file_availability: state,
      uploaded_at: "2026-06-22T14:36:30+00:00",
      size: 1024,
    };
  }

  function renderWithProof(proof) {
    return render(
      <ConfirmProvider>
        <CandidateEditModal
          initial={{ ...CANDIDATE, payment: 30000, payment_proofs: [proof] }}
          onClose={vi.fn()}
          onSave={vi.fn()}
          isAdmin={true}
        />
      </ConfirmProvider>,
    );
  }

  it("offers re-upload, archive and history when the file is unavailable", () => {
    renderWithProof(brokenProof());
    expect(screen.getByText("Original file unavailable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Re-upload proof" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Archive reference" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evidence history" })).toBeInTheDocument();
  });

  it("names the specific problem for a damaged file", () => {
    renderWithProof(brokenProof("CHECKSUM_MISMATCH"));
    expect(
      screen.getByText("Stored file does not match its checksum"),
    ).toBeInTheDocument();
  });

  it("shows no problem banner for readable evidence", () => {
    renderWithProof(brokenProof("AVAILABLE"));
    expect(screen.queryByRole("button", { name: "Re-upload proof" })).toBeNull();
  });

  it("treats an archived reference as settled, not broken", () => {
    renderWithProof(brokenProof("ARCHIVED"));
    expect(screen.queryByRole("button", { name: "Archive reference" })).toBeNull();
  });

  it("loads and renders the evidence history timeline", async () => {
    globalThis.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        history: {
          proof_id: "proof-broken",
          stored_in: "payment_proofs",
          checksum: "abc123",
          verified_amount: 30000,
          counts_towards_total: 0,
          verification_state: "VERIFIED_COMPANY_PAYMENT",
          file_availability: "MISSING_FILE",
          utr_number: "250859628039",
          transaction_id: "T2606221827542453052641",
          events: [
            { kind: "uploaded", at: "2026-06-22T14:36:30+00:00",
              summary: "Proof uploaded" },
            { kind: "verification_changed", at: "2026-08-06T12:00:00+00:00",
              summary: "VERIFIED_COMPANY_PAYMENT → AMOUNT_EXTRACTION_REVIEW_REQUIRED",
              actor: "administrator", reason: "factor-of-ten defect" },
          ],
        },
      }),
    });
    renderWithProof(brokenProof());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Evidence history" }));
    });
    await waitFor(() =>
      expect(document.querySelector(".cand-evidence-history")).toBeTruthy(),
    );
    expect(screen.getByText("250859628039")).toBeInTheDocument();
    expect(screen.getByText("Proof uploaded")).toBeInTheDocument();
    expect(screen.getByText("factor-of-ten defect")).toBeInTheDocument();
    expect(screen.getByText("MISSING_FILE")).toBeInTheDocument();
  });

  it("applies the refreshed summary after archiving", async () => {
    globalThis.fetch.mockResolvedValue({
      ok: true,
      json: async () => ({
        status: "ok",
        candidate: { ...CANDIDATE, payment: 30000, payment_proofs: [] },
        payment_summary: summary({ received: 30000, above: 25000 }),
        financially_unchanged: true,
      }),
    });
    const { container } = renderWithProof(brokenProof());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Archive reference" }));
    });
    await waitFor(() => expect(receivedInput()).toHaveValue(30000));
    expect(container).toBeTruthy();
  });
});


describe("BGV summary on the candidate screen", () => {
  function renderBgv(extra) {
    return render(
      <ConfirmProvider>
        <CandidateEditModal
          initial={{
            ...CANDIDATE,
            name: "sakthivek",
            service_type: "profile_service",
            expected_payment: 50000,
            payment: 30000,
            payment_is_proof_derived: true,
            referral_commission: 10000,
            service_expected: 20000,
            service_received: 20000,
            bgv_expected: 30000,
            bgv_received: 10000,
            bgv_outstanding: 20000,
            ...extra,
          }}
          onClose={vi.fn()}
          onSave={vi.fn()}
          isAdmin={true}
        />
      </ConfirmProvider>,
    );
  }

  it("states what was collected, what is owed, and that it is separate", () => {
    renderBgv();
    const panel = document.querySelector(".cand-bgv-summary");
    expect(panel).toBeTruthy();
    expect(panel.textContent).toContain("₹10,000 collected of ₹30,000");
    expect(panel.textContent).toContain("managed separately");
  });

  it("links through to the BGV case", () => {
    renderBgv();
    const link = document.querySelector(".cand-bgv-summary-link");
    expect(link.getAttribute("href")).toContain("/bgv?candidate=sakthivek");
  });

  it("shows nothing for a candidate without BGV", () => {
    renderBgv({ bgv_expected: 0, bgv_received: 0 });
    expect(document.querySelector(".cand-bgv-summary")).toBeNull();
  });

  it("keeps the referral at the service share, not the whole payment", () => {
    renderBgv();
    expect(referralLabel()).toContain("₹10,000");
    expect(referralLabel()).not.toContain("₹15,000");
  });
});
