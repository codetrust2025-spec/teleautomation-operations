/**
 * Mail Alerts: compact tab strip
 *
 * Three tabs sit above the filter row:
 *   1. Unread Invitations  — selected by default, requests is_read=false
 *   2. AI Retry Pending    — requests priority=retry_pending
 *   3. All Alerts          — no extra filter
 *
 * Switching tabs immediately changes the server request; page resets to 0.
 * Live counts on each tab come from the summary endpoint and update when
 * alerts are read or dismissed.
 * The existing candidate / alert-type / search / booking-result filters
 * remain active inside any tab (they combine with the tab's own filter).
 */

import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MailMonitoringNotifications, MAIL_TABS } from "./MailMonitoringNotifications.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

class FakeWebSocket {
  static OPEN = 1;
  readyState = 1;
  constructor() { setTimeout(() => this.onopen?.(), 0); }
  send() {}
  close() { this.onclose?.(); }
}

const SUMMARY = { visible_total: 109, ai_retry_pending: 17, unread: 34 };

function response(body) {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
}

let listRequests = [];
let summaryRequests = [];

function install(summaryOverride = {}) {
  listRequests = [];
  summaryRequests = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  vi.stubGlobal("fetch", vi.fn((url) => {
    const path = String(url);
    if (path.includes("/notifications?")) {
      listRequests.push(new URL(path, "http://test.invalid").searchParams);
      return response({ notifications: [], total: 0 });
    }
    if (path.includes("/summary")) {
      summaryRequests.push(path);
      return response({ summary: { ...SUMMARY, ...summaryOverride } });
    }
    if (path.includes("/candidates")) return response({ candidates: [] });
    return response({ status: "ok" });
  }));
}

const lastList = () => listRequests[listRequests.length - 1];
const renderScreen = () => render(<ConfirmProvider><MailMonitoringNotifications /></ConfirmProvider>);

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

// ── Tab definition ────────────────────────────────────────────────────────────

describe("MAIL_TABS definition", () => {
  it("exports exactly three tabs in the required order", () => {
    expect(MAIL_TABS.map((t) => t.id)).toEqual(["unread", "retry", "all"]);
    expect(MAIL_TABS.map((t) => t.label)).toEqual([
      "Unread Invitations",
      "AI Retry Pending",
      "All Alerts",
    ]);
  });
});

// ── Default state ─────────────────────────────────────────────────────────────

describe("default tab", () => {
  beforeEach(() => install());

  it("renders a tablist with three tabs", async () => {
    renderScreen();
    const nav = await screen.findByRole("tablist", { name: "Mail alerts filter tabs" });
    const tabs = [...nav.querySelectorAll("[role='tab']")];
    expect(tabs).toHaveLength(3);
    expect(tabs.map((t) => t.textContent.replace(/\d+\+?/g, "").trim())).toEqual([
      "Unread Invitations",
      "AI Retry Pending",
      "All Alerts",
    ]);
  });

  it("selects Unread Invitations by default", async () => {
    renderScreen();
    const tab = await screen.findByRole("tab", { name: /Unread Invitations/ });
    expect(tab).toHaveAttribute("aria-selected", "true");
  });

  it("sends is_read=false on the initial request", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    expect(lastList().get("is_read")).toBe("false");
    expect(lastList().has("priority")).toBe(false);
  });
});

// ── Count badges ──────────────────────────────────────────────────────────────

describe("live count badges", () => {
  beforeEach(() => install());

  it("shows unread count on Unread Invitations tab", async () => {
    renderScreen();
    await waitFor(() => expect(summaryRequests.length).toBeGreaterThan(0));
    const tab = await screen.findByRole("tab", { name: /Unread Invitations/ });
    expect(tab.textContent).toContain(String(SUMMARY.unread));
  });

  it("shows ai_retry_pending count on AI Retry Pending tab", async () => {
    renderScreen();
    await waitFor(() => expect(summaryRequests.length).toBeGreaterThan(0));
    const tab = await screen.findByRole("tab", { name: /AI Retry Pending/ });
    expect(tab.textContent).toContain(String(SUMMARY.ai_retry_pending));
  });

  it("shows visible_total count on All Alerts tab", async () => {
    renderScreen();
    await waitFor(() => expect(summaryRequests.length).toBeGreaterThan(0));
    const tab = await screen.findByRole("tab", { name: /All Alerts/ });
    // 109 > 99, so the badge caps at "99+"
    expect(tab.textContent).toContain("99+");
  });
});

// ── Tab switching ─────────────────────────────────────────────────────────────

describe("switching tabs", () => {
  beforeEach(() => install());

  it("selecting AI Retry Pending sends priority=retry_pending without is_read", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    fireEvent.click(await screen.findByRole("tab", { name: /AI Retry Pending/ }));
    await waitFor(() => {
      const last = lastList();
      expect(last.get("priority")).toBe("retry_pending");
      expect(last.has("is_read")).toBe(false);
    });
  });

  it("selecting All Alerts sends neither is_read nor priority", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    fireEvent.click(await screen.findByRole("tab", { name: /All Alerts/ }));
    await waitFor(() => {
      const last = lastList();
      expect(last.has("is_read")).toBe(false);
      expect(last.has("priority")).toBe(false);
    });
  });

  it("switching back to Unread Invitations restores is_read=false", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    fireEvent.click(await screen.findByRole("tab", { name: /All Alerts/ }));
    fireEvent.click(await screen.findByRole("tab", { name: /Unread Invitations/ }));
    await waitFor(() => expect(lastList().get("is_read")).toBe("false"));
  });

  it("marks the newly clicked tab as aria-selected and deselects the others", async () => {
    renderScreen();
    const retryTab = await screen.findByRole("tab", { name: /AI Retry Pending/ });
    fireEvent.click(retryTab);
    await waitFor(() => expect(retryTab).toHaveAttribute("aria-selected", "true"));
    const unreadTab = screen.getByRole("tab", { name: /Unread Invitations/ });
    const allTab = screen.getByRole("tab", { name: /All Alerts/ });
    expect(unreadTab).toHaveAttribute("aria-selected", "false");
    expect(allTab).toHaveAttribute("aria-selected", "false");
  });

  it("resets to page 0 when switching tabs", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    fireEvent.click(await screen.findByRole("tab", { name: /All Alerts/ }));
    await waitFor(() => expect(lastList().get("offset")).toBe("0"));
  });
});

// ── Filter composition ────────────────────────────────────────────────────────

describe("tab + advanced filter composition", () => {
  beforeEach(() => install());

  it("combines Unread Invitations tab with the search field", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    // Tab is already on Unread by default
    fireEvent.change(screen.getByLabelText("Search notifications"), { target: { value: "Infosys" } });
    await waitFor(() => {
      const last = lastList();
      expect(last.get("is_read")).toBe("false");
      expect(last.get("search")).toBe("Infosys");
    });
  });

  it("combines AI Retry Pending tab with alert-type filter", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    fireEvent.click(await screen.findByRole("tab", { name: /AI Retry Pending/ }));
    fireEvent.change(screen.getByLabelText("Alert type filter"), { target: { value: "interview" } });
    await waitFor(() => {
      const last = lastList();
      expect(last.get("priority")).toBe("retry_pending");
      expect(last.get("classification_group")).toBe("interview");
    });
  });

  it("tab-owned keys do not leak into each other when switching", async () => {
    renderScreen();
    await waitFor(() => expect(listRequests.length).toBeGreaterThan(0));
    // Switch to retry (sets priority=retry_pending)
    fireEvent.click(await screen.findByRole("tab", { name: /AI Retry Pending/ }));
    await waitFor(() => expect(lastList().get("priority")).toBe("retry_pending"));
    // Switch to Unread — priority must vanish, is_read must appear
    fireEvent.click(await screen.findByRole("tab", { name: /Unread Invitations/ }));
    await waitFor(() => {
      const last = lastList();
      expect(last.get("is_read")).toBe("false");
      expect(last.has("priority")).toBe(false);
    });
  });
});

// ── Empty state messages ──────────────────────────────────────────────────────

describe("tab-aware empty state", () => {
  function installEmpty() {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    vi.stubGlobal("fetch", vi.fn((url) => {
      const path = String(url);
      if (path.includes("/notifications?")) return response({ notifications: [], total: 0 });
      if (path.includes("/summary")) return response({ summary: { visible_total: 0, ai_retry_pending: 0, unread: 0 } });
      if (path.includes("/candidates")) return response({ candidates: [] });
      return response({ status: "ok" });
    }));
  }

  beforeEach(() => installEmpty());

  it("shows a friendly message when Unread Invitations is empty", async () => {
    renderScreen();
    await screen.findByText("No unread invitations. All caught up!");
  });

  it("shows an appropriate message when AI Retry Pending is empty", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("tab", { name: /AI Retry Pending/ }));
    await screen.findByText("No alerts are waiting for AI retry.");
  });

  it("shows the generic message when All Alerts is empty", async () => {
    renderScreen();
    fireEvent.click(await screen.findByRole("tab", { name: /All Alerts/ }));
    await screen.findByText("No notifications match these filters.");
  });
});
