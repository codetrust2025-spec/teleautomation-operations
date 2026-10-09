/** A handler expense is history, not a second referrer payment. */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PayoutModal from "./PayoutModal.jsx";

const json = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
const inr = (amount) => `₹${Number(amount).toLocaleString("en-IN")}`;

const augustExpense = {
  id: "aug-expense", reference: "Pavan Kalyan", amount: 36000,
  date: "2026-08-31", category: "commission", note: "", proofs: [],
};
const septemberExpense = {
  id: "sep-expense", reference: "Pavan Kalyan", amount: 8000,
  date: "2026-09-15", category: "commission", note: "", proofs: [],
};
const octoberHandlerExpense = {
  id: "handler-expense-october", reference: "Pavan Kalyan", amount: 24500,
  date: "2026-10-06", category: "commission", note: "",
  proofs: [{ id: "proof-october", url: "/handler-expenses/handler-expense-october/proofs/proof-october" }],
};

function installServer() {
  const rows = [augustExpense, septemberExpense, octoberHandlerExpense];
  const requests = [];
  vi.stubGlobal("fetch", vi.fn((url) => {
    const value = String(url);
    const params = new URL(value, "http://localhost").searchParams;
    requests.push({ path: new URL(value, "http://localhost").pathname, month: params.get("month"), reference: params.get("reference") });
    if (value.endsWith("/referrers")) {
      return json({ status: "ok", referrers: [{ id: "pavan", name: "Pavan Kalyan", aliases: [] }] });
    }
    if (value.includes("/candidates/stats?")) {
      return json({ status: "ok", stats: { top_performers: [{ name: "Pavan Kalyan", net_payable: 0 }] } });
    }
    if (value.includes("/handler-expenses")) {
      const month = params.get("month");
      return json({
        status: "ok",
        expenses: rows.filter((row) => !month || row.date.startsWith(month)),
      });
    }
    return json({ status: "ok" });
  }));
  return requests;
}

function renderModal() {
  render(<PayoutModal
    handlerNames={["Pavan Kalyan"]}
    initialMonth="2026-08"
    onClose={() => {}}
    onChanged={() => {}}
    apiBase=""
    categories={[]}
    categoryLabels={{}}
    formatCurrency={inr}
    formatDate={(date) => date === "2026-10-06" ? "06 Oct 2026" : "31 Aug 2026"}
  />);
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-06T10:00:00Z"));
  installServer();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("referrer expense history", () => {
  it("keeps an existing October handler expense visible once, with proof and classification", async () => {
    const requests = installServer();
    renderModal();
    await waitFor(() => expect(screen.getByRole("option", { name: "Pavan Kalyan" })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), { target: { value: "pavan" } });

    await waitFor(() => expect(screen.getByText("Total expenses: ₹36,000")).toBeInTheDocument());
    const historyMonth = screen.getByRole("combobox", { name: "Filter expense history by month" });
    fireEvent.change(historyMonth, { target: { value: "2026-10" } });

    await waitFor(() => expect(screen.getByText("₹24,500")).toBeInTheDocument());
    expect(screen.getByText("06 Oct 2026")).toBeInTheDocument();
    expect(screen.getByText("Handler Expense")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "View proof" })).toBeInTheDocument();
    expect(screen.getByText("Total expenses: ₹24,500")).toBeInTheDocument();
    const octoberRow = screen.getByText("₹24,500").closest("tr");
    expect(octoberRow.querySelector('[title="Edit"]')).toBeNull();
    expect(octoberRow.querySelector('[title="Delete"]')).toBeNull();
    expect(requests).toContainEqual(expect.objectContaining({ path: "/handler-expenses", reference: "Pavan Kalyan", month: null }));
  });

  it("keeps date filters exact and never doubles the existing record in All months", async () => {
    renderModal();
    await waitFor(() => expect(screen.getByRole("option", { name: "Pavan Kalyan" })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), { target: { value: "pavan" } });
    const historyMonth = screen.getByRole("combobox", { name: "Filter expense history by month" });

    await waitFor(() => expect(screen.getByText("₹36,000")).toBeInTheDocument());
    fireEvent.change(historyMonth, { target: { value: "2026-09" } });
    await waitFor(() => expect(screen.getByText("₹8,000")).toBeInTheDocument());
    expect(screen.queryByText("₹24,500")).toBeNull();

    fireEvent.change(historyMonth, { target: { value: "all" } });
    await waitFor(() => expect(screen.getByText("3 entries")).toBeInTheDocument());
    expect(screen.getByText("Total expenses: ₹68,500")).toBeInTheDocument();
    expect(screen.getAllByText("₹24,500")).toHaveLength(1);
  });
});
