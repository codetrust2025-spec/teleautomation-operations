/**
 * Edit Candidate, compact layout (Oct 2026).
 *
 * The form was rebuilt to the approved mockup: a resume bar under the header,
 * fields two to a row, descriptions moved to tooltips, a one-line payment
 * summary and one compact row per payment proof with a View button. Nothing
 * was taken away: every field, the calculations, validation and the proof
 * actions are the same. jsdom does no layout, so the layout itself is asserted
 * on the stylesheet; it was measured in a browser at desktop and phone widths.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const PROFILE = {
  id: "cand-cp-1",
  name: "Compact Person",
  phone: "9000000801",
  email: "compact@example.test",
  technology: "Salesforce",
  reference: "Test Owner",
  stage: "in_progress",
  service_type: "profile_service",
  ctc_percentage: 12,
  expected_payment: 20000,
  payment: 0,
  payment_proofs: [],
  expected_minimum: 20000,
  balance_due: 20000,
};

const proof = (id, extra = {}) => ({
  id,
  attachment_type: "payment_proof",
  url: `/candidates/cand-cp-1/attachments/payment_proof/${id}`,
  original_name: `${id}.jpg`,
  size: 61112,
  uploaded_at: "2026-09-28T08:22:46+00:00",
  verification_state: "VERIFIED_COMPANY_PAYMENT",
  verified_amount: 20000,
  utr_number: "935144418237",
  ...extra,
});

const PAID = {
  payment: 20000,
  verified_received: 20000,
  verified_proof_total: 20000,
  verified_proof_count: 1,
  proof_count: 1,
  balance_due: 0,
  payment_is_proof_derived: true,
  payment_proofs: [proof("p1")],
};

function renderModal(candidate = {}) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal initial={{ ...PROFILE, ...candidate }} onClose={vi.fn()} onSave={vi.fn()} isAdmin={true} />
    </ConfirmProvider>,
  );
}

// A label's own words, without its tags ("manual", the lock).
const labels = () =>
  Array.from(document.querySelectorAll(".cand-modal .cand-field-label")).map((n) =>
    Array.from(n.childNodes).filter((c) => c.nodeType === 3).map((c) => c.textContent).join("").trim(),
  );

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:c"), revokeObjectURL: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the form", () => {
  it("is the compact form, with the resume bar between the header and the fields", () => {
    renderModal();
    const form = document.querySelector("form.cand-modal");
    expect(form.classList.contains("cand-modal--compact")).toBe(true);
    const order = Array.from(form.children).map((n) => n.className.split(" ")[0]);
    expect(order.slice(0, 3)).toEqual(["cand-modal-header", "cand-resume-bar", "cand-modal-body"]);
  });

  it("keeps every field, in the mockup's order (Date beside % on CTC)", () => {
    renderModal();
    expect(labels()).toEqual([
      "Candidate name *", "Stage", "Technology", "Phone", "Email", "Reference", "Date", "% on CTC *",
      "Service type", "Channel", "Additional services",
      "Expected ₹", "Received ₹", "Follow-up / Remark", "Payment proofs",
    ]);
  });

  it("puts the closure date after % on CTC for a completed candidate", () => {
    renderModal({ stage: "completed", closure_date: "2026-09-30" });
    const all = labels();
    expect(all.indexOf("Closure date")).toBe(all.indexOf("% on CTC *") + 1);
  });

  it("states no paragraph-length descriptions; they are tooltips", () => {
    renderModal({ stage: "completed", closure_date: "2026-09-30" });
    expect(document.querySelector(".cand-modal .cand-field-hint")).toBeNull();
    expect(screen.queryByText(/Earns 50% commission/)).toBeNull();
    expect(screen.getByText("Reference", { selector: ".cand-field-label" }).closest("label").getAttribute("title"))
      .toMatch(/Earns 50% commission/);
    expect(screen.getByText("Closure date", { selector: ".cand-field-label" }).closest("label").getAttribute("title"))
      .toMatch(/profile-closure complimentary/);
    expect(screen.queryByText(/Click a preview to enlarge/)).toBeNull();
  });
});

describe("payment proofs are compact rows with a View button", () => {
  it("a row shows amount, status, UTR and date, with View and an always-visible delete", () => {
    renderModal(PAID);
    const row = document.querySelector('[data-proof-id="p1"]');
    expect(within(row).getByText("₹20,000")).toBeInTheDocument();
    expect(within(row).getByText("Verified")).toBeInTheDocument();
    expect(within(row).getByText("935144418237")).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "View proof of ₹20,000" })).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: "Delete proof of ₹20,000" })).toBeInTheDocument();
  });

  it("View opens the full screenshot and changes nothing", () => {
    renderModal(PAID);
    fireEvent.click(screen.getByRole("button", { name: "View proof of ₹20,000" }));
    const lightbox = document.querySelector(".cand-proof-lightbox");
    expect(lightbox).toBeTruthy();
    expect(lightbox.querySelector("img").getAttribute("src")).toMatch(/\/attachments\/payment_proof\/p1$/);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("a proof whose file is gone has no View, and keeps its repair actions", () => {
    renderModal({ ...PAID, payment_proofs: [proof("p9", { file_availability: "MISSING_FILE" })] });
    const row = document.querySelector('[data-proof-id="p9"]');
    expect(within(row).queryByRole("button", { name: /View proof/ })).toBeNull();
    expect(within(row).getByRole("button", { name: "Re-upload proof" })).toBeInTheDocument();
    expect(within(row).getByText("Screenshot unavailable")).toBeInTheDocument();
  });
});

describe("the stylesheet", () => {
  const CSS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "index.css"), "utf-8").replace(/\r\n/g, "\n");
  const BLOCK = CSS.slice(CSS.indexOf("Edit Candidate, compact (Oct 2026)")).replace(/\/\*[\s\S]*?\*\//g, "");
  const rule = (selector) => {
    const re = new RegExp("(^|\\n)\\s*" + selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "\\s*(,[^{]*)?\\{([^}]*)\\}");
    const m = BLOCK.match(re);
    expect(m, `no rule for ${selector}`).not.toBeNull();
    return m[3];
  };

  it("is the last word on the modal and is scoped to the compact form", () => {
    expect(BLOCK.length).toBeGreaterThan(100);
    for (const line of BLOCK.split("\n")) {
      const selector = line.match(/^\s*([^@{}][^{]*)\{/);
      if (!selector) continue;
      expect(selector[1], selector[1]).toMatch(/cand-modal--compact|cand-resume-|cand-proof-actions|^\s*$/);
    }
  });

  it("lays the fields out two to a row", () => {
    expect(rule(".cand-modal--compact .cand-panel")).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\) minmax\(0, 1fr\)/);
  });

  it("makes each proof a row: thumbnail, details, actions; delete always visible", () => {
    expect(rule(".cand-modal--compact .cand-panel .cand-proofs .cand-proof-card")).toMatch(/grid-template-columns:\s*44px minmax\(0, 1fr\) auto/);
    expect(rule(".cand-modal--compact .cand-panel .cand-proofs .cand-proof-thumb")).toMatch(/height:\s*44px/);
    const del = rule(".cand-modal--compact .cand-panel .cand-proofs .cand-proof-delete");
    expect(del).toMatch(/position:\s*static/);
    expect(del).toMatch(/opacity:\s*1/);
  });

  it("only raises the height on wide screens: a phone keeps the whole viewport", () => {
    const heights = BLOCK.match(/max-height:\s*9\d[dv]?vh/g) || [];
    expect(heights.length).toBeGreaterThan(0);
    const desktop = BLOCK.slice(BLOCK.indexOf("@media (min-width: 900px)"));
    expect(desktop.slice(0, desktop.indexOf("}\n}") + 3)).toMatch(/max-height:\s*94dvh/);
    expect(BLOCK.slice(0, BLOCK.indexOf("@media (min-width: 900px)"))).not.toMatch(/max-height:\s*9\d/);
  });
});
