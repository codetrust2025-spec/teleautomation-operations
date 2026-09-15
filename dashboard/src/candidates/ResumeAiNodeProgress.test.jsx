/**
 * Resume uploads name the AI node reading them, as every AI upload does.
 *
 * The auto-fill button used to say "⏳ AI analyzing (~30-60s)…" -- a guess -- and
 * the table cell "…". Both now show the shared node status: waiting, the node
 * reading it, then which node read it and how long it took. Only a PDF is read
 * by a node before it is filed, so only a PDF is followed.
 */
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResumeAutoFill, ResumeCell } from "./candidatesModule.jsx";

class FakeEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.closed = false;
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }
}

const stream = () => FakeEventSource.instances[FakeEventSource.instances.length - 1];
const push = (status) =>
  act(() => stream().onmessage({ data: JSON.stringify({ analysed_by: [], failed_on: [], ...status }) }));

/** A fetch whose answers the test gives, one pending request at a time. */
function stubServer(route = () => null) {
  const server = { requests: [] };
  vi.stubGlobal("fetch", vi.fn((url, options = {}) => {
    const fixed = route(String(url), options);
    if (fixed) return Promise.resolve({ ok: true, json: async () => fixed });
    return new Promise((resolve) => {
      server.requests.push({ url: String(url), options, answer: (body) => resolve({ ok: true, json: async () => body }) });
    });
  }));
  server.answer = (body) => act(async () => server.requests.shift().answer(body));
  return server;
}

const pdf = () => new File(["%PDF-1.4"], "Ravi_Resume.pdf", { type: "application/pdf" });
const RTX = { state: "done", node: "RTX 4060", analysed_by: ["RTX 4060"], failed_on: [] };

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.querySelectorAll(".cand-resume-manager").forEach((node) => node.remove());
});

describe("resume auto-fill", () => {
  it("follows the node reading a new candidate's resume, then says who read it", async () => {
    const server = stubServer();
    const { container } = render(<ResumeAutoFill onExtracted={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [pdf()] } });

    await waitFor(() => expect(server.requests).toHaveLength(1));
    const { url, options } = server.requests[0];
    expect(url).toBe("/public/slots/extract-resume-ai");
    expect(stream().url).toBe(`/public/slots/analysis/${options.body.get("analysis_id")}/events`);
    expect(screen.getByText("Waiting for AI node…")).toBeTruthy();
    expect(screen.queryByText(/30-60s/)).toBeNull();
    expect(screen.getByRole("button", { name: "Reading resume…" })).toBeTruthy();

    push({ state: "running", node: "Jagadeesh" });
    expect(document.querySelector(".ai-node-progress--active").textContent).toContain("● Jagadeesh · Analysing…");

    await server.answer({
      status: "ok", success: true, data: { candidate_name: "Ravi Kumar", technology: "Java" },
      analysis: { ...RTX, node: "Jagadeesh", analysed_by: ["Jagadeesh"] },
    });
    expect(await screen.findByText(/^✓ Analysed by Jagadeesh in \d+\.\ds$/)).toBeTruthy();
    expect(screen.getByText("✓ Fill profile fields")).toBeTruthy();
    expect(stream().closed).toBe(true);
  });

  it("says a document that is not a resume was refused, with the node that read it", async () => {
    const server = stubServer();
    const { container } = render(<ResumeAutoFill candidateId="cand-1" onExtracted={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [pdf()] } });

    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(server.requests[0].url).toBe("/candidates/cand-1/resumes");
    await server.answer({
      status: "error", message: "This file does not look like a resume, so it was not saved.",
      ai_extraction: { is_resume: false }, analysis: RTX,
    });
    expect(await screen.findByText(/^✕ Not a resume · Analysed by RTX 4060 in \d+\.\ds$/)).toBeTruthy();
    expect(document.querySelector(".ai-node-progress__timer")).toBeNull();
  });

  it("keeps counting across the second read an edit makes, and names that read's node", async () => {
    const server = stubServer();
    const { container } = render(<ResumeAutoFill candidateId="cand-1" onExtracted={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [pdf()] } });

    await waitFor(() => expect(server.requests).toHaveLength(1));
    const firstId = server.requests[0].options.body.get("analysis_id");
    // Filed, but the classification read produced nothing to fill in.
    await server.answer({ status: "ok", resume: { id: "resume-1" }, analysis: { ...RTX, analysed_by: [] } });

    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(server.requests[0].url).toBe("/public/slots/extract-resume-ai");
    const secondId = server.requests[0].options.body.get("analysis_id");
    expect(secondId).not.toBe(firstId);
    expect(stream().url).toBe(`/public/slots/analysis/${secondId}/events`);

    await server.answer({ status: "ok", success: true, data: { candidate_name: "Ravi" }, analysis: RTX });
    expect(await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)).toBeTruthy();
  });

  it("names no node when the request never got an answer", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    const { container } = render(<ResumeAutoFill onExtracted={() => {}} />);
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [pdf()] } });

    expect(await screen.findByText(/^✕ Could not read after \d+\.\ds$/)).toBeTruthy();
    expect(screen.getByText("Failed to fetch")).toBeTruthy();
  });
});

describe("resume upload from the candidates table", () => {
  const CANDIDATE = { id: "cand-1", name: "Ravi", resume_count: 0 };

  it("follows the node reading a PDF in the cell", async () => {
    const server = stubServer();
    const onRefresh = vi.fn(async () => {});
    const { container } = render(<ResumeCell candidate={CANDIDATE} onRefresh={onRefresh} />);
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [pdf()] } });

    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(server.requests[0].options.body.get("analysis_id")).toMatch(/^[0-9a-f]{32}$/);
    push({ state: "running", node: "RTX 4060" });
    expect(document.querySelector(".ai-node-progress--compact").textContent).toContain("● RTX 4060 · Analysing…");

    await server.answer({ status: "ok", resume: { id: "resume-1" }, analysis: RTX });
    expect(await screen.findByText(/^✓ Analysed by RTX 4060 in \d+\.\ds$/)).toBeTruthy();
    expect(onRefresh).toHaveBeenCalled();
  });

  it("shows no AI status for a document no node reads", async () => {
    const server = stubServer();
    const { container } = render(<ResumeCell candidate={CANDIDATE} onRefresh={async () => {}} />);
    const docx = new File(["PK"], "resume.docx", {
      type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    });
    fireEvent.change(container.querySelector('input[type="file"]'), { target: { files: [docx] } });

    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(server.requests[0].options.body.get("analysis_id")).toBeNull();
    expect(document.querySelector(".ai-node-progress")).toBeNull();
    expect(FakeEventSource.instances).toHaveLength(0);
    await server.answer({ status: "ok", resume: { id: "resume-1" } });
    expect(document.querySelector(".ai-node-progress")).toBeNull();
  });

  it("follows the node in the resume manager, and keeps its result when the list redraws", async () => {
    const saved = { id: "resume-0", note: "Old resume", uploaded_at: "2026-09-01T10:00:00Z" };
    const server = stubServer((url, options) =>
      url === "/candidates/cand-1" && !options.method
        ? { status: "ok", candidate: { ...CANDIDATE, resume_count: 1, resumes: [saved] } }
        : null,
    );
    render(<ResumeCell candidate={{ ...CANDIDATE, resume_count: 1, resumes: [saved] }} onRefresh={async () => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /1 resume/ }));

    const uploadButton = await screen.findByRole("button", { name: "Upload new resume" });
    const input = uploadButton.parentElement.querySelector('input[type="file"]');
    Object.defineProperty(input, "files", { value: [pdf()], configurable: true });
    await act(async () => { input.onchange(); });

    await waitFor(() => expect(server.requests).toHaveLength(1));
    expect(await screen.findByText("Waiting for AI node…")).toBeTruthy();
    push({ state: "running", node: "Praveen" });
    await waitFor(() =>
      expect(document.querySelector(".cand-resume-ai-progress").textContent).toContain("● Praveen · Analysing…"),
    );

    await server.answer({ status: "ok", resume: { id: "resume-1" }, analysis: { ...RTX, node: "Praveen", analysed_by: ["Praveen"] } });
    // The list is fetched again and redrawn after the upload; the finished
    // line is carried into the redrawn modal rather than lost with it.
    const detailLoads = () => fetch.mock.calls.filter(([url, options]) => url === "/candidates/cand-1" && !options?.method);
    await waitFor(() => expect(detailLoads()).toHaveLength(2));
    await waitFor(() =>
      expect(document.querySelector(".cand-resume-ai-progress")?.textContent).toMatch(/^✓ Analysed by Praveen in \d+\.\ds$/),
    );

    fireEvent.click(document.querySelector(".cand-resume-manager .cand-modal-close"));
    expect(document.querySelector(".cand-resume-manager")).toBeNull();
  });
});
