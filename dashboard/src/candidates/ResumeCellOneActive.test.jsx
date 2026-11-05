/**
 * One resume per candidate in the Candidates table.
 *
 * The cell used to count every slot row's records ("10 resumes"), and later
 * carried View, Download and Update. Since 5 Nov it is a summary -- "Saved"
 * for the one active resume the server names, or "None" -- and the row
 * opens Edit, whose resume bar has View, Download and Update.
 */
import React from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResumeAutoFill, ResumeSummary } from "./candidatesModule.jsx";

const ACTIVE = { id: "cv9", original_name: "Asha_Fakename_2026.pdf", uploaded_at: "2026-10-01T10:00:00+00:00" };
const CANDIDATE = { id: "cand-7", name: "Asha Fakename", resume_count: 1, active_resume: ACTIVE,
  resumes: [ACTIVE, { id: "cv1", original_name: "old.pdf" }, { id: "cv2", original_name: "lost.pdf" }] };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the table's Resume cell", () => {
  it("names the one active resume, never a count, and has nothing to press", () => {
    const { container } = render(<ResumeSummary candidate={CANDIDATE} />);
    expect(container.textContent).toBe("Saved");
    expect(container.querySelector(".cand-resume-status--on").getAttribute("title")).toBe("Asha_Fakename_2026.pdf");
    expect(within(container).queryByRole("button")).toBeNull();
    expect(within(container).queryByRole("link")).toBeNull();
  });

  it("says None when no file is stored, even when records of lost files remain", () => {
    const { container } = render(<ResumeSummary candidate={{ ...CANDIDATE, resume_count: 0, active_resume: null }} />);
    expect(container.textContent).toBe("None");
    expect(within(container).queryByRole("button")).toBeNull();
  });
});

describe("the Edit dialog's resume bar", () => {
  it("has View, Download and Update for that one resume, and View/Download fetch nothing", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<ResumeAutoFill candidateId="cand-7" savedResume={ACTIVE} onReading={() => ({ filled: [] })} />);
    const view = screen.getByRole("link", { name: "View resume Asha_Fakename_2026.pdf" });
    expect(view.getAttribute("href")).toMatch(/\/candidates\/cand-7\/resumes\/cv9\/preview$/);
    expect(view).toHaveAttribute("target", "_blank");
    const download = screen.getByRole("link", { name: "Download resume Asha_Fakename_2026.pdf" });
    expect(download.getAttribute("href")).toMatch(/\/candidates\/cand-7\/resumes\/cv9$/);
    expect(download).toHaveAttribute("download", "Asha_Fakename_2026.pdf");
    expect(screen.getByRole("button", { name: /Upload Resume/ })).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
  });
});
