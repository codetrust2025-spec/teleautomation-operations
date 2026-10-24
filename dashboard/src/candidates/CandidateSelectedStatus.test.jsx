/**
 * ★ Selected on the Candidates page, Overview and Earnings.
 *
 * A profile-wise candidate whose stage is Closed / Completed reads Selected:
 * a green ★ Selected badge beside the name, Selected in the stage column and
 * in the editor's stage list, a Selected filter, and the Overview and Earnings
 * counts naming how many. A round-wise candidate who completed keeps Closed /
 * Completed, and nothing stored changes (the stage stays `completed`).
 *
 * The server decides (`candidate_selected` on each row, see
 * tests/test_candidate_selected_status.py); the page reads that field, and the
 * rule itself only for a row that predates it. Names are invented.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../context/ConfirmContext.jsx", () => ({ useConfirm: () => ({ confirm: vi.fn(async () => true) }) }));
vi.mock("../context/AuthContext.jsx", () => ({
  useAuth: () => ({ role: "admin", reference: "", enabled: false }),
}));
vi.mock("../dailyOps/PendingWorksProvider.jsx", () => ({
  consumePendingWorkOpenIntent: () => null,
}));

import { CandidatesPanel, CandidateEditModal } from "./candidatesModule.jsx";
import EarningsBreakdown from "./EarningsBreakdown.jsx";
import { isSelectedCandidate, stageLabel, candidateStatusLabel } from "./candidateOutcome.js";
import { captureLayout } from "../test/captureLayout.js";

const base = {
  service_type: "profile_service", technology: "Java", reference: "Test Owner",
  date: "2026-09-10", logged_date: "2026-09-10", expected_payment: 20000, payment: 20000,
  payment_status: "paid", payment_proofs: [], proof_count: 0, resume_count: 0, slot_count: 1,
};

const PLACED = { ...base, id: "placed", name: "Asha Fakename", phone: "9000000901", stage: "completed", candidate_selected: true };
const ROUND = { ...base, id: "round", name: "Ravi Testcase", phone: "9000000902", stage: "completed", service_type: "round_wise", candidate_selected: false };
const OPEN = { ...base, id: "open", name: "Meena Sample", phone: "9000000903", stage: "in_progress", candidate_selected: false };
// Served before the field existed: the page applies the rule itself.
const LEGACY = { ...base, id: "legacy", name: "Gopal Fixture", phone: "9000000904", stage: "completed" };
const ROWS = [PLACED, ROUND, OPEN, LEGACY];

const STATS = {
  total: 4, by_stage: { completed: 3, in_progress: 1, fail: 0, dropped: 0 }, selected_count: 2,
  revenue_total: 80000, revenue_completed: 60000, pending_count: 0, top_performers: [], references: [],
};

let calls;
beforeEach(() => {
  calls = [];
  vi.stubGlobal("fetch", vi.fn((url) => {
    calls.push(String(url));
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", stats: STATS, candidates: ROWS }) });
  }));
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const rowOf = (name) => screen.getByText(name, { selector: ".cand-name" }).closest("tr");

describe("the rule", () => {
  it("is a completed profile-wise candidate, and no one else", () => {
    expect(isSelectedCandidate(PLACED)).toBe(true);
    expect(isSelectedCandidate(LEGACY)).toBe(true);
    expect(isSelectedCandidate({ stage: "completed" })).toBe(true);
    expect(isSelectedCandidate(ROUND)).toBe(false);
    expect(isSelectedCandidate(OPEN)).toBe(false);
    expect(isSelectedCandidate({ stage: "fail", service_type: "profile_service" })).toBe(false);
    expect(isSelectedCandidate({ stage: "dropped" })).toBe(false);
    expect(isSelectedCandidate(null)).toBe(false);
  });

  it("follows the server's answer where the row carries one", () => {
    expect(isSelectedCandidate({ stage: "completed", candidate_selected: false })).toBe(false);
  });

  it("names completed Selected for profile-wise and Closed / Completed for round-wise", () => {
    expect(stageLabel("completed", "profile_service")).toBe("Selected");
    expect(stageLabel("completed", "")).toBe("Selected");
    expect(stageLabel("completed", "round_wise")).toBe("Closed / Completed");
    expect(stageLabel("in_progress", "profile_service")).toBe("In progress");
    expect(stageLabel("fail", "round_wise")).toBe("Rejected");
    expect(candidateStatusLabel(ROUND)).toBe("Closed / Completed");
  });
});

describe("the Candidates table", () => {
  it("shows ★ Selected beside a selected candidate's name, and only there", async () => {
    render(<CandidatesPanel />);
    await screen.findByText("Asha Fakename");
    for (const name of ["Asha Fakename", "Gopal Fixture"]) {
      const badge = rowOf(name).querySelector(".cand-cell-name .ta-selected-badge");
      expect(badge, name).toBeTruthy();
      expect(badge.textContent).toBe("★Selected");
    }
    for (const name of ["Ravi Testcase", "Meena Sample"]) {
      expect(rowOf(name).querySelector(".ta-selected-badge"), name).toBeNull();
    }
    captureLayout("candidates-selected-table", document.querySelector(".cand-page"));
  });

  it("reads Selected in the stage column where Closed / Completed applied before", async () => {
    render(<CandidatesPanel />);
    await screen.findByText("Asha Fakename");
    const stage = (name) => rowOf(name).querySelector(".cand-cell-stage").textContent;
    expect(stage("Asha Fakename")).toBe("★Selected");
    expect(stage("Gopal Fixture")).toBe("★Selected");
    expect(stage("Ravi Testcase")).toBe("Closed / Completed");
    expect(stage("Meena Sample")).toBe("In progress");
  });

  it("filters by Selected and by Closed / Completed, through the server", async () => {
    render(<CandidatesPanel />);
    await screen.findByText("Asha Fakename");
    const filter = screen.getByRole("combobox", { name: "Filter by stage" });
    const options = within(filter).getAllByRole("option").map((o) => [o.value, o.textContent]);
    expect(options).toEqual([
      ["all", "All stages"], ["in_progress", "In progress"], ["selected", "Selected"],
      ["closed_completed", "Closed / Completed"], ["fail", "Rejected"], ["dropped", "Dropped"],
    ]);
    fireEvent.change(filter, { target: { value: "selected" } });
    await waitFor(() => expect(calls.some((url) => /\/candidates\?.*stage=selected/.test(url))).toBe(true));
  });
});

describe("the candidate editor", () => {
  const stageSelect = () =>
    [...document.querySelectorAll("form.cand-modal select")].find((el) => [...el.options].some((o) => o.value === "completed"));

  it("calls a profile-wise candidate's completed stage Selected, and stores completed", () => {
    render(<CandidateEditModal initial={PLACED} onClose={vi.fn()} onSave={vi.fn()} isAdmin={true} />);
    expect(document.querySelector("form.cand-modal .cand-modal-title .ta-selected-badge")).toBeTruthy();
    const select = stageSelect();
    expect(select.value).toBe("completed");
    expect([...select.options].map((o) => [o.value, o.textContent])).toEqual([
      ["in_progress", "In progress"], ["completed", "Selected"], ["fail", "Rejected"], ["dropped", "Dropped"],
    ]);
  });

  it("says Closed / Completed for a round-wise candidate, with no badge", () => {
    render(<CandidateEditModal initial={ROUND} onClose={vi.fn()} onSave={vi.fn()} isAdmin={true} />);
    expect(document.querySelector("form.cand-modal .ta-selected-badge")).toBeNull();
    expect([...stageSelect().options].find((o) => o.value === "completed").textContent).toBe("Closed / Completed");
  });
});

describe("the Overview counts", () => {
  it("names the Selected among the completed", async () => {
    render(<CandidatesPanel />);
    await screen.findByText("Asha Fakename");
    await act(async () => { fireEvent.click(screen.getByRole("tab", { name: "Overview" })); });
    const card = await waitFor(() => {
      const found = [...document.querySelectorAll(".cand-stat-card")].find((el) => el.textContent.includes("Total candidates"));
      expect(found).toBeTruthy();
      return found;
    });
    expect(card.querySelector(".cand-stat-selected").textContent).toBe("★ 2 selected");
    expect(card.textContent).toContain("1 closed");
    captureLayout("candidates-selected-overview", document.querySelector(".cand-page"));
  });
});

describe("Earnings", () => {
  it("counts a handler's Selected beside Done, and badges them in the breakdown", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ status: "ok", candidates: [{ ...PLACED, reference: "Thrilok" }, { ...ROUND, reference: "Thrilok" }] }) })));
    render(
      <EarningsBreakdown
        stats={{ top_performers: [{ name: "Thrilok", count: 2, completed: 2, selected: 1, revenue_total: 24000, commission_total: 12000, auto_earnings_total: 12000, paid_out_total: 0, net_payable: 12000 }] }}
        month="all"
        monthOptions={[]}
        formatCurrency={(v) => `₹${v}`}
        apiBase="/api"
      />,
    );
    const row = screen.getByText("Thrilok").closest("tr");
    expect(row.querySelector(".earn-selected").textContent).toBe("★ 1 selected");
    fireEvent.click(row);
    await waitFor(() => expect(document.querySelector(".earn-breakdown-item")).toBeTruthy());
    const items = [...document.querySelectorAll(".earn-breakdown-item")];
    const placed = items.find((el) => el.textContent.includes("Asha Fakename"));
    const round = items.find((el) => el.textContent.includes("Ravi Testcase"));
    expect(placed.querySelector(".ta-selected-badge")).toBeTruthy();
    expect(round.querySelector(".ta-selected-badge")).toBeNull();
  });
});
