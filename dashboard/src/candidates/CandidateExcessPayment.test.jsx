/**
 * Money received above the agreed figure is shown as an excess, in words a
 * reviewer reads as "paid more than expected" — not as a shortfall or a fault.
 * A round-wise fee is a minimum by design, so it keeps the minimum wording.
 */
import React from "react";
import { cleanup, render, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const PROFILE = {
  id: "cand-excess",
  name: "Test Excess",
  phone: "9000000107",
  technology: "Java",
  reference: "Pavan Kalyan",
  stage: "in_progress",
  service_type: "profile_service",
  expected_payment: 20000,
  payment: 30000,
  payment_proofs: [],
  expected_minimum: 20000,
  verified_received: 30000,
  verified_proof_count: 2,
  above_minimum: 10000,
  balance_due: 0,
  payment_is_proof_derived: true,
  referral_commission: 15000,
  referral_percentage: 50,
};

function panelFor(candidate) {
  render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={candidate}
        onClose={vi.fn()}
        onSave={vi.fn()}
        isAdmin={true}
      />
    </ConfirmProvider>,
  );
  return within(document.querySelector(".cand-receipt-breakdown"));
}

describe("payment above the agreed figure", () => {
  afterEach(cleanup);

  it("labels a profile-service overpayment as an excess with the exact amount", () => {
    const panel = panelFor(PROFILE);
    expect(panel.getByText(/Excess payment/)).toBeInTheDocument();
    expect(panel.getByText("₹10,000")).toBeInTheDocument();
    expect(panel.getByText("₹30,000")).toBeInTheDocument();
    expect(panel.queryByText(/Above minimum/)).toBeNull();
    expect(panel.getByText(/Outstanding/)).toBeInTheDocument();
  });

  it("shows no excess line when the payment is exact", () => {
    const panel = panelFor({
      ...PROFILE,
      payment: 20000,
      verified_received: 20000,
      verified_proof_count: 1,
      above_minimum: 0,
    });
    expect(panel.queryByText(/Excess payment/)).toBeNull();
  });

  it("keeps the minimum wording for a round-wise fee", () => {
    const panel = panelFor({
      ...PROFILE,
      service_type: "round_wise",
      interview_scope: "external",
      expected_payment: 5000,
      expected_minimum: 5000,
      payment: 10000,
      verified_received: 10000,
      above_minimum: 5000,
    });
    expect(panel.getByText(/Minimum expected/)).toBeInTheDocument();
    expect(panel.getByText(/Above minimum/)).toBeInTheDocument();
    expect(panel.queryByText(/Excess payment/)).toBeNull();
  });
});
