/**
 * Add Referrer Expense: what the screen says after a save.
 *
 * The exact case: Thrilok owes ₹42,500, the operator files a ₹5,000 expense.
 * Afterwards the message must say ₹5,000 was deducted (never the balance),
 * "Currently owed" must read ₹37,500, and the history must show the new
 * expense with its count and total.
 *
 * The server counts a month's owed from payouts dated in that month or
 * earlier. The modal opens on the month the Earnings page was showing, so an
 * expense dated in another month (today, while the page showed September)
 * changed nothing the modal displayed: the owed figure and the history looked
 * untouched, the save looked like it had not happened, and it was filed again.
 * The modal now moves to the period the expense belongs to, and everything it
 * shows -- message, owed, history -- comes from the same fresh data.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PayoutModal from "./PayoutModal.jsx";

class FakeEventSource {
  static instances = [];
  constructor(url) { this.url = url; FakeEventSource.instances.push(this); }
  close() {}
}

const inr = (value) => `₹${Number(value).toLocaleString("en-IN")}`;
const json = (body) => Promise.resolve({ ok: true, json: () => Promise.resolve(body) });

// What the next screenshot shows: the amount is read off it, never typed in.
const receipt = { amount: 0 };

// Earned before any payout. September's two payouts leave ₹42,500 owed.
const GROSS = 57500;
const SEPT = [
  { id: "e-1", reference: "Thrilok", amount: 5000, category: "commission", note: "need", date: "2026-09-11", proofs: [] },
  { id: "e-2", reference: "Thrilok", amount: 10000, category: "commission", note: "need for Docs", date: "2026-09-15", proofs: [] },
];

/** The modal's API, following the server's rule for a month's balance. */
function stubServer({ savedAmount } = {}) {
  const store = SEPT.map((row) => ({ ...row }));
  const server = { store, posts: [] };
  const closing = (month) => GROSS - store
    .filter((row) => month === "all" || row.date.slice(0, 7) <= month)
    .reduce((sum, row) => sum + row.amount, 0);
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const value = String(url);
    const month = new URL(value, "http://localhost").searchParams.get("month") || "all";
    if (value.endsWith("/referrers")) {
      return json({ status: "ok", referrers: [{ id: "referrer-thrilok", name: "Thrilok", aliases: [] }] });
    }
    if (value.includes("/candidates/stats?")) {
      return json({ status: "ok", stats: { top_performers: [{ name: "Thrilok", net_payable: closing(month) }] } });
    }
    if (options.method === "POST" && value.endsWith("/handler-expenses/extract")) {
      return json({ status: "ok", amount: receipt.amount });
    }
    if (options.method === "POST") {
      const form = options.body;
      const row = {
        id: `e-${store.length + 1}`, reference: form.get("reference"), amount: Number(form.get("amount")),
        category: form.get("category"), note: form.get("note"), date: form.get("date"), proofs: [],
      };
      store.push(row);
      server.posts.push(row);
      return json({ status: "ok", expense: savedAmount ? { ...row, amount: savedAmount } : row });
    }
    if (options.method === "PATCH") {
      const id = value.split("/").pop();
      const row = store.find((entry) => entry.id === id);
      Object.assign(row, JSON.parse(options.body), { amount: Number(JSON.parse(options.body).amount) });
      return json({ status: "ok", expense: row });
    }
    return json({
      status: "ok", available_months: [],
      expenses: store.filter((row) => month === "all" || row.date.startsWith(month)),
    });
  }));
  return server;
}

async function openModal(initialMonth = "2026-09") {
  render(
    <PayoutModal
      handlerNames={["Thrilok"]}
      ownedSummary={{}}
      initialMonth={initialMonth}
      onClose={() => {}}
      onChanged={() => {}}
      apiBase=""
      categories={[]}
      categoryLabels={{}}
      formatCurrency={inr}
      formatDate={(value) => value}
    />,
  );
  await waitFor(() => expect(screen.getByRole("option", { name: "Thrilok" })).toBeInTheDocument());
  fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), { target: { value: "referrer-thrilok" } });
  await waitFor(() => expect(owed()).toBe(inr(42500)));
}

const owed = () => document.querySelector(".payout-modal__summary")?.textContent.match(/Currently owed:\s*(.*)$/)?.[1];
const history = () => ({
  count: document.querySelector(".payout-modal__history-summary span")?.textContent,
  total: document.querySelector(".payout-modal__history-summary strong")?.textContent,
  amounts: [...document.querySelectorAll(".payout-modal__table .payout-col--amount-positive")].map((cell) => cell.textContent),
});
const banner = () => document.querySelector(".payout-modal__success")?.textContent;

async function fileExpense({ amount, date }) {
  receipt.amount = amount;
  if (date) fireEvent.change(screen.getByLabelText("Expense date *"), { target: { value: date } });
  fireEvent.change(document.querySelector('input[type="file"]'), {
    target: { files: [new File(["receipt"], "receipt.png", { type: "image/png" })] },
  });
  // The amount is read off the screenshot; saving waits for it.
  await waitFor(() => expect(screen.getByLabelText("Expense amount (₹) *").value).toBe(String(amount)));
  fireEvent.click(screen.getByRole("button", { name: "Save expense" }));
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  window.__TA_CONFIRM_VALUE__ = { confirm: vi.fn().mockResolvedValue(true) };
  // These cases happen in September, the month the modal opens on: pin "today" so
  // that month is the current one whatever the real date is.
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-09-25T10:00:00"));
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete window.__TA_CONFIRM_VALUE__;
});

describe("the starting point", () => {
  it("owes ₹42,500 with two September expenses totalling ₹15,000", async () => {
    stubServer();
    await openModal();
    expect(owed()).toBe("₹42,500");
    await waitFor(() => expect(history().count).toBe("2 entries"));
    expect(history().total).toBe("Total expenses: ₹15,000");
  });
});

describe("₹42,500 owed, a ₹5,000 expense in the same month", () => {
  it("says ₹5,000 was deducted -- never ₹42,500", async () => {
    stubServer();
    await openModal();
    await fileExpense({ amount: 5000, date: "2026-09-20" });
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(banner()).toContain(`${inr(5000)} was deducted from the amount owed`);
    expect(banner()).not.toContain("42,500");
  });

  it("shows ₹37,500 owed, in the header and in the message", async () => {
    stubServer();
    await openModal();
    await fileExpense({ amount: 5000, date: "2026-09-20" });
    await waitFor(() => expect(owed()).toBe("₹37,500"));
    await waitFor(() => expect(banner()).toContain("Currently owed: ₹37,500"));
  });

  it("shows the new expense, with its count and total, straight away", async () => {
    stubServer();
    await openModal();
    await fileExpense({ amount: 5000, date: "2026-09-20" });
    await waitFor(() => expect(history().count).toBe("3 entries"));
    expect(history().total).toBe("Total expenses: ₹20,000");
    expect(history().amounts).toHaveLength(3);
    expect(history().amounts).toContain("₹5,000");
  });
});

describe("a ₹5,000 expense dated in another month than the one the modal opened on", () => {
  it("moves to that month, so the owed figure and history show the save", async () => {
    const server = stubServer();
    await openModal("2026-09");
    await fileExpense({ amount: 5000, date: "2026-10-06" });
    await waitFor(() => expect(server.posts).toHaveLength(1));
    // October's balance: ₹57,500 earned less every payout to date, ₹20,000.
    await waitFor(() => expect(owed()).toBe("₹37,500"));
    await waitFor(() => expect(history().count).toBe("1 entry"));
    expect(history().total).toBe("Total expenses: ₹5,000");
    expect(history().amounts).toEqual(["₹5,000"]);
    expect(screen.getByRole("combobox", { name: "Filter expense history by month" }).value).toBe("2026-10");
  });

  it("says so, and still says ₹5,000 -- not the balance", async () => {
    stubServer();
    await openModal("2026-09");
    await fileExpense({ amount: 5000, date: "2026-10-06" });
    await waitFor(() => expect(banner()).toContain("Currently owed: ₹37,500"));
    expect(banner()).toContain(`${inr(5000)} was deducted from the amount owed`);
    expect(banner()).toContain("Oct 2026");
    expect(banner()).not.toContain("42,500");
  });

  it("does not move when the expense is in the month already shown", async () => {
    stubServer();
    await openModal("2026-09");
    await fileExpense({ amount: 5000, date: "2026-09-20" });
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(banner()).not.toMatch(/now show/);
    expect(screen.getByRole("combobox", { name: "Filter expense history by month" }).value).toBe("2026-09");
  });
});

describe("what was saved is what is reported", () => {
  it("takes the amount from the server's answer, and says if it differs from what was confirmed", async () => {
    stubServer({ savedAmount: 4000 });
    await openModal();
    await fileExpense({ amount: 5000, date: "2026-09-20" });
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(banner()).toContain(`${inr(4000)} was deducted from the amount owed`);
    expect(banner()).toContain(`Saved as ${inr(4000)}, not the ${inr(5000)} confirmed`);
  });

  it("checks the next expense against the balance as it now stands", async () => {
    stubServer();
    await openModal();
    await fileExpense({ amount: 5000, date: "2026-09-20" });
    await waitFor(() => expect(owed()).toBe("₹37,500"));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save expense" })).toBeDisabled());
    receipt.amount = 40000;
    fireEvent.change(document.querySelector('input[type="file"]'), {
      target: { files: [new File(["receipt"], "receipt.png", { type: "image/png" })] },
    });
    await waitFor(() => expect(screen.getByLabelText("Expense amount (₹) *").value).toBe("40000"));
    fireEvent.submit(document.querySelector("form.payout-modal__form-section"));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      `Expense amount cannot exceed the current outstanding amount of ${inr(37500)}.`,
    );
  });
});

describe("editing an expense", () => {
  it("reports the new amount, not a deduction of it, and recalculates the owed figure", async () => {
    stubServer();
    await openModal();
    await waitFor(() => expect(history().amounts).toContain("₹5,000"));
    const row = screen.getByText("₹5,000", { selector: ".payout-col--amount" }).closest("tr");
    fireEvent.click(within(row).getByTitle("Edit"));
    fireEvent.change(screen.getByLabelText("Expense amount (₹) *"), { target: { value: "6000" } });
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(banner()).toBeTruthy());
    expect(banner()).toContain(`Expense updated. Now ${inr(6000)} (was ${inr(5000)}).`);
    expect(banner()).not.toContain("deducted");
    await waitFor(() => expect(owed()).toBe("₹41,500"));
    expect(banner()).toContain("Currently owed: ₹41,500");
  });
});
