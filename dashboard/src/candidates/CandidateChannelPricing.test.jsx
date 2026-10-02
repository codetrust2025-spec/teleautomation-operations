/**
 * Profile channel in Candidate Edit.
 *
 *   Direct       checked = the candidate came directly to us      -> ₹20,000
 *   Consultancy  checked = the candidate came through consultancy -> ₹15,000
 *
 * Both choices are always visible and exactly one is selected, so an unchecked
 * "Direct" can never sit beside ₹20,000. The BGV add-on (₹30,000) rides on
 * either; an amount agreed by hand is never rewritten by a channel change.
 */
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const PROFILE = {
  id: "cand-ch-1",
  name: "Test Channel Person",
  phone: "9000000801",
  technology: "Java",
  reference: "Test Owner",
  stage: "in_progress",
  service_type: "profile_service",
  ctc_percentage: 10,
  consultancy: false,
  bgv_certificates: false,
  expected_payment: 20000,
  payment: 0,
  payment_proofs: [],
  expected_minimum: 20000,
  verified_proof_count: 0,
  balance_due: 20000,
  payment_is_proof_derived: false,
  referral_percentage: 50,
};

function renderModal(candidate) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={{ ...PROFILE, ...candidate }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        isAdmin={true}
      />
    </ConfirmProvider>,
  );
}

const direct = () => screen.getByRole("radio", { name: /Direct/ });
const consultancy = () => screen.getByRole("radio", { name: /Consultancy/ });
const expectedInput = () => screen.getByText(/^Expected ₹/).closest("label").querySelector("input");

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:p"), revokeObjectURL: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the two channels are explicit and always consistent", () => {
  it("a direct candidate: Direct is checked, Consultancy is not, and ₹20,000 is expected", () => {
    renderModal({ consultancy: false });
    expect(direct()).toBeChecked();
    expect(consultancy()).not.toBeChecked();
    expect(expectedInput()).toHaveValue(20000);
  });

  it("a consultancy candidate: Consultancy is checked, Direct is not, and ₹15,000 is expected", () => {
    renderModal({ consultancy: true, expected_payment: 15000, expected_minimum: 15000, balance_due: 15000 });
    expect(consultancy()).toBeChecked();
    expect(direct()).not.toBeChecked();
    expect(expectedInput()).toHaveValue(15000);
  });

  it("a candidate with no channel stored is direct", () => {
    renderModal({ consultancy: undefined });
    expect(direct()).toBeChecked();
  });

  it("each choice states what it means and what it costs", () => {
    renderModal({});
    expect(screen.getByText(/came directly to us/)).toBeInTheDocument();
    expect(screen.getByText(/came through a consultancy/)).toBeInTheDocument();
    expect(direct().closest("label").textContent).toContain("₹20,000");
    expect(consultancy().closest("label").textContent).toContain("₹15,000");
  });

  it("there is never a state with neither selected", () => {
    renderModal({});
    fireEvent.click(consultancy());
    expect([direct().checked, consultancy().checked].filter(Boolean)).toHaveLength(1);
    fireEvent.click(direct());
    expect([direct().checked, consultancy().checked].filter(Boolean)).toHaveLength(1);
  });
});

describe("choosing a channel reprices the default and nothing else", () => {
  it("Direct -> Consultancy moves ₹20,000 to ₹15,000, and back", () => {
    renderModal({});
    fireEvent.click(consultancy());
    expect(expectedInput()).toHaveValue(15000);
    fireEvent.click(direct());
    expect(expectedInput()).toHaveValue(20000);
  });

  it("the BGV add-on follows the channel instead of staying at the direct price", () => {
    renderModal({ bgv_certificates: true, expected_payment: 50000, expected_minimum: 50000, balance_due: 50000 });
    expect(expectedInput()).toHaveValue(50000);
    fireEvent.click(consultancy());
    expect(expectedInput()).toHaveValue(45000);
    fireEvent.click(direct());
    expect(expectedInput()).toHaveValue(50000);
  });

  it("an amount agreed by hand is kept when the channel changes", () => {
    renderModal({ expected_payment: 22000, expected_minimum: 22000, balance_due: 22000 });
    fireEvent.click(consultancy());
    expect(expectedInput()).toHaveValue(22000);
  });

  it("a custom amount with BGV is kept too", () => {
    renderModal({ bgv_certificates: true, expected_payment: 60000, expected_minimum: 60000, balance_due: 60000 });
    fireEvent.click(consultancy());
    expect(expectedInput()).toHaveValue(60000);
  });

  it("the handler's earnings line follows the repriced amount's payment, not the channel label", () => {
    renderModal({ payment: 15000, payment_is_proof_derived: false });
    expect(screen.getByRole("radio", { name: /Direct/ })).toBeChecked();
    expect(document.body.textContent).toMatch(/5,000 pending|₹5,000/);
  });
});

describe("where the channel applies", () => {
  it("is not offered for a round-wise candidate", () => {
    renderModal({ service_type: "round_wise", interview_scope: "external", expected_payment: 5000, expected_minimum: 5000, balance_due: 5000, ctc_percentage: "" });
    expect(screen.queryByRole("radio", { name: /Consultancy/ })).toBeNull();
    expect(screen.queryByText(/came directly to us/)).toBeNull();
  });

  it("is offered when adding a new Profile candidate, defaulting to Direct", () => {
    render(
      <ConfirmProvider>
        <CandidateEditModal initial={null} onClose={vi.fn()} onSave={vi.fn()} isAdmin={true} />
      </ConfirmProvider>,
    );
    expect(direct()).toBeChecked();
    expect(consultancy()).not.toBeChecked();
  });

  it("is available to a handler as well as an admin", () => {
    render(
      <ConfirmProvider>
        <CandidateEditModal initial={{ ...PROFILE }} onClose={vi.fn()} onSave={vi.fn()} isAdmin={false} />
      </ConfirmProvider>,
    );
    expect(direct()).toBeChecked();
  });

  it("is still shown with a proof-derived, locked Received", () => {
    renderModal({ payment: 20000, payment_is_proof_derived: true, verified_received: 20000, verified_proof_count: 1, proof_count: 1, balance_due: 0 });
    expect(direct()).toBeChecked();
  });
});
