/**
 * Candidates page to the owner's mockup (25 Nov): + Add in the shell header,
 * CSV and Expenses in a ••• More menu, dates named for what they are, "No
 * resume", six-row phone cards, and no grey blocks when a card is tapped.
 */
import React from "react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CandidatesMoreMenu, HeaderAction } from "./CandidatesMoreMenu.jsx";
import { recordDateOf } from "./candidatesModule.jsx";

const CSS = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "index.css"), "utf-8").replace(/\r\n/g, "\n");
const BLOCK = CSS.slice(CSS.indexOf("Candidates page to the owner's mockup (25 Nov)"));
const PHONE = BLOCK.slice(BLOCK.indexOf("@media (max-width: 599px)"));

afterEach(() => {
  cleanup();
  document.getElementById("page-header-actions")?.remove();
});

describe("dates are named for what they are", () => {
  it("calls the logged day Added, falling back to when the row was created", () => {
    expect(recordDateOf({ logged_date: "2026-11-22", date: "2026-11-24" })).toMatchObject({ label: "Added", value: "2026-11-22" });
    expect(recordDateOf({ created_at: "2026-11-20T10:00:00Z" })).toMatchObject({ label: "Added" });
  });

  it("only says Date for an old row that has nothing but the booking day", () => {
    expect(recordDateOf({ date: "2026-11-24" })).toMatchObject({ label: "Date", value: "2026-11-24" });
    expect(recordDateOf({})).toBeNull();
  });
});

describe("+ Add in the header", () => {
  it("goes into the shell header's slot when there is one", () => {
    const slot = document.createElement("div");
    slot.id = "page-header-actions";
    document.body.appendChild(slot);
    render(<div className="toolbar"><HeaderAction><button type="button">Add</button></HeaderAction></div>);
    expect(slot.querySelector("button")?.textContent).toBe("Add");
    expect(document.querySelector(".toolbar button")).toBeNull();
  });

  it("stays where it is written when the page runs on its own", () => {
    render(<div className="toolbar"><HeaderAction><button type="button">Add</button></HeaderAction></div>);
    expect(document.querySelector(".toolbar button")?.textContent).toBe("Add");
  });
});

describe("the More menu", () => {
  it("runs each action once and closes; Escape closes it and returns focus", () => {
    const csv = vi.fn();
    render(<CandidatesMoreMenu items={[{ key: "csv", icon: "download", label: "Download active CSV", ariaLabel: "Download active CSV", onSelect: csv }, false]} />);
    const more = screen.getByRole("button", { name: "More actions" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(more);
    expect(screen.getAllByRole("menuitem")).toHaveLength(1);
    fireEvent.click(screen.getByRole("menuitem", { name: "Download active CSV" }));
    expect(csv).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    fireEvent.click(more);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(document.activeElement).toBe(more);
  });
});

describe("the phone card stylesheet", () => {
  it("uses 16px page padding, 14px card padding and radius, and 12px gaps", () => {
    expect(PHONE).toMatch(/\.business-body:has\(> \.cand-page--candidates\) \{ padding: 12px 16px 24px; \}/);
    expect(PHONE).toMatch(/tbody \{ gap: 12px !important; \}/);
    expect(PHONE).toMatch(/padding: 14px !important;\s*border-radius: 14px !important;/);
  });

  it("lays each card out in the mockup's six rows", () => {
    expect(PHONE).toMatch(/"name stage"\s*"phone ref"\s*"tech tech"\s*"service added"\s*"interview resume"\s*"pay pay"/);
  });

  it("keeps every control at least 44px tall", () => {
    expect(BLOCK).toMatch(/\.cand-bar__more \{ height: 44px; min-height: 44px;/);
    expect(BLOCK).toMatch(/\.cand-tabs__btn \{\s*flex: 0 1 auto; min-height: 44px;/);
    expect(BLOCK).toMatch(/\.cand-more__item \{[^}]*min-height: 44px;/);
  });

  it("never shades a tapped card cell by cell, and leaves selection and the focus ring alone", () => {
    expect(PHONE).toMatch(/tr\.cand-row:hover > td,\s*\.cand-page\.cand-page--candidates \.cand-table tbody tr\.cand-row:active > td \{ box-shadow: none !important; \}/);
    expect(PHONE).toMatch(/tr\.cand-row:focus-visible \{ outline: 2px solid/);
    expect(BLOCK).not.toMatch(/user-select:\s*none/);
  });
});
