/**
 * Candidates table, 12 Oct audit. Names and numbers are invented.
 *
 * - The payment cell says what its screenshots are: "✓ verified", "awaiting
 *   review" or "not accepted". A "Paid · 1 proof" beside a proof nobody had
 *   verified read as checked money, and on a ₹0 row a proof waiting for its
 *   receiver to be confirmed was not shown at all.
 * - An amount recorded above what the row expects is flagged, not corrected.
 * - A row that holds an interview names it ("Interview L2 · 17 Sept"), so an
 *   L1 and an L2 of one name, logged the same day, no longer look like a
 *   duplicate.
 * - Phone numbers read grouped ("90000 00811"); links keep the number.
 * - The table reads at 12-13px with Daily Ops' row tint, and keeps 940px.
 */
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../context/AuthContext.jsx", () => ({
  useAuth: () => ({ role: "admin", reference: "", enabled: false }),
}));
vi.mock("../context/ConfirmContext.jsx", () => ({
  useConfirm: () => ({ confirm: vi.fn(async () => true) }),
}));

import { _Component27 as PaymentCell, formatPhoneDisplay, interviewLabel, proofReviewSummary } from "./candidatesModule.jsx";

const here = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(here, "..", "index.css"), "utf-8").replace(/\r\n/g, "\n");

afterEach(cleanup);

const proof = (id, state, amount = 5000) => ({
  id, url: `/candidates/x/proofs/${id}`, original_name: `${id}.png`, attachment_type: "payment_proof",
  verification_state: state, verified_amount: amount, company_payment_verified: state === "VERIFIED_COMPANY_PAYMENT",
});
const row = (extra) => ({ id: "c1", name: "Asha Fakename", service_type: "round_wise", payment: 5000, expected_payment: 5000, payment_proofs: [], ...extra });
const cellText = () => document.querySelector(".cand-pay").textContent.replace(/\s+/g, " ").trim();

describe("the payment cell", () => {
  it("marks verified screenshots as verified", () => {
    render(<PaymentCell row={row({ payment_proofs: [proof("p1", "VERIFIED_COMPANY_PAYMENT")] })} />);
    expect(cellText()).toMatch(/Paid.*1 proof.*✓ verified.*View/);
    expect(document.querySelector(".cand-pay-proofs--verified")).not.toBeNull();
  });

  it("says a screenshot is awaiting review instead of letting Paid stand alone", () => {
    render(<PaymentCell row={row({ payment_proofs: [proof("p1", "UNKNOWN_RECEIVER")] })} />);
    expect(cellText()).toMatch(/Paid.*1 proof.*awaiting review/);
    expect(screen.getByRole("button", { name: /View 1 payment screenshot, awaiting review/ })).toBeTruthy();
  });

  it("shows a proof waiting on a ₹0 row, beside what is due", () => {
    render(<PaymentCell row={row({ payment: 0, payment_proofs: [proof("p1", "INCOMPLETE_PAYMENT_EVIDENCE")] })} />);
    expect(cellText()).toMatch(/₹0.*₹5k due.*1 proof.*awaiting review.*View/);
  });

  it("flags an amount above what the row expects, and changes nothing", () => {
    const r = row({ payment: 10000, payment_proofs: [proof("p1", "VERIFIED_COMPANY_PAYMENT"), proof("p2", "VERIFIED_COMPANY_PAYMENT")] });
    render(<PaymentCell row={r} />);
    expect(screen.getByText("₹5k above expected").getAttribute("title")).toMatch(/Recorded ₹10,000 against ₹5,000 expected/);
    expect(r.payment).toBe(10000);
  });

  it("leaves older screenshots with no review state as a plain count", () => {
    render(<PaymentCell row={row({ payment_proofs: [proof("p1", undefined)] })} />);
    expect(cellText()).toMatch(/Paid.*1 proof.*View/);
    expect(document.querySelector(".cand-pay-proofs-state")).toBeNull();
  });

  it("counts proofs by review state", () => {
    expect(proofReviewSummary([proof("a", "VERIFIED_COMPANY_PAYMENT"), proof("b", "UNKNOWN_RECEIVER"), proof("c", "REJECTED"), proof("d", undefined)]))
      .toEqual({ total: 4, verified: 1, awaiting: 1, notAccepted: 1 });
  });
});

describe("the interview a row holds", () => {
  const year = new Date().getFullYear();
  it("is named by round and day, with the year only outside this year", () => {
    expect(interviewLabel({ date: `${year}-09-17`, time: "17:00", interview_round: "L2" })).toMatch(/^L2 · 17 Sep/);
    expect(interviewLabel({ date: `${year}-09-17`, time: "17:00", interview_round: "L2" })).not.toMatch(String(year));
    expect(interviewLabel({ date: `${year + 1}-01-05`, time: "10:00" })).toMatch(String(year + 1));
    expect(interviewLabel({ date: `${year}-09-17`, time: "17:00", interview_round: "L2" }, true)).toMatch(/at 17:00$/);
  });

  it("is empty for a row with no interview", () => {
    expect(interviewLabel({ date: `${year}-09-17` })).toBe("");
    expect(interviewLabel({ interview_round: "L1" })).toBe("");
  });
});

describe("phone numbers", () => {
  it("read grouped, and anything unusual stays as stored", () => {
    expect(formatPhoneDisplay("9000000811")).toBe("90000 00811");
    expect(formatPhoneDisplay("+91 90000-00811")).toBe("+91 90000 00811");
    expect(formatPhoneDisplay("+1 415 555 0100")).toBe("+1 415 555 0100");
    expect(formatPhoneDisplay("")).toBe("");
  });
});

describe("the stylesheet", () => {
  const block = CSS.slice(CSS.indexOf("/* ---- Candidates table, 12 Oct audit"), CSS.indexOf("Edit Candidate, compact (Oct 2026)"));

  it("sits before the compact Edit block, which stays the last word on the modal", () => {
    expect(block.length).toBeGreaterThan(200);
  });

  it("reads at 12px, 13px from 1181px, and keeps a 940px table", () => {
    expect(block).toMatch(/\.cand-page--candidates > \.cand-table-wrap \.cand-table \{ min-width: 940px; \}/);
    expect(block).toMatch(/\.cand-page--candidates \.cand-table tbody td \{ font-size: 12px;/);
    expect(block).toMatch(/@media \(min-width: 1181px\) \{\n  \.cand-page--candidates \.cand-table tbody td \{ font-size: 13px; \}/);
  });

  it("keeps a stage on one line in sentence case, Update readable, notes bright, buttons visible", () => {
    expect(block).toMatch(/\.cand-th--stage \{ width: 132px; \}/);
    expect(block).toMatch(/\.cand-cell-stage \.cand-badge \{\n    font-size: 11px; letter-spacing: \.01em; text-transform: none; white-space: nowrap;/);
    expect(block).toMatch(/\.cand-resume-chip__cta \{\n    font-size: 12px; letter-spacing: 0; text-transform: none;/);
    expect(block).toMatch(/\.cand-cell-note \.cand-note__text \{ color: #cbd5e1; \}/);
    expect(block).toMatch(/td\.cand-cell-actions \.cand-btn \{\n    color: #e2e8f0; border: 1px solid/);
  });

  it("tints a row under the pointer, as Daily Ops does", () => {
    expect(block).toMatch(/@media \(min-width: 600px\) and \(hover: hover\)[\s\S]*tr\.cand-row:hover > td \{ box-shadow: inset/);
  });
});
