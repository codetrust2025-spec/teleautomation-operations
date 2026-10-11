/**
 * Edit Candidate without the Follow-up / Remark field (11 Oct).
 *
 * The field, and the rule that a balance needed a remark before saving, are
 * gone. A remark saved earlier is kept: Save does not send one at all, so the
 * stored text is never replaced -- it used to send "" once the balance was
 * cleared. Saving a candidate whose interview is already booked is not judged
 * again by the slot rules (it used to need Rs 10,000 recorded). Invented names.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const ROW = {
  id: "c-r1", name: "Asha Fakename", phone: "9000000611", technology: "Salesforce", reference: "Test Owner",
  stage: "in_progress", service_type: "profile_service", ctc_percentage: 10, expected_payment: 20000,
  payment: 0, balance_due: 20000, payment_proofs: [],
  follow_up: "Promised to pay after the first interview",
  slot_confirmed: true, slots_group_posted: true, date: "2026-10-01", time: "10:00",
};

function renderModal(row = ROW, { isAdmin = false } = {}) {
  const onSave = vi.fn(async () => {});
  render(
    <ConfirmProvider>
      <CandidateEditModal initial={row} onClose={vi.fn()} onSave={onSave} isAdmin={isAdmin} />
    </ConfirmProvider>,
  );
  return onSave;
}

const save = () => fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("no Follow-up / Remark", () => {
  it("is not on the form, even with a balance pending", () => {
    renderModal();
    expect(screen.queryByText("Follow-up / Remark")).toBeNull();
    expect(screen.queryByPlaceholderText("Why is balance pending?")).toBeNull();
  });

  it("is not required to save, and a remark saved before is kept (not sent, so not replaced)", async () => {
    const onSave = renderModal();
    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const sent = onSave.mock.calls[0][0];
    expect(sent.follow_up).toBeUndefined();
    expect(JSON.stringify(sent)).not.toContain("follow_up");
    expect(screen.queryByText(/add a short follow-up/)).toBeNull();
  });

  it("is not erased when the balance is cleared", async () => {
    const onSave = renderModal({
      ...ROW, payment: 20000, balance_due: 0, verified_received: 20000, verified_proof_count: 1, proof_count: 1,
      payment_is_proof_derived: true,
      payment_proofs: [{ id: "p1", attachment_type: "payment_proof", url: "/x/p1", original_name: "p1.png",
        verification_state: "VERIFIED_COMPANY_PAYMENT", verified_amount: 20000 }],
    });
    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(JSON.stringify(onSave.mock.calls[0][0])).not.toContain("follow_up");
  });
});

describe("a booked interview is not judged again on every save", () => {
  it("a handler can save a candidate whose interview is booked with nothing paid yet", async () => {
    const onSave = renderModal({ ...ROW, technology: "Java Backend" }, { isAdmin: false });
    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(screen.queryByText(/Record at least/)).toBeNull();
  });
});
