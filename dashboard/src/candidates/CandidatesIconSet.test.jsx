/**
 * One icon style: the Candidates toolbar, row actions, payment-proof, resume
 * and phone chips, and the Earnings proof button draw from the shared line
 * icon set (components/ui/Icon) that the sidebar uses, not from emoji whose
 * size and colour depend on the font.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ICON_NAMES } from "../components/ui/Icon.jsx";

vi.mock("../context/ConfirmContext.jsx", () => ({ useConfirm: () => ({ confirm: vi.fn() }) }));
vi.mock("../context/AuthContext.jsx", () => ({ useAuth: () => ({ role: "admin", reference: "", enabled: false }) }));
vi.mock("../dailyOps/PendingWorksProvider.jsx", () => ({ consumePendingWorkOpenIntent: () => null }));

import { CandidatesPanel } from "./candidatesModule.jsx";

const HERE = dirname(fileURLToPath(import.meta.url));
const MODULE = readFileSync(join(HERE, "candidatesModule.jsx"), "utf8");
const EARNINGS = readFileSync(join(HERE, "EarningsBreakdown.jsx"), "utf8");

const ROW = {
  id: "r1", name: "Asha Rao", phone: "9000000001", stage: "in_progress", service_type: "profile_service",
  technology: "Java", reference: "Karthik", date: "2026-09-10", logged_date: "2026-09-10",
  expected_payment: 20000, payment: 20000, payment_status: "paid", resume_count: 1, proof_count: 1,
  payment_proofs: [{ id: "p1", attachment_type: "payment_proof", url: "/p1", verification_state: "VERIFIED_COMPANY_PAYMENT", verified_amount: 20000 }],
};

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({
    ok: true, json: () => Promise.resolve({ status: "ok", stats: { pending_count: 0, top_performers: [], references: [] }, candidates: [ROW] }),
  })));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the Candidates page", () => {
  it("draws its toolbar and row controls from the shared icon set", async () => {
    render(<CandidatesPanel />);
    await screen.findByText("Asha Rao");
    const toolbar = document.querySelector(".cand-toolbar");
    for (const [label, icon] of [["Active list", "list"], ["Download active CSV", "download"], ["Total expenditure", "chart"]]) {
      const button = within(toolbar).getByText(label, { exact: false }).closest("button");
      expect(button.querySelector(`svg.ta-icon--${icon}`)).not.toBeNull();
    }
    const row = document.querySelector('tr[data-cid="r1"]');
    expect(within(row).getByRole("button", { name: "Edit Asha Rao" }).querySelector("svg.ta-icon--pencil")).not.toBeNull();
    expect(within(row).getByRole("button", { name: "Delete Asha Rao" }).querySelector("svg.ta-icon--trash")).not.toBeNull();
    expect(row.querySelector(".cand-pay-proofs svg.ta-icon--paperclip")).not.toBeNull();
    expect(row.querySelector(".cand-resume-chip svg.ta-icon--file")).not.toBeNull();
    expect(row.querySelector(".cand-phone-icon svg.ta-icon--phone")).not.toBeNull();
  });

  it("no longer uses emoji for those controls", () => {
    for (const glyph of ["☷ Active list", "⇩ Download active CSV", "📊 Total expenditure", "<span aria-hidden={true}>📎</span>"]) {
      expect(MODULE).not.toContain(glyph);
    }
    expect(EARNINGS).not.toContain("📷");
    for (const name of ["pencil", "trash", "list", "download", "chart", "paperclip", "file", "camera", "phone", "copy", "check", "plus"]) {
      expect(ICON_NAMES).toContain(name);
    }
  });
});
