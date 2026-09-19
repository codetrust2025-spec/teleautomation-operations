import React from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MailMonitoringNotifications, MailNotificationBell, mailStatusTone, blockingReason, BOOKING_RESULTS } from "./MailMonitoringNotifications.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";
import BLOCKED from "./__fixtures__/blockedBookingNotification.json";

class FakeWebSocket {
  static OPEN = 1;
  static latest = null;
  readyState = 1;
  constructor() { FakeWebSocket.latest = this; setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

const notification = {
  id: "notification-1", candidate_id: "candidate-1", candidate_name: "Rahul Kumar",
  ai_recruitment_event_id: "event-1", gmail_message_id: "gmail-message-1",
  candidate_email: "rahul@example.com", company_name: "Infosys", job_role: "Software Engineer",
  classification: "offer_received", candidate_status: "Offer Received", priority: "high",
  email_subject: "Formal employment offer", sender_name: "Recruiter", sender_email: "hr@infosys.example",
  ai_confidence: 0.94, ai_summary: "A formal offer was issued.", ai_reason: "Employment terms are confirmed.",
  recommended_action: "Verify the offer with the candidate.", is_read: false, is_reviewed: false,
  email_received_at: "2026-07-15T04:55:00Z",
  created_at: "2026-07-15T05:00:00Z",
  interview_date: "2026-07-23", interview_time: "17:30", interview_timezone: "Asia/Kolkata",
};

function response(body) { return Promise.resolve({ ok: true, json: () => Promise.resolve(body) }); }
function renderNotifications() {
  return render(<ConfirmProvider><MailMonitoringNotifications /></ConfirmProvider>);
}

describe("mail monitoring notifications", () => {
  it("uses semantic colors for booking outcomes", () => {
    expect(mailStatusTone({ candidate_status: "Interview Automatically Booked" })).toBe("success");
    expect(mailStatusTone({ candidate_status: "Automatic Booking Blocked" })).toBe("warning");
    expect(mailStatusTone({ booking_status: "Processing Failed" })).toBe("danger");
    expect(mailStatusTone({ candidate_status: "AI Retry Pending" })).toBe("warning");
    expect(mailStatusTone({ candidate_status: "Already Booked — Duplicate Ignored" })).toBe("success");
    expect(mailStatusTone({ candidate_status: "Historical Interview Skipped" })).toBe("neutral");
    expect(mailStatusTone({ candidate_status: "Historical Interview Skipped" })).toBe("neutral");
  });

  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/config")) return response({ enabled: true });
      if (String(url).includes("/summary")) return response({ summary: { unread: 1, new_offers: 1, selections: 0, joining_confirmations: 0, needs_review: 0 } });
      if (String(url).includes("/api/ai-recruitment/events/event-1")) return response({
        event: {
          received_email: {
            subject: "Formal employment offer",
            sender_name: "Recruiter",
            sender_email: "hr@infosys.example",
            recipient_email: "rahul@example.com",
            sent_at: "2026-07-15T04:55:00Z",
            body: "Dear Rahul,\nWe are pleased to offer you the role.",
          },
        },
      });
      if (String(url).includes("/notifications")) return response({ notifications: [notification], total: 1 });
      return response({ status: "ok" });
    }));
  });
  afterEach(() => {
    cleanup();
    FakeWebSocket.latest = null;
    vi.unstubAllGlobals();
  });

  it("shows the persisted unread count and latest notification", async () => {
    render(<MailNotificationBell />);
    await waitFor(() => expect(screen.getByLabelText("1 unread mail monitoring notifications")).toBeInTheDocument());
    fireEvent.click(screen.getByLabelText("1 unread mail monitoring notifications"));
    expect(await screen.findByText(/Rahul Kumar · Infosys/)).toBeInTheDocument();
    expect(screen.getByText("Offer Received")).toBeInTheDocument();
  });

  it("renders summary and evidence without manual decision actions", async () => {
    renderNotifications();
    expect(await screen.findByRole("heading", { name: "Mail Monitoring Notifications" })).toBeInTheDocument();
    expect(await screen.findByText("Formal employment offer")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Mail received" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Tool detected" })).toBeInTheDocument();
    // The eighteen-classification dropdown was replaced by two groups plus a
    // candidate filter; see MailAlertsFilters.test.jsx for their behaviour.
    expect(screen.getByLabelText("Alert type filter")).toBeInTheDocument();
    expect(screen.getByLabelText("Candidate filter")).toBeInTheDocument();
    expect(screen.queryByLabelText("Candidate status filter")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Priority filter")).not.toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Open email notification: Formal employment offer"));
    expect(await screen.findByText(/We are pleased to offer you the role/)).toBeInTheDocument();
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/mail-monitoring/notifications/notification-1/read",
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByText("23 Jul 2026, 5:30 pm IST")).toBeInTheDocument();
  });

  it("offers only the two actions that live outside this screen", async () => {
    renderNotifications();
    fireEvent.click(await screen.findByLabelText(
      "Open email notification: Formal employment offer"));
    await screen.findByText(/We are pleased to offer you the role/);

    // The panel reports; it does not edit. Every control that wrote from here
    // is gone. Read/unread tracking does not change the interview decision.
    for (const name of [
      "Re-run AI", "Save correction", "Confirm & reviewed", "False detection",
      "View audit history", "View email", "View payment",
      "Start payment follow-up", "View / contact candidate",
    ]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
    // Scoped to the panel: the list's own filter dropdowns are comboboxes too,
    // and they are not part of this change.
    const panel = document.querySelector(".mail-detail");
    expect(panel.querySelector("textarea")).toBeNull();
    expect(panel.querySelector("select")).toBeNull();
    expect(panel.querySelector("input")).toBeNull();
  });

  it("still opens the booking and the meeting from the panel", async () => {
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/config")) return response({ enabled: true });
      if (String(url).includes("/summary")) return response({ summary: { unread: 1 } });
      if (String(url).includes("/api/ai-recruitment/events/event-1")) {
        return response({ event: { received_email: { subject: "Formal employment offer", body: "We are pleased to offer you the role." } } });
      }
      if (String(url).includes("/notifications")) {
        return response({
          notifications: [{
            ...notification,
            booking_id: "booking-1",
            meeting_link: "https://teams.microsoft.com/l/meetup-join/test",
          }],
          total: 1,
        });
      }
      return response({ status: "ok" });
    }));
    renderNotifications();
    fireEvent.click(await screen.findByLabelText(
      "Open email notification: Formal employment offer"));
    await screen.findByText(/We are pleased to offer you the role/);
    const footer = document.querySelector(".mail-detail footer");
    const labels = [...footer.querySelectorAll("button")].map((node) => node.textContent);
    expect(labels).toEqual(["View booking", "Open meeting link"]);
  });

  it("writes nothing when the panel is merely opened and closed", async () => {
    renderNotifications();
    fireEvent.click(await screen.findByLabelText(
      "Open email notification: Formal employment offer"));
    await screen.findByText(/We are pleased to offer you the role/);
    const writes = fetch.mock.calls.filter(
      ([url, options]) => options?.method === "POST"
        && !String(url).endsWith("/read") && !String(url).endsWith("/unread"),
    );
    expect(writes).toEqual([]);
    expect(screen.queryByRole("columnheader", { name: "Review" })).not.toBeInTheDocument();
  });

  it("clears the complete notification list after confirmation", async () => {
    renderNotifications();
    const button = await screen.findByRole("button", { name: "Clear all notifications" });
    fireEvent.click(button);
    expect(await screen.findByText("Clear all mail notifications?")).toBeInTheDocument();
    expect(screen.getByText("Email evidence")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Clear notifications"));
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(
      "/api/mail-monitoring/notifications/clear-all",
      expect.objectContaining({ method: "POST" }),
    ));
  });

  it("does not let an older list response erase a realtime alert", async () => {
    let resolveFirstList;
    let listCalls = 0;
    vi.stubGlobal("fetch", vi.fn((url) => {
      const path = String(url);
      if (path.includes("/config")) return response({ enabled: true });
      if (path.includes("/summary")) return response({ summary: { unread: 1 } });
      if (path.includes("/candidates")) return response({ candidates: [] });
      if (path.includes("/notifications")) {
        listCalls += 1;
        if (listCalls === 1) {
          return new Promise((resolve) => { resolveFirstList = resolve; });
        }
        return response({ notifications: [notification], total: 1 });
      }
      return response({ status: "ok" });
    }));

    renderNotifications();
    await waitFor(() => expect(FakeWebSocket.latest).not.toBeNull());
    await act(async () => {
      FakeWebSocket.latest.onmessage?.({ data: JSON.stringify({
        event: "notification_created", event_id: "realtime-1",
        notification_id: notification.id, classification: notification.classification,
      }) });
    });
    expect(await screen.findByText("Formal employment offer")).toBeInTheDocument();

    await act(async () => {
      resolveFirstList(await response({ notifications: [], total: 0 }));
      await Promise.resolve();
    });
    expect(screen.getByText("Formal employment offer")).toBeInTheDocument();
  });
});

describe("blocked booking reasons", () => {
  // A stored alert and the explanation the backend gives for it -- the report's
  // own case, a cancellation that matched more than one booking. The Python
  // suite holds the backend to this same fixture, so these tests cannot pass
  // against wording the backend no longer produces.
  const blocked = {
    ...notification,
    ...BLOCKED.row,
    id: "notification-blocked",
    booking_block: BLOCKED.booking_block,
  };
  const RAW_CODE = /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/;

  function serve(rows) {
    vi.stubGlobal("fetch", vi.fn((url) => {
      if (String(url).includes("/config")) return response({ enabled: true });
      if (String(url).includes("/summary")) return response({ summary: { unread: 0, new_offers: 0, selections: 0, joining_confirmations: 0, needs_review: 1 } });
      if (String(url).includes("/notifications")) return response({ notifications: rows, total: rows.length });
      return response({ status: "ok" });
    }));
  }

  async function openBlocked() {
    renderNotifications();
    fireEvent.click(await screen.findByText("Automatic Booking Blocked"));
    return screen.findByRole("dialog");
  }

  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    serve([blocked]);
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it("takes the explanation from the backend rather than inferring it", () => {
    // A status of "Blocked" says nothing about which check failed, so the
    // component must never manufacture a reason of its own.
    expect(blockingReason({ candidate_status: "Automatic Booking Blocked" })).toBeNull();
    expect(blockingReason(blocked)).toEqual(BLOCKED.booking_block);
  });

  it("never shows a code as the reason, even without an explanation", () => {
    const reason = blockingReason({ booking_block_reason_code: "MANUAL_REVIEW_REQUIRED" });
    expect(reason.title).not.toMatch(RAW_CODE);
    expect(reason.reason).not.toMatch(RAW_CODE);
    expect(reason.technical.reason_code).toBe("MANUAL_REVIEW_REQUIRED");
  });

  it("keeps an old stored sentence out of the reason when there is no explanation", () => {
    // Alerts written before the rewording stored sentences like this one,
    // which described a cancellation that matched no booking as a round problem.
    const stored = "Interview round could not be identified";
    const reason = blockingReason({ classification: "interview_cancelled", booking_block_reason: stored, booking_block_reason_code: "ROUND_NOT_FOUND" });
    expect(reason.reason).not.toContain(stored);
    expect(reason.action).toBeTruthy();
    expect(reason.technical.message).toBe(stored);
  });

  it("titles a fallback by what the email tried to do", () => {
    const block = { booking_block_reason_code: "ROUND_NOT_FOUND" };
    expect(blockingReason({ ...block, classification: "interview_cancelled" }).title).toBe("Cancellation not applied");
    expect(blockingReason({ ...block, classification: "interview_rescheduled" }).title).toBe("Booking not updated");
    expect(blockingReason({ ...block, classification: "interview_confirmed" }).title).toBe("Booking not created");
    expect(blockingReason({ ...block, candidate_status: "Assessment Needs a Slot" }).title).toBe("Assessment not booked");
  });

  it("shows the plain reason in the row without opening the notification", async () => {
    renderNotifications();
    const reason = await screen.findByText(`Reason: ${BLOCKED.booking_block.reason}`);
    // Same cell as the badge, so the two are read together.
    expect(reason.closest("td")).toContainElement(screen.getByText("Automatic Booking Blocked"));
    expect(reason.textContent).not.toMatch(RAW_CODE);
  });

  it("offers what to do on hover, not the codes", async () => {
    renderNotifications();
    const reason = await screen.findByText(`Reason: ${BLOCKED.booking_block.reason}`);
    expect(reason).toHaveAttribute("title", `What to do: ${BLOCKED.booking_block.action}`);
    expect(reason.getAttribute("title")).not.toMatch(RAW_CODE);
  });

  it("clamps a long reason instead of stretching the table", async () => {
    renderNotifications();
    const reason = await screen.findByText(`Reason: ${BLOCKED.booking_block.reason}`);
    expect(reason).toHaveClass("mail-status__reason");
  });

  it("leads the detail view with the decision in plain words", async () => {
    const dialog = await openBlocked();
    expect(within(dialog).getByRole("heading", { name: BLOCKED.booking_block.title })).toBeInTheDocument();
    const decision = within(dialog).getByRole("region", { name: "Booking decision" });
    expect(decision).toHaveTextContent(`Reason${BLOCKED.booking_block.reason}`);
    expect(decision).toHaveTextContent(`What to do${BLOCKED.booking_block.action}`);
    // The detected candidate stays visible alongside the decision.
    expect(dialog).toHaveTextContent("Rahul Kumar");
  });

  it("keeps the codes behind a closed Technical details section", async () => {
    const dialog = await openBlocked();
    const technical = within(dialog).getByText("Technical details").closest("details");
    expect(technical.open).toBe(false);
    expect(technical).toHaveTextContent("ROUND_NOT_FOUND");
    expect(technical).toHaveTextContent("BOOKING_AMBIGUOUS");
    expect(technical).toHaveTextContent("Blocked");
    // Everywhere else in the dialog: no code, and none of the old labels.
    const outside = dialog.cloneNode(true);
    outside.querySelector(".mail-block__technical").remove();
    expect(outside.textContent).not.toMatch(RAW_CODE);
    for (const label of ["Blocking reason", "Reason code", "Attempted booking"]) {
      expect(outside.textContent).not.toContain(label);
    }
  });

  it("gives one action, and it is the decision's", async () => {
    const dialog = await openBlocked();
    // The stored recommended action is the validator's own sentence -- a
    // reason, not an action -- so it moves to Technical details.
    expect(within(dialog).queryByText("Recommended action")).toBeNull();
    expect(dialog.querySelector(".mail-block__technical")).toHaveTextContent(BLOCKED.row.recommended_action);
  });

  it("marks a block with nothing to do as information, not a warning", async () => {
    serve([{ ...blocked, booking_block: { ...BLOCKED.booking_block, needs_action: false } }]);
    const dialog = await openBlocked();
    expect(within(dialog).getByRole("region", { name: "Booking decision" })).toHaveClass("mail-block--info");
  });

  it("shows no reason and keeps the recommended action for a booking that succeeded", async () => {
    serve([{ ...blocked, candidate_status: "Interview Automatically Booked", booking_status: "Auto Booked", booking_block_reason: null, booking_block_reason_code: null, booking_failure_code: null, booking_block: null, recommended_action: "Verify the offer with the candidate." }]);
    renderNotifications();
    fireEvent.click(await screen.findByText("Interview Automatically Booked"));
    const dialog = await screen.findByRole("dialog");
    expect(screen.queryByText(/^Reason:/)).toBeNull();
    expect(within(dialog).queryByRole("region", { name: "Booking decision" })).toBeNull();
    expect(dialog).toHaveTextContent("Recommended action");
  });
});

describe("booking result filter", () => {
  let listRequests;
  let summaryRequests;

  beforeEach(() => {
    listRequests = [];
    summaryRequests = [];
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("fetch", vi.fn((url) => {
      const path = String(url);
      if (path.includes("/notifications?")) {
        listRequests.push(new URL(path, "http://test.invalid").searchParams);
        return response({ notifications: [notification], total: 1 });
      }
      if (path.includes("/summary")) {
        summaryRequests.push(path);
        return response({ summary: { visible_total: 109, ai_retry_pending: 17, unread: 0 } });
      }
      if (path.includes("/candidates")) return response({ candidates: [{ candidate_id: "c1", candidate_name: "Asha Rao" }] });
      return response({ enabled: true, status: "ok" });
    }));
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  const lastList = () => listRequests[listRequests.length - 1];
  const bookingFilter = () => screen.getByRole("combobox", { name: "Booking result filter" });

  it("sits last in the filter row with the three labels", async () => {
    renderNotifications();
    await screen.findByText("Rahul Kumar");
    const row = bookingFilter().closest(".mail-filters");
    const controls = [...row.querySelectorAll("input, select")].map((control) => control.getAttribute("aria-label"));
    expect(controls).toEqual(["Search notifications", "Candidate filter", "Alert type filter", "Booking result filter"]);
    expect([...bookingFilter().options].map((option) => option.textContent))
      .toEqual(["All booking results", "Successfully booked", "Blocked"]);
    expect(BOOKING_RESULTS.map((result) => result.value)).toEqual(["booked", "blocked"]);
  });

  it("asks the server for booked or blocked alerts, and for everything again", async () => {
    renderNotifications();
    await screen.findByText("Rahul Kumar");
    expect(lastList().has("booking_result")).toBe(false);
    fireEvent.change(bookingFilter(), { target: { value: "booked" } });
    await waitFor(() => expect(lastList().get("booking_result")).toBe("booked"));
    fireEvent.change(bookingFilter(), { target: { value: "blocked" } });
    await waitFor(() => expect(lastList().get("booking_result")).toBe("blocked"));
    fireEvent.change(bookingFilter(), { target: { value: "" } });
    await waitFor(() => expect(lastList().has("booking_result")).toBe(false));
  });

  it("combines with the candidate, alert type and search filters", async () => {
    renderNotifications();
    await screen.findByText("Rahul Kumar");
    await screen.findByRole("option", { name: "Asha Rao" });
    fireEvent.change(screen.getByRole("combobox", { name: "Candidate filter" }), { target: { value: "c1" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Alert type filter" }), { target: { value: "interview" } });
    fireEvent.change(screen.getByLabelText("Search notifications"), { target: { value: "Java" } });
    fireEvent.change(bookingFilter(), { target: { value: "blocked" } });
    await waitFor(() => expect(lastList().get("booking_result")).toBe("blocked"));
    expect(lastList().get("candidate_id")).toBe("c1");
    expect(lastList().get("classification_group")).toBe("interview");
    expect(lastList().get("search")).toBe("Java");
    // A new filter always starts from the first page.
    expect(lastList().get("offset")).toBe("0");
  });

  it("leaves the summary counts global", async () => {
    renderNotifications();
    await screen.findByText("Rahul Kumar");
    fireEvent.change(bookingFilter(), { target: { value: "booked" } });
    await waitFor(() => expect(lastList().get("booking_result")).toBe("booked"));
    // The cards count everything, whatever the table is filtered to.
    expect(summaryRequests.every((path) => !path.includes("?"))).toBe(true);
    expect(screen.getByRole("button", { name: /109\s*All/ })).toBeInTheDocument();
  });

  it("is cleared by the All card, with every other filter", async () => {
    renderNotifications();
    await screen.findByText("Rahul Kumar");
    await screen.findByRole("option", { name: "Asha Rao" });
    fireEvent.change(screen.getByRole("combobox", { name: "Candidate filter" }), { target: { value: "c1" } });
    fireEvent.change(screen.getByRole("combobox", { name: "Alert type filter" }), { target: { value: "interview" } });
    fireEvent.change(bookingFilter(), { target: { value: "blocked" } });
    await waitFor(() => expect(lastList().get("booking_result")).toBe("blocked"));
    fireEvent.click(screen.getByRole("button", { name: /109\s*All/ }));
    await waitFor(() => expect(lastList().has("booking_result")).toBe(false));
    // It used to send candidate_id=undefined, which no row matches, so "All"
    // emptied the table.
    expect(lastList().toString()).not.toContain("undefined");
    for (const key of ["candidate_id", "classification_group", "search"]) expect(lastList().has(key)).toBe(false);
    expect(bookingFilter()).toHaveValue("");
  });
});
