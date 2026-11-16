// Selection Related on an iPhone follows its approved mockup (16 Nov 2026):
// five counters in one row with short labels, "Search mail alerts...", and the
// phone rules scoped to Selection and to phones, so desktop and Interview
// Related are untouched (checked pixel for pixel with dashboard/visual).
import React from "react";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { ConfirmProvider } from "../context/ConfirmContext.jsx";
import { MailCategoryPage } from "./MailCategoryPage.jsx";

const here = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(here, "MailCategoryPage.css"), "utf-8").replace(/\r\n/g, "\n");

function phone(matches) {
  vi.stubGlobal("matchMedia", (query) => ({
    matches: matches && query === "(max-width: 640px)", media: query,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {},
  }));
}
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ groups: [], emails: [], notifications: [], total: 0, companies: [], summary: {} }), { status: 200, headers: { "Content-Type": "application/json" } })));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const labels = async (name) => within(await screen.findByRole("tablist", { name })).getAllByRole("tab")
  .map((t) => t.querySelector(".mcp-tile__label").textContent);

describe("Selection Related on a phone", () => {
  it("labels the counters as the mockup does and says what the search searches", async () => {
    phone(true);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    expect(await labels("Selection Related tabs")).toEqual(["Unread", "Opened", "AI Retry", "Booking", "All Alerts"]);
    expect(screen.getByLabelText("Search notifications")).toHaveAttribute("placeholder", "Search mail alerts...");
    // The full meaning stays in the tab's title.
    expect(screen.getByRole("tab", { name: /Booking/ })).toHaveAttribute("title", "Booking failed Selection alerts");
  });

  it("keeps the full wording on wider screens", async () => {
    phone(false);
    render(<ConfirmProvider><MailCategoryPage category="selection" /></ConfirmProvider>);
    expect((await labels("Selection Related tabs"))[3]).toBe("Booking failed");
    expect(screen.getByLabelText("Search notifications")).toHaveAttribute("placeholder", "Search");
  });

  it("gives Interview Related phones the same five short counters, keeping its own search wording", async () => {
    phone(true);
    render(<ConfirmProvider><MailCategoryPage category="interview" /></ConfirmProvider>);
    expect(await labels("Interview Related tabs")).toEqual(["Unread", "Opened", "AI Retry", "Booking", "All Alerts"]);
    expect(screen.getByLabelText("Search notifications")).toHaveAttribute("placeholder", "Search");
  });
});

describe("the phone stylesheet", () => {
  const block = CSS.slice(CSS.indexOf("/* Selection Related on an iPhone, matched"));
  const rules = block.replace(/\/\*[\s\S]*?\*\//g, "");
  it("puts all five counters in one row on phones, Selection and Interview", () => {
    expect(block).toMatch(/^[\s\S]*?@media \(max-width: 640px\) \{/);
    expect(block).toMatch(/:is\(\.mcp--selection, \.mcp--interview\) \.mcp-tiles \{ grid-template-columns: repeat\(5, minmax\(0, 1fr\)\);/);
    expect(block).toMatch(/:is\(\.mcp--selection, \.mcp--interview\) \.mcp-tile \{ flex-direction: column;/);
  });
  it("scopes every rule in the phone block to Selection Related (counters: Interview Related too)", () => {
    const selectors = [...rules.matchAll(/^\s*([^@/{}\n][^{}]*)\{/gm)].map((m) => m[1].trim());
    expect(selectors.length).toBeGreaterThan(40);
    for (const selector of selectors) for (const part of selector.replaceAll(":is(.mcp--selection, .mcp--interview)", ".mcp--selection").split(",")) expect(part).toMatch(/mcp--selection/);
  });
  it("keeps phone inputs at 16px so iOS never zooms into them", () => {
    for (const [, selector, body] of rules.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (/(^|\s)(input|select)(?=[\s,:[{]|$)/.test(selector.trim())) expect(body).not.toMatch(/font-size:\s*1[0-5](\.\d+)?px/);
    }
  });
});

describe("all five counters in one row at every width (16 Nov 2026)", () => {
  it("lays the counters out in five columns, not four", () => {
    expect(CSS).toMatch(/\.mcp-tiles \{[^}]*grid-template-columns: repeat\(5, minmax\(0, 1fr\)\);/);
    expect(CSS).not.toMatch(/\.mcp-tiles[^{]*\{[^}]*repeat\(4,/);
  });
  it("stacks a counter too narrow for icon beside text instead of clipping it", () => {
    expect(CSS).toMatch(/\.mcp-tiles \{[^}]*container: mcp-tiles \/ inline-size;/);
    const narrow = CSS.slice(CSS.indexOf("@container mcp-tiles (max-width: 915px)"));
    expect(narrow).toMatch(/^@container[^{]*\{\s*\.mcp-tile \{ flex-direction: column;/);
  });
  it("styles the Interview Related phone counters with the Selection mockup's rules", () => {
    expect(CSS).toMatch(/:is\(\.mcp--selection, \.mcp--interview\) \.mcp-tiles \{ grid-template-columns: repeat\(5/);
  });
});
