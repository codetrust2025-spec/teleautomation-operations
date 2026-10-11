/**
 * One resume per candidate in the Candidates table.
 *
 * The cell used to say "2 resumes", "3 resumes", "10 resumes" -- the records of
 * every slot row added up, lost files and copies included -- and opened a list
 * of versions. It now shows the one active resume the server names (the newest
 * whose file is stored) with View, Download and Update, or "Upload resume".
 */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResumeCell } from "./candidatesModule.jsx";

const ACTIVE = { id: "cv9", original_name: "Asha_Fakename_2026.pdf", uploaded_at: "2026-10-01T10:00:00+00:00" };
const CANDIDATE = { id: "cand-7", name: "Asha Fakename", resume_count: 1, active_resume: ACTIVE,
  resumes: [ACTIVE, { id: "cv1", original_name: "old.pdf" }, { id: "cv2", original_name: "lost.pdf" }] };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("a candidate with a resume", () => {
  it("shows that one resume, with View, Download and Update -- never a count", () => {
    const { container } = render(<ResumeCell candidate={CANDIDATE} onRefresh={vi.fn()} />);
    const view = screen.getByRole("link", { name: "View resume Asha_Fakename_2026.pdf" });
    expect(view.getAttribute("href")).toMatch(/\/candidates\/cand-7\/resumes\/cv9\/preview$/);
    expect(view).toHaveAttribute("target", "_blank");
    const download = screen.getByRole("link", { name: "Download resume Asha_Fakename_2026.pdf" });
    expect(download.getAttribute("href")).toMatch(/\/candidates\/cand-7\/resumes\/cv9$/);
    expect(download).toHaveAttribute("download", "Asha_Fakename_2026.pdf");
    expect(screen.getByRole("button", { name: "Update resume Asha_Fakename_2026.pdf" })).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\d+ resumes?/);
    expect(screen.queryByRole("button", { name: /Upload resume/ })).toBeNull();
  });

  it("View and Download change nothing and open no list of versions", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<ResumeCell candidate={CANDIDATE} onRefresh={vi.fn()} />);
    fireEvent.click(screen.getByRole("link", { name: /View resume/ }));
    fireEvent.click(screen.getByRole("link", { name: /Download resume/ }));
    expect(fetch).not.toHaveBeenCalled();
    expect(document.querySelector(".cand-resume-manager")).toBeNull();
  });

  it("Update uploads the newer file, which replaces it, and refreshes the row", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ status: "ok", resume: { id: "cv10" } }) })));
    const onRefresh = vi.fn(async () => {});
    const { container } = render(<ResumeCell candidate={CANDIDATE} onRefresh={onRefresh} />);
    const input = container.querySelector('input[type="file"]');
    const pick = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", { name: /Update resume/ }));
    expect(pick).toHaveBeenCalled();
    fireEvent.change(input, { target: { files: [new File(["x"], "new.docx", { type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document" })] } });
    await waitFor(() => expect(onRefresh).toHaveBeenCalled());
    expect(fetch.mock.calls[0][0]).toBe("/candidates/cand-7/resumes");
    expect(fetch.mock.calls[0][1].method).toBe("POST");
  });
});

describe("a candidate without a stored resume", () => {
  it("offers Upload resume only, even when records of lost files remain", () => {
    render(<ResumeCell candidate={{ ...CANDIDATE, resume_count: 0, active_resume: null }} onRefresh={vi.fn()} />);
    const cell = document.querySelector(".cand-resume-cell-react");
    expect(within(cell).getAllByRole("button").map((b) => b.textContent)).toEqual(["Upload resume"]);
    expect(within(cell).queryByRole("link")).toBeNull();
  });
});
