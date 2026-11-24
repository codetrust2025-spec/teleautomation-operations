/**
 * Deleting one payment screenshot of three (owner, 24 Nov).
 *
 * The editor shows a candidate's proofs from all its rows together. Deleting
 * one sent the right id, but the editor then replaced its whole list with the
 * proofs of the one row that owned it, so the other screenshots vanished from
 * the screen. Now: the delete names that proof alone and the candidate on
 * screen (`?view=`), and the list keeps every other proof with its status.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CandidateEditModal, proofsAfterDelete } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const proof = (id, owner, amount, utr) => ({
  id,
  candidate_id: owner,
  attachment_type: "payment_proof",
  url: `/candidates/${owner}/attachments/payment_proof/${id}`,
  original_name: `${id}.jpg`,
  uploaded_at: "2026-11-14T04:20:00+00:00",
  verification_state: "VERIFIED_COMPANY_PAYMENT",
  verified_amount: amount,
  utr_number: utr,
});
const P40 = proof("p40", "rowa", 40000, "559789457773");
const P3A = proof("p3a", "rowb", 3000, "111795298203");
const P3B = proof("p3b", "rowb", 3000, "126427114041");

const CANDIDATE = {
  id: "rowa", name: "Delete Onetest", phone: "9000000731", technology: "Java", reference: "Karthik",
  stage: "in_progress", service_type: "profile_service", expected_payment: 20000, expected_minimum: 20000,
  payment: 46000, verified_received: 46000, verified_proof_total: 46000, verified_proof_count: 3,
  proof_count: 3, balance_due: 0, payment_is_proof_derived: true,
  payment_proofs: [P40, P3A, P3B],
};
const summary = (total, count) => ({
  received_total: total, verified_proof_total: total, expected_amount: 20000, outstanding_amount: 0,
  above_minimum_amount: Math.max(0, total - 20000), verified_proof_count: count, proof_count: count,
  proof_derived: true, payment_status: "PAID",
});
const dialogButton = (label) =>
  Array.from(document.querySelectorAll(".cm-btn")).find((b) => b.textContent.trim() === label);
const shownIds = () => [...document.querySelectorAll("[data-proof-id]")].map((el) => el.getAttribute("data-proof-id"));

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:p"), revokeObjectURL: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderModal() {
  render(
    <ConfirmProvider>
      <CandidateEditModal initial={CANDIDATE} onClose={vi.fn()} onSave={vi.fn()} isAdmin={true} />
    </ConfirmProvider>,
  );
}

async function deleteProof(utrLabel) {
  const card = [...document.querySelectorAll("[data-proof-id]")].find((el) => el.textContent.includes(utrLabel));
  fireEvent.click(card.querySelector(".cand-proof-delete"));
  await screen.findByText("Delete this payment proof?");
  fireEvent.click(dialogButton("Delete proof"));
}

describe("deleting 1 of 3 screenshots", () => {
  it("removes only that one; the other two stay with their status, and the totals follow", async () => {
    // The server's answer, with ?view: the candidate as shown, two proofs left.
    fetch.mockResolvedValue({
      json: async () => ({
        status: "ok",
        candidate: { ...CANDIDATE, payment_proofs: [P40, P3B], payment: 43000 },
        payment_summary: summary(43000, 2),
      }),
    });
    renderModal();
    expect(shownIds()).toEqual(["p40", "p3a", "p3b"]);
    await deleteProof("111795298203");
    await waitFor(() => expect(shownIds()).toEqual(["p40", "p3b"]));
    const [url, options] = fetch.mock.calls[0];
    expect(options.method).toBe("DELETE");
    // That proof alone, on the row that holds it; the candidate on screen named.
    expect(url).toMatch(/\/candidates\/rowb\/proofs\/p3a\?view=rowa$/);
    for (const id of ["p40", "p3b"]) {
      expect(document.querySelector(`[data-proof-id="${id}"]`).textContent).toContain("Verified");
    }
    expect(document.body.textContent).toContain("₹43,000");
  });

  it("keeps the others even when the answer holds only the owner row's proofs (the old reply)", async () => {
    fetch.mockResolvedValue({
      json: async () => ({ status: "ok", candidate: { id: "rowb", payment_proofs: [P3B] }, payment_summary: summary(43000, 2) }),
    });
    renderModal();
    await deleteProof("111795298203");
    await waitFor(() => expect(shownIds()).toEqual(["p40", "p3b"]));
  });

  it("a failed delete leaves all three", async () => {
    fetch.mockResolvedValue({ json: async () => ({ status: "error", message: "Proof not found" }) });
    renderModal();
    await deleteProof("559789457773");
    expect(await screen.findByText("Proof not found")).toBeInTheDocument();
    expect(shownIds()).toEqual(["p40", "p3a", "p3b"]);
  });
});

describe("proofsAfterDelete", () => {
  it("drops the deleted id only, keeps order, and takes the server's newer copies", () => {
    const newer = { ...P40, note: "checked" };
    expect(proofsAfterDelete([P40, P3A, P3B], "p3a", [newer]).map((p) => [p.id, p.note])).toEqual([
      ["p40", "checked"], ["p3b", undefined],
    ]);
    expect(proofsAfterDelete([P40, P3A, P3B], "p3a", [P3A]).map((p) => p.id)).toEqual(["p40", "p3b"]);
  });
});
