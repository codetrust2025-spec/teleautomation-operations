import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  RecruitmentMailPanel,
  shouldShowInSelectionOfferReview,
} from "./RecruitmentMailPanel.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

// This page now carries the project-wide OCR switch, which reads the signed-in
// role to decide whether the control is writable. These tests render the panel
// outside AuthProvider, so the role is supplied directly rather than wrapping
// every case in a provider that would also start its own auth requests.
vi.mock("../context/AuthContext.jsx", () => ({
  useAuth: () => ({ username: "tester", role: "admin" }),
}));

const payloadFor = (url) => {
  if (url.includes("/ollama/status"))
    return {
      status: "ok",
      ollama: {
        status: "healthy",
        diagnostic_status: "AVAILABLE",
        last_checked_at: "2026-07-18T10:15:31Z",
      },
    };
  if (url.includes("/dashboard"))
    return {
      status: "ok",
      metrics: {
        selected: 1,
        offers_received: 0,
        offers_accepted: 0,
        joining_confirmed: 5,
        joined: 0,
        needs_review: 0,
      },
      charts: {},
      flags: [],
    };
  if (url.includes("/review")) return { status: "ok", events: [] };
  if (url.includes("/offer-verification")) return { status: "ok", cases: [] };
  if (url.includes("/mail-monitoring/notifications"))
    return {
      status: "ok",
      notifications: [
        {
          id: "notification-1",
          ai_recruitment_event_id: "event-1",
          candidate_id: "c1",
          candidate_name: "Test Candidate",
          classification: "interview_confirmed",
          email_subject: "Frontend interview invitation",
          interview_date: "2026-07-22",
          interview_time: "03:00 PM",
          interview_timezone: "Asia/Kolkata",
          ai_confidence: 0.8,
          booking_status: "Blocked",
        },
      ],
    };
  if (url.includes("/events/event-1"))
    return {
      status: "ok",
      event: {
        id: "event-1",
        subject: "Frontend interview invitation",
        primary_status: "INTERVIEW_CONFIRMED",
        review_status: "APPROVED",
        validation_status: "APPROVED",
        ai_status: "RETRY_PENDING",
        ai_model: "unavailable:ollama_request_timeout",
        summary:
          "Fallback evidence indicates interview confirmed. AI validation unavailable (OLLAMA_REQUEST_TIMEOUT).",
        evidence_summary:
          "Fallback evidence indicates interview confirmed. AI validation unavailable (OLLAMA_REQUEST_TIMEOUT).",
        structured_result: {
          evidence: [
            { meaning: "Interview confirmed", text: "Interview at 3 PM" },
          ],
        },
        received_email: {
          subject: "Frontend interview invitation",
          sender_name: "Recruiter",
          sender_email: "recruiter@example.com",
          recipient_email: "candidate@example.com",
          sent_at: "2026-07-21T08:00:00Z",
          body: "Your frontend interview is scheduled for tomorrow at 3 PM.",
        },
      },
    };
  if (url.includes("/candidates?"))
    return {
      status: "ok",
      candidates: [
        {
          id: "c1",
          name: "Test Candidate",
          phone: "9000000000",
          email: "test.candidate@example.com",
          stage: "in_progress",
          service_type: "profile_service",
        },
      ],
    };
  return { status: "ok" };
};

describe("RecruitmentMailPanel", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => ({
        ok: true,
        json: async () => payloadFor(String(url)),
      })),
    );
  });
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  it("renders the canonical mailbox-only page without retired hub navigation", async () => {
    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    expect(
      screen.getByRole("heading", { name: "Candidate Mailboxes" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: "+ Add Gmail" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Global candidate filter" }),
    ).toBeInTheDocument();
    // This page is intentionally mailbox-only: the former top-level journeys
    // are retired and must not reappear as navigation.
    for (const retired of [
      "Review Queue",
      "Selection & Offers",
      "Interview Monitoring",
      "Overview",
    ]) {
      expect(
        screen.queryByRole("button", { name: retired }),
      ).not.toBeInTheDocument();
    }
  });
  it("opens Gmail connection inline from the main review screen", async () => {
    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    const addButton = await screen.findByRole("button", {
      name: "+ Add Gmail",
    });
    fireEvent.click(addButton);
    expect(
      screen.getByRole("heading", { name: "Connect Gmail" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Cancel" }),
    ).toBeInTheDocument();
  });
  it("refreshes monitoring data without reloading the page", async () => {
    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    await waitFor(() =>
      expect(
        fetch.mock.calls.filter(([url]) =>
          String(url).includes("/candidate-mailboxes/overview"),
        ).length,
      ).toBeGreaterThanOrEqual(1),
    );
    fireEvent.click(screen.getByRole("button", { name: "↻ Refresh" }));
    await waitFor(() =>
      expect(
        fetch.mock.calls.filter(([url]) =>
          String(url).includes("/candidate-mailboxes/overview"),
        ).length,
      ).toBeGreaterThanOrEqual(2),
    );
    expect(screen.getByText(/Last updated:/)).not.toHaveTextContent("Loading");
  });
  it("offers an add candidate Gmail form for candidates without a mailbox", async () => {
    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    const addButton = await screen.findByRole("button", {
      name: "+ Add Gmail",
    });
    fireEvent.click(addButton);
    expect(
      screen.getByRole("heading", { name: "Connect Gmail" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /Test Candidate · 9000000000/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText(/^candidate@gmail\.com/),
    ).toHaveAttribute("type", "email");
    expect(
      screen.getByRole("button", { name: "Connect Gmail" }),
    ).toBeDisabled();
  });
  it("shows only in-progress profile candidates without a linked Gmail", async () => {
    fetch.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes("/candidates?"))
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            candidates: [
              {
                id: "profile-pending",
                name: "Pending Profile",
                phone: "9111111111",
                email: "pending@example.com",
                stage: "in_progress",
                service_type: "profile_service",
              },
              {
                id: "round-wise",
                name: "Round Wise Candidate",
                stage: "in_progress",
                service_type: "round_wise",
              },
              {
                id: "completed-profile",
                name: "Completed Profile",
                stage: "completed",
                service_type: "profile_service",
              },
            ],
          }),
        };
      if (path.includes("/candidate-mailboxes/overview"))
        return {
          ok: true,
          json: async () => ({ status: "ok", mailboxes: [] }),
        };
      return { ok: true, json: async () => payloadFor(path) };
    });

    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    const pendingTab = await screen.findByRole("tab", {
      name: "Pending Gmail 1",
    });
    fireEvent.click(pendingTab);

    const pendingTable = screen.getByRole("table");
    expect(within(pendingTable).getByText("Pending Profile")).toBeInTheDocument();
    expect(
      within(pendingTable).getByText("Profile in progress"),
    ).toBeInTheDocument();
    expect(
      within(pendingTable).queryByText("Round Wise Candidate"),
    ).not.toBeInTheDocument();
    expect(
      within(pendingTable).queryByText("Completed Profile"),
    ).not.toBeInTheDocument();

    fireEvent.click(
      within(pendingTable).getByRole("button", { name: "Link Gmail" }),
    );
    expect(screen.getByLabelText("Candidate Gmail owner")).toHaveValue(
      "profile-pending",
    );
    expect(screen.getByLabelText("Gmail address")).toHaveValue(
      "pending@example.com",
    );
  });
  it("autopopulates the selected candidate's saved email address", async () => {
    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "+ Add Gmail" }),
    );
    fireEvent.change(screen.getByLabelText("Candidate Gmail owner"), {
      target: { value: "c1" },
    });
    expect(screen.getByLabelText("Gmail address")).toHaveValue(
      "test.candidate@example.com",
    );
    expect(screen.getByRole("button", { name: "Connect Gmail" })).toBeEnabled();
  });
  it("keeps mailboxes attached to a legacy candidate alias visible", async () => {
    fetch.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes("/candidate-mailboxes/overview"))
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            mailboxes: [
              {
                mailbox: {
                  id: "mailbox-legacy",
                  candidate_id: "legacy-candidate-row",
                  canonical_candidate_id: "c1",
                  email_address: "legacy-linked@example.com",
                  connection_status: "CONNECTED",
                  monitoring_enabled: true,
                },
                stats: { important_emails: 2, pending_reviews: 0 },
              },
            ],
          }),
        };
      return { ok: true, json: async () => payloadFor(path) };
    });

    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );

    expect(await screen.findByText("legacy-linked@example.com")).toBeInTheDocument();
    expect(
      screen.getByRole("cell", { name: /Test Candidate/ }),
    ).toBeInTheDocument();
  });
  it("labels the phone as a phone and keeps candidates sharing one distinct", async () => {
    fetch.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes("/candidates?"))
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            candidates: [
              {
                id: "candidate-alpha",
                name: "Shared Phone One",
                phone: "9000000102",
                stage: "in_progress",
                service_type: "profile_service",
              },
              {
                id: "candidate-beta",
                name: "Shared Phone Two",
                phone: "9000000102",
                stage: "in_progress",
                service_type: "profile_service",
              },
            ],
          }),
        };
      if (path.includes("/candidate-mailboxes/overview"))
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            mailboxes: [
              {
                mailbox: {
                  id: "mailbox-alpha",
                  candidate_id: "candidate-alpha",
                  email_address: "alpha@example.com",
                  connection_status: "CONNECTED",
                  monitoring_enabled: true,
                },
                stats: {},
              },
              {
                mailbox: {
                  id: "mailbox-beta",
                  candidate_id: "candidate-beta",
                  email_address: "beta@example.com",
                  connection_status: "CONNECTED",
                  monitoring_enabled: true,
                },
                stats: {},
              },
            ],
          }),
        };
      return { ok: true, json: async () => payloadFor(path) };
    });

    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );

    await screen.findByText("alpha@example.com");
    // A phone number presented as a candidate id made two distinct candidates
    // read as one during an identity investigation.  The label must match the
    // value it describes.
    expect(screen.queryByText(/Candidate ID: 9000000102/)).toBeNull();
    expect(screen.getAllByText("Phone: 9000000102")).toHaveLength(2);
    // Two candidates sharing a phone stay distinguishable by their real ids.
    expect(
      screen.getByText("Candidate ID: candidate-alpha"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Candidate ID: candidate-beta"),
    ).toBeInTheDocument();
  });
  it("keeps mailbox administration separate from mail review reporting", async () => {
    fetch.mockImplementation(async (url, options = {}) => {
      const path = String(url);
      if (path.includes("/candidate-mailboxes/overview")) {
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            mailboxes: [
              {
                mailbox: {
                  id: "mailbox-relevant",
                  candidate_id: "c1",
                  email_address: "relevant@example.com",
                  connection_status: "CONNECTED",
                  monitoring_enabled: true,
                },
                stats: { important_emails: 3, pending_reviews: 0 },
              },
              {
                mailbox: {
                  id: "mailbox-review",
                  candidate_id: "c1",
                  email_address: "review@example.com",
                  connection_status: "CONNECTED",
                  monitoring_enabled: true,
                },
                stats: { important_emails: 0, pending_reviews: 1 },
              },
            ],
          }),
        };
      }
      return { ok: true, json: async () => payloadFor(path) };
    });
    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    await screen.findByText("relevant@example.com");
    expect(screen.getByText("review@example.com")).toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Relevant Emails" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "Needs Review" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "View Emails" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getAllByLabelText(/More actions for Test Candidate/),
    ).toHaveLength(2);
  });
  it("shows action confirmation and live sync progress for a mailbox", async () => {
    let syncRequested = false;
    fetch.mockImplementation(async (url, options = {}) => {
      const path = String(url);
      if (path.includes("/api/candidates/c1/mailbox/sync")) {
        syncRequested = true;
        return {
          ok: true,
          json: async () => ({ status: "ok", job: { status: "QUEUED" } }),
        };
      }
      if (path.includes("/candidate-mailboxes/overview")) {
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            mailboxes: [
              {
                mailbox: {
                  id: "m1",
                  candidate_id: "c1",
                  email_address: "candidate@example.com",
                  connection_status: "CONNECTED",
                  monitoring_enabled: true,
                },
                stats: {
                  latest_sync_status: syncRequested ? "QUEUED" : "COMPLETED",
                },
              },
            ],
          }),
        };
      }
      return { ok: true, json: async () => payloadFor(path) };
    });
    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    await screen.findByText("candidate@example.com");
    fireEvent.click(screen.getByRole("button", { name: "Sync Now" }));
    await waitFor(() =>
      expect(screen.getByText(/mailbox sync is queued/i)).toBeInTheDocument(),
    );
    expect(screen.getByText("Sync Queued")).toBeInTheDocument();
    expect(screen.getByText("Waiting to start…")).toBeInTheDocument();
  });
  it("does not overlap mailbox overview polls while a sync is active", async () => {
    vi.useFakeTimers();
    let overviewCalls = 0;
    let finishPoll;
    fetch.mockImplementation(async (url) => {
      const path = String(url);
      if (path.includes("/candidate-mailboxes/overview")) {
        overviewCalls += 1;
        if (overviewCalls > 1) {
          return new Promise((resolve) => {
            finishPoll = () => resolve({
              ok: true,
              json: async () => ({ status: "ok", mailboxes: [] }),
            });
          });
        }
        return {
          ok: true,
          json: async () => ({
            status: "ok",
            mailboxes: [{
              mailbox: {
                id: "mailbox-active",
                candidate_id: "c1",
                email_address: "active@example.com",
                connection_status: "CONNECTED",
                monitoring_enabled: true,
              },
              stats: { latest_sync_status: "RUNNING" },
            }],
          }),
        };
      }
      return { ok: true, json: async () => payloadFor(path) };
    });

    render(
      <ConfirmProvider>
        <RecruitmentMailPanel />
      </ConfirmProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByText("active@example.com")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    expect(overviewCalls).toBe(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(overviewCalls).toBe(2);

    await act(async () => {
      finishPoll();
      await vi.advanceTimersByTimeAsync(0);
    });
    vi.useRealTimers();
  });
  it("defensively hides historical zero-percent recommendations", () => {
    expect(
      shouldShowInSelectionOfferReview({
        primary_status: "MANUAL_REVIEW_REQUIRED",
        confidence: 0,
        review_status: "PENDING",
        visible_in_offer_review: true,
        subject: "Job recommendations for you | foundit (Monster)",
        structured_result: {
          is_selection_or_offer_related: false,
          evidence: [],
        },
      }),
    ).toBe(false);
  });
  it("keeps strong manual offer evidence at 80 percent or more", () => {
    expect(
      shouldShowInSelectionOfferReview({
        primary_status: "MANUAL_REVIEW_REQUIRED",
        confidence: 0.85,
        review_status: "PENDING",
        visible_in_offer_review: true,
        structured_result: {
          is_selection_or_offer_related: true,
          evidence: [
            { meaning: "OFFER_INDICATION", text: "we are pleased to offer" },
          ],
        },
      }),
    ).toBe(true);
  });

  const retryPendingEvent = {
    id: "event-retry-1",
    candidate_id: "c1",
    subject: "Reminder: Don't Forget to attend these Walk-in's today",
    primary_status: "MANUAL_REVIEW_REQUIRED",
    review_status: "PENDING",
    validation_status: "RETRY_PENDING",
    ai_status: "RETRY_PENDING",
    ai_model: "unavailable:ollama_connection_failed",
    confidence: 0,
    visible_in_offer_review: true,
    created_at: "2026-07-18T13:38:53Z",
    structured_result: { evidence: [], validation_status: "RETRY_PENDING" },
  };






});
