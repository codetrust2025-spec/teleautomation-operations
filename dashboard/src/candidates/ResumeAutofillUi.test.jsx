/**
 * Resume in Candidate Edit: Upload, View and Download are separate, and a
 * reading never silently replaces what the form holds.
 *
 * Only an EMPTY field is filled: for an existing candidate, what the server
 * saved (features/resume_autofill: an AI reading at sufficient confidence, a
 * matching name, never another candidate's phone or email); for a new
 * candidate, a confident reading. Every other difference -- a typed value, a
 * cut-short name, an incomplete number, another person's phone -- is shown as
 * Existing -> Resume to accept or keep field by field, with a warning at the
 * field (11 Oct). A blank on the resume never replaces anything.
 */
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CandidateEditModal } from "./candidatesModule.jsx";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";

const CANDIDATE = {
  id: "cand-rs-1",
  name: "Asha Rao",
  phone: "9000000901",
  email: "",
  technology: "Salesforce",
  reference: "Test Owner",
  stage: "in_progress",
  service_type: "profile_service",
  ctc_percentage: 10,
  expected_payment: 20000,
  payment: 0,
  payment_proofs: [],
};

const RESUME_READING = {
  is_resume: true,
  candidate_name: "Asha Rao",
  phone: "9000000999",
  email: "asha.rao@example.test",
  technology: "ServiceNow",
  confidence_score: 90,
  extraction_source: "pdf_text_ai",
};

const SAVED = {
  id: "r0",
  original_name: "Asha_Rao_Resume.pdf",
  size: 1258291,
  uploaded_at: "2026-09-25T06:00:00+00:00",
};

/** Every resume request answers `payload`; `hold` keeps it until released. */
function mockResumeUpload(payload, { hold = false } = {}) {
  const control = { release: null };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url) => {
      if (String(url).includes("resume")) {
        if (hold) await new Promise((resolve) => { control.release = resolve; });
        return { ok: true, status: 200, json: async () => payload };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    }),
  );
  return control;
}

function renderModal(candidate = {}) {
  return render(
    <ConfirmProvider>
      <CandidateEditModal
        initial={candidate === null ? null : { ...CANDIDATE, ...candidate }}
        onClose={vi.fn()}
        onSave={vi.fn()}
        isAdmin={true}
      />
    </ConfirmProvider>,
  );
}

const field = (label) => screen.getByText(label, { selector: ".cand-field-label" }).closest("label").querySelector("input, select");
const resumeInput = (container) =>
  Array.from(container.querySelectorAll('input[type="file"]')).find((n) => (n.getAttribute("accept") || "").includes("pdf"));

async function uploadResume(container, name = "resume.pdf") {
  const file = new File(["%PDF"], name, { type: "application/pdf" });
  fireEvent.change(resumeInput(container), { target: { files: [file] } });
}

/** The read is over: the upload button is back. */
const readingDone = () => screen.findByRole("button", { name: "Upload Resume (PDF)" });

beforeEach(() => {
  vi.stubGlobal("URL", { ...URL, createObjectURL: vi.fn(() => "blob:r"), revokeObjectURL: vi.fn() });
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("Upload and View are separate", () => {
  it("shows the saved resume with its size and date, and View opens it in a new tab", () => {
    renderModal({ resumes: [SAVED], resume_count: 1 });
    expect(screen.getByRole("button", { name: "Upload Resume (PDF)" })).toBeInTheDocument();
    expect(screen.getByText("Asha_Rao_Resume.pdf")).toBeInTheDocument();
    expect(screen.getByText("1.2 MB • Uploaded on 25 Sep 2026")).toBeInTheDocument();
    const view = screen.getByRole("link", { name: /View resume/ });
    expect(view.getAttribute("href")).toMatch(/\/candidates\/cand-rs-1\/resumes\/r0\/preview$/);
    expect(view).toHaveAttribute("target", "_blank");
    expect(view.getAttribute("rel")).toContain("noopener");
  });

  it("Download, beside View, saves the stored file under its uploaded name", () => {
    renderModal({ resumes: [{ ...SAVED, note: "Latest CV" }] });
    const download = screen.getByRole("link", { name: /Download resume/ });
    // The file itself (served as an attachment), not the preview page.
    expect(download.getAttribute("href")).toMatch(/\/candidates\/cand-rs-1\/resumes\/r0$/);
    expect(download).toHaveAttribute("download", "Asha_Rao_Resume.pdf");
    expect(download.previousElementSibling).toBe(screen.getByRole("link", { name: /View resume/ }));
  });

  it("View and Download send nothing and change nothing", () => {
    vi.stubGlobal("fetch", vi.fn());
    renderModal({ resumes: [SAVED] });
    fireEvent.click(screen.getByRole("link", { name: /View resume/ }));
    fireEvent.click(screen.getByRole("link", { name: /Download resume/ }));
    expect(fetch).not.toHaveBeenCalled();
    expect(field("Email")).toHaveValue("");
  });

  it("shows the newest of several saved resumes", () => {
    renderModal({ resumes: [SAVED, { ...SAVED, id: "r9", original_name: "Newer.pdf", uploaded_at: "2026-10-01T06:00:00+00:00" }] });
    expect(screen.getByText("Newer.pdf")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View resume/ }).getAttribute("href")).toMatch(/resumes\/r9\/preview$/);
  });

  it("offers no View when there is no saved resume", () => {
    renderModal({ resumes: [] });
    expect(screen.queryByRole("link", { name: /View resume/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Download resume/ })).toBeNull();
    expect(screen.getByText(/Upload to auto-fill name, phone, email and technology/)).toBeInTheDocument();
  });

  it("a newly uploaded resume becomes the one View opens", async () => {
    mockResumeUpload({
      status: "ok", resume: { ...SAVED, id: "r2", original_name: "Fresh.pdf" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal({ resumes: [SAVED] });
    await uploadResume(container, "Fresh.pdf");
    expect(await screen.findByText("Fresh.pdf")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /View resume/ }).getAttribute("href")).toMatch(/resumes\/r2\/preview$/);
    const download = screen.getByRole("link", { name: /Download resume/ });
    expect(download.getAttribute("href")).toMatch(/resumes\/r2$/);
    expect(download).toHaveAttribute("download", "Fresh.pdf");
  });
});

const row = (key) => document.querySelector(`[data-compare="${key}"]`);
const accept = (key) => fireEvent.click(row(key).querySelector(".cand-btn--primary"));
const keep = (key) => fireEvent.click(row(key).querySelector(".cand-btn--ghost"));
const warning = (key) => document.querySelector(`[data-field-warning="${key}"]`)?.textContent || "";

describe("an existing candidate: only an empty field is filled; every other difference is offered", () => {
  it("mirrors the empty field the server filled, and offers the differing phone and technology", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: { ...CANDIDATE, email: "asha.rao@example.test" },
      ai_extraction: RESUME_READING, autofill: { filled: { email: "asha.rao@example.test" }, skipped: {}, refused: "" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    await waitFor(() => expect(field("Email")).toHaveValue("asha.rao@example.test"));
    expect(screen.getByText("Filled from this resume: email. Existing details were kept.")).toBeInTheDocument();
    // Nothing else changed by itself...
    expect(field("Phone")).toHaveValue("9000000901");
    expect(field("Technology")).toHaveValue("Salesforce");
    // ...but each difference is shown Existing -> Resume, with a warning at the field.
    expect(row("phone").textContent).toContain("9000000901 → 9000000999");
    expect(row("technology").textContent).toContain("Salesforce → ServiceNow");
    expect(row("name")).toBeNull();
    expect(row("email")).toBeNull();
    expect(warning("phone")).toBe("Resume: 9000000999");
  });

  it("Accept takes the resume's value into the form; Keep leaves the form as it is", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal({ email: "asha@example.test" });
    await uploadResume(container);
    await waitFor(() => expect(row("phone")).not.toBeNull());
    accept("technology");
    expect(field("Technology")).toHaveValue("ServiceNow");
    keep("phone");
    expect(field("Phone")).toHaveValue("9000000901");
    keep("email");
    expect(field("Email")).toHaveValue("asha@example.test");
    expect(document.querySelector(".cand-resume-compare")).toBeNull();
    expect(warning("phone")).toBe("");
  });

  it("never overwrites what was typed while the resume was being read", async () => {
    const upload = mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: { email: "asha.rao@example.test" }, skipped: {}, refused: "" },
    }, { hold: true });
    const { container } = renderModal();
    await uploadResume(container);
    fireEvent.change(field("Email"), { target: { value: "typed@example.test" } });
    await waitFor(() => expect(upload.release).toBeTypeOf("function"));
    upload.release();
    await waitFor(() => expect(row("email")).not.toBeNull());
    expect(field("Email")).toHaveValue("typed@example.test");
    expect(row("email").textContent).toContain("typed@example.test → asha.rao@example.test");
  });

  it("a cut-short name and an incomplete phone are offered, not replaced", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE,
      ai_extraction: { ...RESUME_READING, candidate_name: "Asha Raghavendra", phone: "+91 90000 00901" },
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal({ name: "Asha Ragha", phone: "900000090", email: "asha@example.test" });
    expect(warning("phone")).toBe("Not a full 10-digit mobile number");
    await uploadResume(container);
    await waitFor(() => expect(row("name")).not.toBeNull());
    expect(row("name").textContent).toContain("Looks cut short");
    expect(row("phone").textContent).toContain("Incomplete number");
    expect(field("Candidate name *")).toHaveValue("Asha Ragha");
    accept("name");
    accept("phone");
    expect(field("Candidate name *")).toHaveValue("Asha Raghavendra");
    expect(field("Phone")).toHaveValue("9000000901");
  });

  it("the same name in another order is not offered", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: { ...RESUME_READING, candidate_name: "Rao Asha" },
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal({ email: "asha.rao@example.test", phone: "9000000999", technology: "ServiceNow" });
    await uploadResume(container);
    await readingDone();
    expect(document.querySelector(".cand-resume-compare")).toBeNull();
    expect(field("Candidate name *")).toHaveValue("Asha Rao");
  });

  it("says why nothing was filled when the server refused the reading, and offers nothing", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "the name on the resume does not match this candidate" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    expect(await screen.findByText(/Not filled automatically: the name on the resume does not match this candidate/)).toBeInTheDocument();
    expect(field("Email")).toHaveValue("");
    expect(document.querySelector(".cand-resume-compare")).toBeNull();
  });

  it("a resume whose phone differs fills nothing but is compared field by field", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "", compare_only: "the phone number on the resume differs from this candidate's" },
    });
    const { container } = renderModal();
    await uploadResume(container);
    expect(await screen.findByText("Nothing was filled: the phone number on the resume differs from this candidate's.")).toBeInTheDocument();
    expect(field("Email")).toHaveValue("");
    expect(row("email").textContent).toContain("empty → asha.rao@example.test");
    expect(row("phone")).not.toBeNull();
  });

  it("does not offer a value the server would not fill, and says why", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: { phone: "that number belongs to another candidate" }, refused: "" },
    });
    const { container } = renderModal({ phone: "", email: "asha.rao@example.test", technology: "ServiceNow" });
    await uploadResume(container);
    expect(await screen.findByText("Not filled: phone (that number belongs to another candidate).")).toBeInTheDocument();
    expect(row("phone")).toBeNull();
    expect(field("Phone")).toHaveValue("");
  });

  it("stays quiet when the resume agrees with the form", async () => {
    mockResumeUpload({
      status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING,
      autofill: { filled: {}, skipped: {}, refused: "" },
    });
    const { container } = renderModal({ email: "asha.rao@example.test", phone: "9000000999", technology: "ServiceNow" });
    await uploadResume(container);
    await readingDone();
    expect(screen.queryByText(/Filled from this resume/)).toBeNull();
    expect(document.querySelector(".cand-resume-compare")).toBeNull();
  });

  it("a reading the server could not judge is only compared, including empty fields", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: CANDIDATE, ai_extraction: RESUME_READING });
    const { container } = renderModal();
    await uploadResume(container);
    await waitFor(() => expect(row("email")).not.toBeNull());
    expect(field("Email")).toHaveValue("");
    accept("email");
    expect(field("Email")).toHaveValue("asha.rao@example.test");
  });
});

describe("the field-level warnings", () => {
  it("names a phone or email that is another person's", () => {
    renderModal({ identity_warnings: { phone: "Also the phone of Farha Sample (13 records)", email: "Also the email of Farha Sample" }, email: "x@example.test" });
    expect(warning("phone")).toBe("Also the phone of Farha Sample (13 records)");
    expect(warning("email")).toBe("Also the email of Farha Sample");
    expect(field("Phone").className).toContain("cand-input--warn");
  });

  it("goes when the person changes the value", () => {
    renderModal({ identity_warnings: { phone: "Also the phone of Farha Sample (13 records)" } });
    fireEvent.change(field("Phone"), { target: { value: "9000000777" } });
    expect(warning("phone")).toBe("");
  });

  it("says an incomplete phone or an invalid email is one", () => {
    renderModal({ phone: "77995", email: "asha@example" });
    expect(warning("phone")).toBe("Not a full 10-digit mobile number");
    expect(warning("email")).toBe("Not a valid email");
  });
});

describe("a new candidate: a confident reading fills the empty fields", () => {
  const reading = (data) => ({ status: "ok", success: true, data: { ...RESUME_READING, ...data } });

  it("fills name, phone, email and technology, with a note", async () => {
    mockResumeUpload(reading({ phone: "+91 90000 00999", technology: "React.js" }));
    const { container } = renderModal(null);
    await uploadResume(container, "Asha.pdf");
    await waitFor(() => expect(field("Candidate name *")).toHaveValue("Asha Rao"));
    expect(field("Phone")).toHaveValue("9000000999");
    expect(field("Email")).toHaveValue("asha.rao@example.test");
    expect(field("Technology")).toHaveValue("React JS");
    expect(screen.getByText("Filled from this resume: name, phone, email, technology.")).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => String(url).endsWith("/public/slots/extract-resume-ai"))).toBe(true);
    // Read, not saved: a new candidate has no row to file it on, so no View or Download.
    expect(screen.getByText("Asha.pdf")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /View resume/ })).toBeNull();
    expect(screen.queryByRole("link", { name: /Download resume/ })).toBeNull();
  });

  it("offers, rather than replaces, what was typed", async () => {
    const upload = mockResumeUpload(reading({}), { hold: true });
    const { container } = renderModal(null);
    await uploadResume(container);
    fireEvent.change(field("Candidate name *"), { target: { value: "Asha Raghavan" } });
    fireEvent.change(field("Phone"), { target: { value: "77995" } });
    await waitFor(() => expect(upload.release).toBeTypeOf("function"));
    upload.release();
    await waitFor(() => expect(row("name")).not.toBeNull());
    expect(field("Candidate name *")).toHaveValue("Asha Raghavan");
    expect(field("Phone")).toHaveValue("77995");
    expect(field("Email")).toHaveValue("asha.rao@example.test");
    accept("phone");
    expect(field("Phone")).toHaveValue("9000000999");
  });

  it("does not use an incomplete name or phone from the reading", async () => {
    mockResumeUpload(reading({ candidate_name: "Asha R", phone: "+91 90000" }));
    const { container } = renderModal(null);
    await uploadResume(container);
    await waitFor(() => expect(field("Email")).toHaveValue("asha.rao@example.test"));
    expect(field("Candidate name *")).toHaveValue("");
    expect(field("Phone")).toHaveValue("");
    expect(row("name")).toBeNull();
    expect(row("phone")).toBeNull();
  });

  it("a low-confidence reading is only compared until accepted", async () => {
    mockResumeUpload(reading({ confidence_score: 40 }));
    const { container } = renderModal(null);
    await uploadResume(container);
    await waitFor(() => expect(row("name")).not.toBeNull());
    expect(screen.getByText(/Low-confidence reading/)).toBeInTheDocument();
    expect(field("Candidate name *")).toHaveValue("");
    accept("name");
    expect(field("Candidate name *")).toHaveValue("Asha Rao");
  });

  it("an unreadable file says so and fills nothing", async () => {
    mockResumeUpload({ status: "error", data: { error: "Not a resume" } });
    const { container } = renderModal(null);
    await uploadResume(container);
    expect(await screen.findByText("Not a resume")).toBeInTheDocument();
    expect(field("Candidate name *")).toHaveValue("");
  });
});

describe("the reported case: phone, email and technology differ from the resume (invented values)", () => {
  // The resume reads "Raghavendrarao Kolla", 90000 00777, raghav.k@, React.js. The
  // record holds another person's phone, a shortened email and another technology.
  const READ = {
    is_resume: true, candidate_name: "Raghavendrarao Kolla", phone: "+919000000777",
    email: "raghav.k@example.test", technology: "React.js", confidence_score: 85, extraction_source: "pdf_text_ai",
  };
  const ROW = {
    ...CANDIDATE, name: "Raghavendrarao Kolla", phone: "9000000998", email: "raghav@example.test", technology: "SAP Sales",
    follow_up: "Balance after the offer",
    identity_warnings: { phone: "Also the phone of Farha Sample (13 records)" },
  };

  it("shows each difference with a warning, and Save sends exactly what was accepted or kept", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: ROW, ai_extraction: READ,
      autofill: { filled: {}, skipped: {}, refused: "", compare_only: "the phone number on the resume differs from this candidate's" } });
    const onSave = vi.fn(async () => {});
    const { container } = render(
      <ConfirmProvider>
        <CandidateEditModal initial={ROW} onClose={vi.fn()} onSave={onSave} isAdmin={true} />
      </ConfirmProvider>,
    );
    expect(warning("phone")).toBe("Also the phone of Farha Sample (13 records)");
    await uploadResume(container);
    await waitFor(() => expect(row("phone")).not.toBeNull());
    expect(row("phone").textContent).toContain("9000000998 → 9000000777");
    expect(row("email").textContent).toContain("raghav@example.test → raghav.k@example.test");
    expect(row("technology").textContent).toContain("SAP Sales → React JS");
    expect(row("name")).toBeNull();
    accept("phone");
    keep("email");
    accept("technology");
    expect(warning("phone")).toBe("");
    fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const sent = onSave.mock.calls[0][0];
    expect([sent.name, sent.phone, sent.email, sent.technology]).toEqual(
      ["Raghavendrarao Kolla", "9000000777", "raghav@example.test", "React JS"]);
  });
});

describe("after Save and reopen, the complete values stay; a save never cuts them short", () => {
  const READ = {
    is_resume: true, candidate_name: "Raghavendrarao Kolla", phone: "+919000000777",
    email: "raghav.k@example.test", technology: "React.js", confidence_score: 85, extraction_source: "pdf_text_ai",
  };
  const ROW = {
    ...CANDIDATE, name: "Kolla Raghava", phone: "9000000777", email: "raghav.k@example.test", technology: "React JS",
    follow_up: "Balance after the offer",
  };
  const dialogButton = (label) =>
    Array.from(document.querySelectorAll(".cm-btn")).find((b) => b.textContent.trim() === label);

  function openModal(row, onSave = vi.fn(async () => {}), onServerChange = vi.fn()) {
    const view = render(
      <ConfirmProvider>
        <CandidateEditModal initial={row} onClose={vi.fn()} onSave={onSave} onServerChange={onServerChange} isAdmin={true} />
      </ConfirmProvider>,
    );
    return { ...view, onSave, onServerChange };
  }
  const save = () => fireEvent.click(screen.getByRole("button", { name: /Save changes/ }));

  it("the completed name, the 10-digit phone, the email and the technology are what Save sends and what reopens", async () => {
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: ROW, ai_extraction: READ,
      autofill: { filled: {}, skipped: {}, refused: "" } });
    const { container, onSave, onServerChange, unmount } = openModal(ROW);
    await uploadResume(container);
    // The cut-short name is offered, not replaced; accepted, it is the complete one.
    await waitFor(() => expect(row("name")).not.toBeNull());
    expect(field("Candidate name *")).toHaveValue("Kolla Raghava");
    accept("name");
    expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla");
    expect(onServerChange).toHaveBeenCalled();          // the list behind the form is reloaded
    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const sent = onSave.mock.calls[0][0];
    expect([sent.name, sent.phone, sent.email, sent.technology]).toEqual(
      ["Raghavendrarao Kolla", "9000000777", "raghav.k@example.test", "React JS"]);
    unmount();

    // Reopened from what was saved: the same four values, and another upload changes nothing.
    mockResumeUpload({ status: "ok", resume: { id: "r1" }, candidate: ROW, ai_extraction: READ,
      autofill: { filled: {}, skipped: {}, refused: "" } });
    const reopened = openModal({ ...ROW, ...sent });
    expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla");
    expect(field("Phone")).toHaveValue("9000000777");
    expect(field("Email")).toHaveValue("raghav.k@example.test");
    expect(field("Technology")).toHaveValue("React JS");
    await uploadResume(reopened.container);
    await readingDone();
    expect(field("Candidate name *")).toHaveValue("Raghavendrarao Kolla");
    expect(screen.queryByText(/Filled from this resume/)).toBeNull();
  });

  it("a cut-short phone typed in the form is not saved", async () => {
    const { onSave } = openModal({ ...ROW, name: "Raghavendrarao Kolla" });
    fireEvent.change(field("Phone"), { target: { value: "900000077" } });
    save();
    expect(await screen.findByText("Enter the full 10-digit mobile number.")).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("a saved incomplete number left as it is can still be saved", async () => {
    const { onSave } = openModal({ ...ROW, name: "Raghavendrarao Kolla", phone: "900000077" });
    save();
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  });

  it("a name the server finds cut short is saved only after the person confirms it", async () => {
    const refusal = Object.assign(new Error("“Kolla Raghava” looks like the saved name “Raghavendrarao Kolla” cut short."), { shorterName: true });
    const onSave = vi.fn(async (payload) => { if (!payload.allow_shorter_name) throw refusal; });
    openModal({ ...ROW, name: "Kolla Raghava" }, onSave);

    save();
    expect(await screen.findByText("Save a shorter name?")).toBeInTheDocument();
    fireEvent.click(dialogButton("Keep editing"));
    expect(await screen.findByText(refusal.message)).toBeInTheDocument();
    expect(onSave).toHaveBeenCalledTimes(1);

    save();
    await screen.findByText("Save a shorter name?");
    fireEvent.click(dialogButton("Save shorter name"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(3));
    expect(onSave.mock.calls[2][0]).toMatchObject({ name: "Kolla Raghava", allow_shorter_name: true });
  });
});
