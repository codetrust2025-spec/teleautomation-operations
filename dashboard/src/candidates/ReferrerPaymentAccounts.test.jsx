import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";

import PayoutModal from "./PayoutModal.jsx";
import ReferrerPaymentAccounts from "./ReferrerPaymentAccounts.jsx";
import EarningsBreakdown from "./EarningsBreakdown.jsx";


function response(body) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  });
}


afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  // restoreAllMocks does not undo vi.stubGlobal — unstub so the fetch stub
  // never leaks into other test files sharing the worker.
  vi.unstubAllGlobals();
  // Runs even when a test fails, so a pinned clock can never leak.
  vi.useRealTimers();
});


describe("referrer payment accounts", () => {
  it("falls back to the production /api registry route when the root route serves HTML", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url) === "https://teleautomation.online/referrers") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.reject(new SyntaxError("Unexpected token '<'")),
        });
      }
      if (String(url) === "https://teleautomation.online/api/referrers") {
        return response({
          status: "ok",
          referrers: [{
            id: "referrer-pavan-kalyan",
            name: "Sample Referrer",
            aliases: [],
          }],
        });
      }
      return response({ status: "ok", accounts: [] });
    }));

    render(
      <ReferrerPaymentAccounts
        apiBase="https://teleautomation.online"
        referrerName="Sample Referrer"
      />,
    );

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "https://teleautomation.online/api/referrers",
      { credentials: "include" },
    ));
    expect(screen.queryByText(
      "This name is not linked to the current referrer registry.",
    )).not.toBeInTheDocument();
  });

  it("loads the existing referrer by ID and displays only a masked identifier", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).endsWith("/referrers")) {
        return response({
          status: "ok",
          referrers: [{
            id: "referrer-pavan-kalyan",
            name: "Sample Referrer",
            aliases: ["Sample Referrer", "SAMPLE REFERRER"],
          }],
        });
      }
      return response({
        status: "ok",
        accounts: [{
          id: "receiver-pavan",
          referrer_id: "referrer-pavan-kalyan",
          account_holder_name: "SAMPLE REFERRER",
          masked_upi_id: "pa***********@okaxis",
          provider_name: "UPI",
          verification_status: "VERIFIED",
          is_active: true,
          created_at: "2026-07-27T00:00:00Z",
          verified_at: "2026-07-27T00:00:00Z",
          history: [],
        }],
      });
    }));

    render(
      <ReferrerPaymentAccounts
        apiBase="/api"
        referrerName="Sample Referrer"
      />,
    );

    expect(await screen.findByText("SAMPLE REFERRER")).toBeInTheDocument();
    expect(screen.getByText(/pa\*+@okaxis/)).toBeInTheDocument();
    expect(screen.queryByText("referrer@upi")).not.toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "/api/referrers/referrer-pavan-kalyan/payment-accounts",
      { credentials: "include" },
    );
  });

  it("opens in a focused expense view and keeps payment accounts hidden", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).endsWith("/referrers")) {
        return response({
          status: "ok",
          referrers: [{
            id: "referrer-pavan-kalyan",
            name: "Sample Referrer",
            aliases: ["Sample Referrer"],
          }],
        });
      }
      if (String(url).includes("/candidates/stats?")) {
        return response({
          status: "ok",
          stats: {
            top_performers: [{
              name: "Sample Referrer",
              net_payable: 4500,
            }],
          },
        });
      }
      return response({
        status: "ok",
        expenses: [],
        available_months: [],
      });
    }));

    render(
      <PayoutModal
        handlerNames={["Sample Referrer"]}
        topPerformers={[]}
        ownedSummary={{}}
        onClose={() => {}}
        onChanged={() => {}}
        apiBase="/api"
        categories={[]}
        categoryLabels={{}}
        formatCurrency={(value) => String(value)}
        formatDate={(value) => String(value)}
      />,
    );

    expect(screen.getByRole("heading", { name: "Add Referrer Expense" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("option", {
      name: "Sample Referrer",
    })).toHaveValue("referrer-pavan-kalyan"));
    expect(screen.getByRole("combobox", { name: "Referrer *" })).toHaveValue("all");
    expect(screen.getByLabelText("Expense amount (₹) *")).toBeDisabled();
    expect(screen.getByText("Select a referrer to view recent expenses.")).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), {
      target: { value: "referrer-pavan-kalyan" },
    });
    await waitFor(() => expect(screen.getByText("4500")).toBeInTheDocument());
    expect(screen.queryByText("Payment Accounts")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Period")).not.toBeInTheDocument();
    expect(screen.queryByText(/Commission \(50%\)/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save expense" })).toBeDisabled();
    expect(screen.getByRole("heading", { name: "Recent expense history" })).toBeInTheDocument();
  });

  it("uses the launching earnings month for salary-inclusive payout validation", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      const value = String(url);
      if (value.endsWith("/referrers")) {
        return response({
          status: "ok",
          referrers: [{ id: "referrer-thrilok", name: "Thrilok", aliases: [] }],
        });
      }
      if (value.includes("/candidates/stats?")) {
        return response({
          status: "ok",
          stats: {
            top_performers: [{
              name: "Thrilok",
              commission_total: 27000,
              salary_total: 15000,
              auto_earnings_total: 42000,
              net_payable: 42000,
            }],
          },
        });
      }
      return response({ status: "ok", expenses: [], available_months: [] });
    }));

    render(
      <PayoutModal
        handlerNames={["Thrilok"]}
        ownedSummary={{}}
        initialMonth="2026-07"
        onClose={() => {}}
        apiBase="/api"
        categories={[]}
        categoryLabels={{}}
        formatCurrency={(value) => `₹${Number(value).toLocaleString("en-IN")}`}
        formatDate={(value) => value}
      />,
    );

    await waitFor(() => expect(screen.getByRole("option", { name: "Thrilok" })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), {
      target: { value: "referrer-thrilok" },
    });

    await waitFor(() => expect(screen.getByText("₹42,000")).toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(
      "/api/candidates/stats?month=2026-07&reference=Thrilok",
      { credentials: "include" },
    );
    expect(screen.getByLabelText("Expense amount (₹) *")).toHaveAttribute("max", "42000");
    expect(screen.getByRole("combobox", { name: "Filter expense history by month" })).toHaveValue("2026-07");
  });

  it("confirms and saves an expense through the existing handler-expenses API", async () => {
    const confirm = vi.fn().mockResolvedValue(true);
    window.__TA_CONFIRM_VALUE__ = { confirm };
    let submittedBody;
    vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
      const value = String(url);
      if (value.endsWith("/referrers")) {
        return response({
          status: "ok",
          referrers: [{
            id: "referrer-pavan-kalyan",
            name: "Sample Referrer",
            aliases: [],
          }],
        });
      }
      if (value.includes("/candidates/stats?")) {
        return response({
          status: "ok",
          stats: {
            top_performers: [{ name: "Sample Referrer", net_payable: 4500 }],
          },
        });
      }
      if (value.endsWith("/handler-expenses") && options.method === "POST") {
        submittedBody = options.body;
        return response({ status: "ok" });
      }
      return response({ status: "ok", expenses: [], available_months: [] });
    }));

    render(
      <PayoutModal
        handlerNames={["Sample Referrer"]}
        ownedSummary={{}}
        onClose={() => {}}
        onChanged={() => {}}
        apiBase="/api"
        categories={[]}
        categoryLabels={{}}
        formatCurrency={(value) => `₹${value}`}
        formatDate={(value) => value}
      />,
    );

    await waitFor(() => expect(screen.getByRole("option", {
      name: "Sample Referrer",
    })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), {
      target: { value: "referrer-pavan-kalyan" },
    });
    await waitFor(() => expect(screen.getByText("₹4500")).toBeInTheDocument());
    fireEvent.change(screen.getByLabelText("Expense amount (₹) *"), {
      target: { value: "1000" },
    });
    fireEvent.change(screen.getByLabelText("Note / reason"), {
      target: { value: "Interview expense" },
    });
    const proof = new File(["proof"], "5k payment screenshot.jpg", { type: "image/png" });
    fireEvent.change(document.querySelector('input[type="file"]'), {
      target: { files: [proof] },
    });
    // The attachment is described, never named -- not in the label, not in the
    // hover title, not in any accessible name.
    expect(screen.getByText("Screenshot attached")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("5k payment screenshot");
    expect([...document.querySelectorAll("[title], [aria-label], [alt]")].some((el) =>
      ["title", "aria-label", "alt"].some((attr) => (el.getAttribute(attr) || "").includes("5k payment")),
    )).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Save expense" }));

    await waitFor(() => expect(confirm).toHaveBeenCalledWith(expect.objectContaining({
      message: "₹1000 will be deducted from Sample Referrer’s outstanding amount. Continue?",
    })));
    await waitFor(() => expect(submittedBody).toBeInstanceOf(FormData));
    expect(submittedBody.get("reference")).toBe("Sample Referrer");
    expect(submittedBody.get("amount")).toBe("1000");
    expect(submittedBody.get("note")).toBe("Interview expense");
    expect(screen.getByText(
      "Expense added successfully. ₹1000 was deducted from the amount owed.",
    )).toBeInTheDocument();
    delete window.__TA_CONFIRM_VALUE__;
  });

  it("shows one global Add expense action and no row payout actions", () => {
    const onAddExpense = vi.fn();
    render(
      <EarningsBreakdown
        stats={{
          top_performers: [
            { name: "Venugopal", count: 2, net_payable: 10000 },
            { name: "Thrilok", count: 2, net_payable: -4000 },
            { name: "Sample Referrer", count: 1, net_payable: -1500 },
          ],
        }}
        month="2026-07"
        monthOptions={[{ value: "2026-07", label: "Jul 2026" }]}
        onMonthChange={() => {}}
        onAddExpense={onAddExpense}
        formatCurrency={(value) => `₹${value}`}
      />,
    );

    expect(screen.getAllByRole("button", { name: "Add expense" })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Edit payouts" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Add expense" }));
    expect(onAddExpense).toHaveBeenCalledTimes(1);
  });

  it("reconciles the expanded handler summary with the parent handler totals", async () => {
    const candidates = Array.from({ length: 5 }, (_, index) => ({
      id: `candidate-${index}`,
      name: `Candidate ${index + 1}`,
      payment: 20000,
      handler_commission: 1000,
      date: "2026-07-20",
      payment_proofs: [],
    }));
    vi.stubGlobal("fetch", vi.fn(() => response({
      status: "ok",
      candidates,
    })));

    const props = {
      monthOptions: [
        { value: "2026-07", label: "Jul 2026" },
        { value: "2026-08", label: "Aug 2026" },
      ],
      onMonthChange: () => {},
      formatCurrency: (value) => `₹${Number(value).toLocaleString("en-IN")}`,
      apiBase: "/api",
    };
    const { container, rerender } = render(
      <EarningsBreakdown
        stats={{
          top_performers: [{
            name: "Sample Referrer",
            count: 5,
            commission_total: 36000,
            auto_earnings_total: 36000,
            paid_out_total: 29000,
            net_payable: 7000,
            prior_balance: 0,
          }],
        }}
        month="2026-07"
        {...props}
      />,
    );

    fireEvent.click(screen.getByText("Sample Referrer"));
    await waitFor(() => expect(container.querySelector(".earn-breakdown-total")).toBeTruthy());

    const summary = within(container.querySelector(".earn-breakdown-total"));
    expect(summary.getByText("Total (5 candidates)")).toBeInTheDocument();
    // The money now reads as one vertical calculation below the candidate list.
    // Read each amount from its own row — with no salary, referral earnings and
    // the "Total earned" subtotal are the same figure.
    const ledger = within(container.querySelector(".earn-ledger"));
    const amountFor = (label) =>
      ledger.getByText(label).closest(".earn-ledger-row")
        .querySelector(".earn-ledger-value").textContent;

    expect(amountFor("Referral earnings")).toBe("₹36,000");
    expect(amountFor("Total earned")).toBe("₹36,000");
    expect(amountFor("Paid out")).toBe("−₹29,000");
    expect(amountFor(/^Closing balance/)).toContain("+₹7,000");
    // "Owe" never said who owed whom; the status now states the direction.
    expect(ledger.getByText("To pay")).toBeInTheDocument();
    expect(ledger.getByText(/^Company needs to pay/)).toBeInTheDocument();
    expect(summary.queryByText("₹5,000")).not.toBeInTheDocument();

    rerender(
      <EarningsBreakdown
        stats={{
          top_performers: [{
            name: "Sample Referrer",
            count: 5,
            commission_total: 36000,
            auto_earnings_total: 36000,
            paid_out_total: 29000,
            net_payable: 14000,
            prior_balance: 7000,
          }],
        }}
        month="2026-08"
        {...props}
      />,
    );

    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/candidates?month=2026-08&reference=Sample+Referrer",
      { credentials: "include" },
    ));
    const refreshedLedger = within(container.querySelector(".earn-ledger"));
    expect(refreshedLedger.getByText("Opening balance")).toBeInTheDocument();
    expect(refreshedLedger.getByText("+₹7,000")).toBeInTheDocument();
    expect(refreshedLedger.getByText(/^Closing balance/)).toBeInTheDocument();
    expect(refreshedLedger.getByText("+₹14,000")).toBeInTheDocument();
  });

  it("confirms before clearing unsaved fields when switching referrers", async () => {
    const confirm = vi.fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);
    window.__TA_CONFIRM_VALUE__ = { confirm };
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).endsWith("/referrers")) {
        return response({
          status: "ok",
          referrers: [
            { id: "referrer-pavan", name: "Sample Referrer" },
            { id: "referrer-thrilok", name: "Thrilok" },
          ],
        });
      }
      if (String(url).includes("/candidates/stats?")) {
        const name = String(url).includes("Thrilok") ? "Thrilok" : "Sample Referrer";
        return response({
          status: "ok",
          stats: { top_performers: [{ name, net_payable: 5000 }] },
        });
      }
      return response({ status: "ok", expenses: [], available_months: [] });
    }));

    render(
      <PayoutModal
        handlerNames={["Sample Referrer", "Thrilok"]}
        ownedSummary={{}}
        onClose={() => {}}
        apiBase="/api"
        categories={[]}
        categoryLabels={{}}
        formatCurrency={(value) => `₹${value}`}
        formatDate={(value) => value}
      />,
    );

    const referrer = screen.getByRole("combobox", { name: "Referrer *" });
    await waitFor(() => expect(screen.getByRole("option", {
      name: "Sample Referrer",
    })).toBeInTheDocument());
    fireEvent.change(referrer, { target: { value: "referrer-pavan" } });
    await waitFor(() => expect(screen.getByLabelText("Expense amount (₹) *")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("Expense amount (₹) *"), {
      target: { value: "1000" },
    });

    fireEvent.change(referrer, { target: { value: "referrer-thrilok" } });
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(referrer).toHaveValue("referrer-pavan");
    expect(screen.getByLabelText("Expense amount (₹) *")).toHaveValue(1000);

    fireEvent.change(referrer, { target: { value: "referrer-thrilok" } });
    await waitFor(() => expect(referrer).toHaveValue("referrer-thrilok"));
    expect(screen.getByLabelText("Expense amount (₹) *")).toHaveValue(null);
    delete window.__TA_CONFIRM_VALUE__;
  });

  it("filters recent expense history by month and totals all matching records", async () => {
    // PayoutModal seeds its history filter from the real clock, so this test
    // only sees the July fixtures while the machine is actually in July 2026.
    // Pin the instant instead. Only Date is faked — setTimeout stays real so
    // Testing Library's async queries still resolve. afterEach restores it.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-07-15T12:00:00+05:30"));
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).endsWith("/referrers")) {
        return response({
          status: "ok",
          referrers: [{ id: "referrer-pavan", name: "Sample Referrer" }],
        });
      }
      if (String(url).includes("/candidates/stats?")) {
        return response({
          status: "ok",
          stats: {
            top_performers: [{ name: "Sample Referrer", net_payable: 10000 }],
          },
        });
      }
      return response({
        status: "ok",
        expenses: [
          {
            id: "expense-jul",
            reference: "Sample Referrer",
            amount: 1000,
            date: "2026-07-28",
            note: "July expense",
            proofs: [],
          },
          {
            id: "expense-jun",
            reference: "Sample Referrer",
            amount: 500,
            date: "2026-06-30",
            note: "June expense",
            proofs: [],
          },
          {
            id: "expense-invalid",
            reference: "Sample Referrer",
            amount: 900,
            date: "2026-07-27",
            note: "Invalid expense",
            status: "invalid",
            proofs: [],
          },
        ],
        available_months: [
          { value: "2026-07", label: "Jul 2026", is_current: true },
          { value: "2026-06", label: "Jun 2026", is_current: false },
        ],
      });
    }));

    render(
      <PayoutModal
        handlerNames={["Sample Referrer"]}
        ownedSummary={{}}
        onClose={() => {}}
        apiBase="/api"
        categories={[]}
        categoryLabels={{}}
        formatCurrency={(value) => `₹${Number(value).toLocaleString("en-IN")}`}
        formatDate={(value) => value}
      />,
    );

    await waitFor(() => expect(screen.getByRole("option", {
      name: "Sample Referrer",
    })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), {
      target: { value: "referrer-pavan" },
    });
    expect(await screen.findByText("July expense")).toBeInTheDocument();
    expect(screen.queryByText("June expense")).not.toBeInTheDocument();
    expect(screen.queryByText("Invalid expense")).not.toBeInTheDocument();
    expect(screen.getByText("1 entry")).toBeInTheDocument();
    expect(screen.getByText("Total expenses: ₹1,000")).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox", {
      name: "Filter expense history by month",
    }), {
      target: { value: "all" },
    });

    expect(screen.getByText("July expense")).toBeInTheDocument();
    expect(screen.getByText("June expense")).toBeInTheDocument();
    expect(screen.getByText("2 entries")).toBeInTheDocument();
    expect(screen.getByText("Total expenses: ₹1,500")).toBeInTheDocument();
  });

  it("offers the entered expense month even when it has no history yet", async () => {
    // Filing the first expense of a month was impossible: the History month
    // dropdown was built only from the current month and months that already
    // had rows, so a fresh month never appeared as an option.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-08-01T12:00:00+05:30"));
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).endsWith("/referrers")) {
        return response({ status: "ok", referrers: [{ id: "referrer-thrilok", name: "Thrilok" }] });
      }
      if (String(url).includes("/candidates/stats?")) {
        return response({ status: "ok", stats: { top_performers: [{ name: "Thrilok", net_payable: 32000 }] } });
      }
      return response({ status: "ok", expenses: [], available_months: [] });
    }));

    render(
      <PayoutModal
        handlerNames={["Thrilok"]}
        ownedSummary={{}}
        onClose={() => {}}
        apiBase="/api"
        categories={[]}
        categoryLabels={{}}
        formatCurrency={(value) => `₹${Number(value).toLocaleString("en-IN")}`}
        formatDate={(value) => value}
      />,
    );

    await waitFor(() => expect(screen.getByRole("option", { name: "Thrilok" })).toBeInTheDocument());
    fireEvent.change(screen.getByRole("combobox", { name: "Referrer *" }), {
      target: { value: "referrer-thrilok" },
    });

    const monthSelect = screen.getByRole("combobox", { name: "Filter expense history by month" });
    // July is absent before a July date is entered...
    expect(within(monthSelect).queryByRole("option", { name: "Jul 2026" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Expense date/i), { target: { value: "2026-07-31" } });

    // ...and becomes selectable once the expense is dated in July.
    expect(within(monthSelect).getByRole("option", { name: "Jul 2026" })).toBeInTheDocument();
    fireEvent.change(monthSelect, { target: { value: "2026-07" } });
    expect(monthSelect).toHaveValue("2026-07");
  });
});
