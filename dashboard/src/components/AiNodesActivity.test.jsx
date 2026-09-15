/**
 * The AI nodes panel shows the node serving a booking analysis as
 * "Busy · Booking analysis".
 *
 * The activity comes from the gateway's own record -- the same one the booking
 * page names its node from -- through the nodes endpoint and the live socket.
 * Driven through the real panel with the network and the socket replaced.
 */
import React from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RecruitmentMailPanel } from "./RecruitmentMailPanel.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

vi.mock("../context/AuthContext.jsx", () => ({
  useAuth: () => ({ username: "tester", role: "admin" }),
}));

const live = { activity: null, status: null };
vi.mock("../notifications/mailEventStream.js", () => ({
  subscribeNodeActivity: (handler) => {
    live.activity = handler;
    return () => {};
  },
  subscribeMailStatus: (handler) => {
    live.status = handler;
    handler("Offline");
    return () => {};
  },
  subscribeMailEvents: () => () => {},
  traceMailAlert: () => ({}),
  getMailStatus: () => "Offline",
}));

const NODES = [
  { id: "rtx4060", label: "RTX 4060", primary: true },
  { id: "jagadeesh", label: "Jagadeesh", primary: false },
  { id: "our_machine", label: "Praveen", primary: false },
].map((node) => ({
  ...node,
  status: "online",
  endpoint_reachable: true,
  ready: true,
  model_loaded: false,
  response_time_ms: 20,
}));

const BOOKING = { kind: "booking_analysis", label: "Booking analysis", workload: "payment_screenshot_vision", started_at: 1 };

function serve({ activity, activityEndpoint } = {}) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      const target = String(url);
      let body = { status: "ok" };
      if (target.includes("/ollama/nodes"))
        body = {
          status: "ok",
          nodes: NODES.map((node) => ({ ...node, activity: activity?.nodes?.[node.id] || [] })),
          activity,
        };
      else if (target.includes("/ollama/activity"))
        body = { status: "ok", activity: activityEndpoint };
      else if (target.includes("/candidates?")) body = { status: "ok", candidates: [] };
      return { ok: true, json: async () => body };
    }),
  );
}

function stateOf(label) {
  const row = screen.getByText(label, { selector: "strong" }).closest("article");
  const states = row.querySelectorAll(".sot-ai-node-state");
  return states[states.length - 1].textContent;
}

async function renderPanel() {
  render(
    <ConfirmProvider>
      <RecruitmentMailPanel />
    </ConfirmProvider>,
  );
  await screen.findByText("RTX 4060", { selector: "strong" });
}

describe("AI nodes panel: what a node is busy with", () => {
  beforeEach(() => {
    live.activity = null;
    live.status = null;
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("marks the node the gateway reports as Busy · Booking analysis", async () => {
    serve({ activity: { boot: "b1", version: 3, nodes: { rtx4060: [BOOKING] } } });
    await renderPanel();
    await waitFor(() => expect(stateOf("RTX 4060")).toBe("Busy · Booking analysis"));
    expect(stateOf("Jagadeesh")).toBe("Ready");
    expect(stateOf("Praveen")).toBe("Ready");
  });

  it("follows a live push when the analysis moves to another node", async () => {
    serve({ activity: { boot: "b1", version: 3, nodes: { rtx4060: [BOOKING] } } });
    await renderPanel();
    await waitFor(() => expect(stateOf("RTX 4060")).toBe("Busy · Booking analysis"));

    act(() => live.activity({ event: "ai_node_activity", boot: "b1", version: 4, nodes: { our_machine: [BOOKING] } }));

    expect(stateOf("RTX 4060")).toBe("Ready");
    expect(stateOf("Praveen")).toBe("Busy · Booking analysis");

    act(() => live.activity({ event: "ai_node_activity", boot: "b1", version: 5, nodes: {} }));
    expect(stateOf("Praveen")).toBe("Ready");
  });

  it("names other work the same way, and falls back to the old states when idle", async () => {
    serve({
      activity: {
        boot: "b1",
        version: 1,
        nodes: { jagadeesh: [{ kind: "mail_analysis", label: "Mail analysis" }] },
      },
    });
    await renderPanel();
    await waitFor(() => expect(stateOf("Jagadeesh")).toBe("Busy · Mail analysis"));
    expect(stateOf("RTX 4060")).toBe("Ready");
  });

  it("ignores a snapshot older than one already shown", async () => {
    serve({ activity: { boot: "b1", version: 9, nodes: { rtx4060: [BOOKING] } } });
    await renderPanel();
    await waitFor(() => expect(stateOf("RTX 4060")).toBe("Busy · Booking analysis"));
    act(() => live.activity({ event: "ai_node_activity", boot: "b1", version: 8, nodes: {} }));
    expect(stateOf("RTX 4060")).toBe("Busy · Booking analysis");
  });

  it("accepts a restarted server's snapshot even though its counter is lower", async () => {
    serve({ activity: { boot: "b1", version: 9, nodes: { rtx4060: [BOOKING] } } });
    await renderPanel();
    await waitFor(() => expect(stateOf("RTX 4060")).toBe("Busy · Booking analysis"));
    act(() => live.activity({ event: "ai_node_activity", boot: "b2", version: 1, nodes: {} }));
    expect(stateOf("RTX 4060")).toBe("Ready");
  });

  it("reads the current snapshot whenever the socket comes back", async () => {
    serve({
      activity: { boot: "b1", version: 1, nodes: {} },
      activityEndpoint: { boot: "b1", version: 2, nodes: { jagadeesh: [BOOKING] } },
    });
    await renderPanel();
    await waitFor(() => expect(stateOf("Jagadeesh")).toBe("Ready"));

    act(() => live.status("Live"));

    await waitFor(() => expect(stateOf("Jagadeesh")).toBe("Busy · Booking analysis"));
    expect(fetch.mock.calls.some(([url]) => String(url).includes("/api/ai-recruitment/ollama/activity"))).toBe(true);
  });
});
