/**
 * The body production actually stores, not a tidy fixture.
 *
 * The first pass was tested against hand-written HTML with <p> and <b>. What
 * `mailbox_messages.body_text` holds for this mail is 644 characters on a
 * single line with no newline anywhere, and a Teams URL split across four
 * fragments by the sending client's 76-column wrap, the breaks turned into
 * spaces by the text extraction.
 *
 * Read verbatim from production, message f8ae1940-c841-40ab-94a2-f2da38586379.
 */
import React from "react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { OriginalEmail, rejoinWrappedUrl } from "./OriginalEmail.jsx";
import STORED_HTML from "./__fixtures__/wrappedInterviewEmail.html?raw";

const STORED_BODY = "Dear Nitin, As discussed, your Virtual Interview is scheduled at 3.30 PM - 4.15 PM on 15th Sep 2026 (Tuesday). Please join the link before 5 minutes. Interview Scheduled 15th Sep 2026 (Tuesday) at 3.30 PM. Interview link https://teams.microsoft.com/l/meetup-join/19%3ameeting_U3ludGhldGljVGVzdE1lZ XRpbmdJZE5vdEFSZWFsTWVldGlu%40thread.v2/0?context=%7b%22Tid%22%3a%2200000000 -1111-4222-8333-444444444444%22%2c%22Oid%22%3a%2200000000-5555-4666-8777-888 888888888%22%7d Thanks & Regards Anita Raghavan HR Technical Recruiter || 9000000109 Example Staffing Services Pvt Ltd 1 Example Street, Test Layout, Bengaluru 560001.";

const WHOLE_URL = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_U3ludGhldGljVGVzdE1lZXRpbmdJZE5vdEFSZWFsTWVldGlu%40thread.v2/0?context=%7b%22Tid%22%3a%2200000000-1111-4222-8333-444444444444%22%2c%22Oid%22%3a%2200000000-5555-4666-8777-888888888888%22%7d";

function view(body = STORED_BODY) {
  return render(<OriginalEmail
    email={{
      subject: "Virtual Technical Interview Scheduled_TCS",
      sender_name: "anita.raghavan",
      recipient_email: "nitin.deshmukh@example.com",
      sent_at: "2026-09-11T07:22:01Z", body,
    }}
    formatWhen={() => "11 Sept 2026, 12:52 pm"}
  />);
}

describe("the body production actually stores", () => {
  afterEach(cleanup);

  it("has no newline at all, which is why paragraphs cannot be recovered", () => {
    // Stated as a fact about the input, so the day ingestion starts keeping
    // newlines this fails and the renderer can be given real paragraphs.
    expect(STORED_BODY).not.toContain("\n");
  });

  it("no longer leaves the tracking string on screen", () => {
    const { container } = view();
    const shown = container.querySelector(".gmail-view__body").textContent;
    for (const fragment of ["%40thread.v2", "%22Tid%22", "%22Oid%22", "context="]) {
      expect(shown).not.toContain(fragment);
    }
  });

  it("gives the link the whole URL, not the first fragment", () => {
    // The visible defect was the raw tail. The worse one was silent: the href
    // stopped at the first space, so the link did not open the meeting.
    view();
    const link = screen.getByRole("link", { name: "Join Microsoft Teams meeting" });
    expect(link).toHaveAttribute("href", WHOLE_URL);
  });

  it("keeps the sentence after the link out of the URL", () => {
    const { container } = view();
    expect(container.textContent).toContain("Thanks & Regards");
    expect(screen.getByRole("link", { name: "Join Microsoft Teams meeting" })
      .getAttribute("href")).not.toContain("Thanks");
  });

  it("still links the recruiter's number", () => {
    view();
    expect(screen.getByRole("link", { name: "9000000109" }))
      .toHaveAttribute("href", "tel:9000000109");
  });

  it("renders it as one paragraph, because that is what was stored", () => {
    const { container } = view();
    expect(container.querySelectorAll(".gmail-view__body p")).toHaveLength(1);
  });
});

describe("rejoining a wrapped URL", () => {
  const join = (text) => {
    const m = /https?:\/\/\S+/.exec(text);
    return rejoinWrappedUrl(text, m.index, m[0]).url;
  };

  it("absorbs only fragments carrying a percent-escape", () => {
    expect(join("go https://x.example/a%20b c%2Fd then Thanks & Regards"))
      .toBe("https://x.example/a%20bc%2Fd");
  });

  it("stops at ordinary prose", () => {
    expect(join("see https://x.example/path Thanks & Regards Anita"))
      .toBe("https://x.example/path");
  });

  it("stops at a following sentence even when it has an ampersand", () => {
    expect(join("link https://x.example/a Terms & Conditions apply"))
      .toBe("https://x.example/a");
  });

  it("leaves an unwrapped URL exactly as it was", () => {
    expect(join("https://teams.microsoft.com/l/meetup-join/abc%40thread.v2"))
      .toBe("https://teams.microsoft.com/l/meetup-join/abc%40thread.v2");
  });
});


describe("the stored HTML, which is what the sender actually wrote", () => {
  afterEach(cleanup);

  function html(overrides = {}) {
    return render(<OriginalEmail
      email={{
        subject: "Virtual Technical Interview Scheduled_TCS",
        sender_name: "anita.raghavan",
        recipient_email: "nitin.deshmukh@example.com",
        sent_at: "2026-09-11T07:22:01Z",
        body: STORED_BODY, body_html: STORED_HTML, ...overrides,
      }}
      formatWhen={() => "11 Sept 2026, 12:52 pm"}
    />);
  }

  it("prefers the HTML over the flattened text", () => {
    const { container } = html();
    // The flattened body renders as one paragraph; the real mail has several.
    expect(container.querySelectorAll(".gmail-view__body p").length).toBeGreaterThan(3);
  });

  it("recovers the paragraphs the sender wrote", () => {
    const { container } = html();
    const paragraphs = [...container.querySelectorAll(".gmail-view__body p")]
      .map((n) => n.textContent.trim());
    expect(paragraphs[0]).toContain("Dear Nitin");
    expect(paragraphs.some((p) => p.startsWith("Interview Scheduled"))).toBe(true);
    expect(paragraphs.some((p) => p.includes("Thanks"))).toBe(true);
  });

  it("still links the number, redacted in this fixture", () => {
    const { container } = html();
    const tel = [...container.querySelectorAll("a")]
      .find((a) => (a.getAttribute("href") || "").startsWith("tel:"));
    expect(tel).toBeTruthy();
  });

  it("recovers the bold the recruiter used", () => {
    const { container } = html();
    const bold = [...container.querySelectorAll(".gmail-view__body strong")]
      .map((n) => n.textContent).join(" | ");
    expect(bold).toContain("Dear Nitin");
    expect(bold).toContain("4.15 PM");
    expect(bold).toContain("Interview Scheduled");
  });

  it("drops Word's empty spacer paragraphs", () => {
    const { container } = html();
    for (const p of container.querySelectorAll(".gmail-view__body p")) {
      expect(p.textContent.trim()).not.toBe("");
    }
  });

  it("drops Word's style and office markup entirely", () => {
    const { container } = html();
    expect(container.querySelector("style")).toBeNull();
    const shown = container.querySelector(".gmail-view__body").textContent;
    expect(shown).not.toContain("MsoNormal");
    expect(shown).not.toContain("font-family");
    expect(shown).not.toContain("panose");
  });

  it("gives the link the whole href, unwrapped, from the anchor itself", () => {
    const { container } = html();
    const link = [...container.querySelectorAll(".gmail-view__body a")]
      .find((a) => /teams\.microsoft\.com/.test(a.getAttribute("href") || ""));
    expect(link).toBeTruthy();
    expect(link.textContent).toBe("Join Microsoft Teams meeting");
    expect(link.getAttribute("href")).toContain("%40thread.v2");
    expect(link.getAttribute("href")).not.toMatch(/\s/);
  });

  it("shows no tracking string anywhere on screen", () => {
    const { container } = html();
    const shown = container.querySelector(".gmail-view__body").textContent;
    for (const fragment of ["%40thread.v2", "%22Tid%22", "context="]) {
      expect(shown).not.toContain(fragment);
    }
  });

  it("falls back to the flattened text when the HTML is absent", () => {
    const { container } = html({ body_html: "" });
    expect(container.querySelectorAll(".gmail-view__body p")).toHaveLength(1);
  });
});
