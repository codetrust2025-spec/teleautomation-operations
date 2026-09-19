"""Lay captured dashboard markup out in a real browser, at every width at once.

jsdom performs no layout, so a component test can say what renders but never
whether it fits. This builds a page that does: the markup a test captured
(`captureLayout` in dashboard/src/test/captureLayout.js), inside the real
application shell -- `.desktop-app.business-desktop-app`, whose 232px sidebar is
what every width measurement has to account for -- with the stylesheets
main.jsx loads, once per viewport width, side by side in iframes.

    LAYOUT_CAPTURE_DIR=.layout-harness/captures npx vitest run <test>   # in dashboard/
    python scripts/layout_harness.py .layout-harness/captures/<name>.html

It serves the result and prints the URL. In a browser, one call measures every
width:

    await window.layoutReport()

which returns, per width: horizontal overflow, the elements that cross the
viewport's right edge, and the controls whose text is cut off -- a select whose
longest option is wider than its box was the bug the first use of this found.

Local development only: nothing here runs in CI except its own test, and the
output directory is ignored by git.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import shutil
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "dashboard" / "src"
OUT = ROOT / ".layout-harness"
# The stylesheets main.jsx imports, in its order: the cascade depends on it.
STYLESHEETS = ("index.css", "businessShell.css", "dailyOps.css", "recruitmentMail.css")
# Desktop, the two sides of every breakpoint the dashboard declares that a
# change has so far needed, tablet and phone.
WIDTHS = (1440, 1280, 1181, 1180, 1024, 902, 900, 768, 390)

FRAME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{links}
<style>html,body{{margin:0}}</style>
</head><body>
<div class="desktop-app business-desktop-app">
  <aside class="desktop-sidebar" aria-hidden="true"></aside>
  <main class="desktop-body business-body">
{markup}
  </main>
</div>
</body></html>
"""

INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Layout harness</title>
<style>
  body{{margin:0;background:#070b12;color:#94a3b8;font:12px system-ui}}
  figure{{margin:8px 0 24px}} figcaption{{margin:0 8px 4px}}
  iframe{{border:1px solid #1f2937;height:900px;display:block}}
</style>
</head><body>
{frames}
<script>
// One call for every width. Waits for each frame, then measures inside it.
window.layoutReport = async function layoutReport() {{
  const frames = [...document.querySelectorAll('iframe')];
  await Promise.all(frames.map((f) => f.contentDocument?.readyState === 'complete'
    ? null : new Promise((ok) => f.addEventListener('load', ok, {{ once: true }}))));
  return frames.map((frame) => {{
    const win = frame.contentWindow, doc = frame.contentDocument;
    // The layout viewport. innerWidth can read device pixels while a frame is
    // still settling, which once reported 1800 for a 1440px frame.
    const vw = doc.documentElement.clientWidth;
    const canvas = doc.createElement('canvas').getContext('2d');
    const label = (el) => el.getAttribute('aria-label') || el.id
      || (el.className && String(el.className).split(' ')[0]) || el.tagName.toLowerCase();
    const room = (el) => {{
      const cs = win.getComputedStyle(el);
      return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    }};
    const textWidth = (el, text) => {{
      const cs = win.getComputedStyle(el);
      canvas.font = `${{cs.fontWeight}} ${{cs.fontSize}} ${{cs.fontFamily}}`;
      return canvas.measureText(text).width;
    }};
    const visible = (el) => el.getClientRects().length > 0;
    const clipped = [];
    for (const el of doc.querySelectorAll('select')) {{
      if (!visible(el)) continue;
      const need = Math.max(0, ...[...el.options].map((o) => textWidth(el, o.textContent)));
      if (need > room(el) + 0.5) clipped.push(`${{label(el)}}: needs ${{Math.round(need)}}px, has ${{Math.round(room(el))}}px`);
    }}
    for (const el of doc.querySelectorAll('input[placeholder]')) {{
      if (!visible(el)) continue;
      const need = textWidth(el, el.placeholder);
      if (need > room(el) + 0.5) clipped.push(`${{label(el)}} placeholder: needs ${{Math.round(need)}}px, has ${{Math.round(room(el))}}px`);
    }}
    for (const el of doc.querySelectorAll('body *')) {{
      const cs = win.getComputedStyle(el);
      if (!visible(el) || cs.textOverflow !== 'ellipsis') continue;
      if (el.scrollWidth > el.clientWidth + 1) clipped.push(`${{label(el)}}: text truncated`);
    }}
    // Past the edge, unless a scrolling or clipping ancestor that itself fits
    // contains it -- a wide table in an overflow-x wrapper is designed to scroll.
    const contained = (el) => {{
      for (let a = el.parentElement; a && a !== doc.body; a = a.parentElement) {{
        const ox = win.getComputedStyle(a).overflowX;
        if (ox !== 'visible' && a.getBoundingClientRect().right <= vw + 1) return true;
      }}
      return false;
    }};
    const pastEdge = [...doc.querySelectorAll('body *')]
      .filter((el) => visible(el) && el.getBoundingClientRect().right > vw + 1 && !contained(el))
      .filter((el) => !el.parentElement || el.parentElement.getBoundingClientRect().right <= vw + 1)
      .map((el) => `${{label(el)}} ends at ${{Math.round(el.getBoundingClientRect().right)}}px`);
    return {{
      width: vw,
      overflowX: doc.documentElement.scrollWidth > vw,
      pastEdge: pastEdge.slice(0, 10),
      clipped: clipped.slice(0, 10),
    }};
  }});
}};
</script>
</body></html>
"""


def build(capture: Path, *, out: Path = OUT, extra_css: tuple[Path, ...] = (), widths=WIDTHS) -> Path:
    """Write the harness for one capture and return the index page."""
    out.mkdir(parents=True, exist_ok=True)
    links = []
    for sheet in [SRC / name for name in STYLESHEETS] + [Path(p) for p in extra_css]:
        if not sheet.exists():
            raise FileNotFoundError(f"stylesheet {sheet} does not exist")
        shutil.copyfile(sheet, out / sheet.name)
        links.append(f'<link rel="stylesheet" href="{sheet.name}">')
    markup = Path(capture).read_text(encoding="utf-8")
    (out / "frame.html").write_text(FRAME.format(links="\n".join(links), markup=markup), encoding="utf-8")
    frames = "\n".join(
        f'<figure><figcaption>{w}px</figcaption>'
        f'<iframe src="frame.html" width="{w}" title="{w}px"></iframe></figure>'
        for w in widths
    )
    index = out / "index.html"
    index.write_text(INDEX.format(frames=frames), encoding="utf-8")
    return index


def serve(out: Path, port: int) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(out))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"layout harness: http://127.0.0.1:{port}/index.html  (Ctrl+C to stop)", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("capture", type=Path, help="HTML written by captureLayout()")
    parser.add_argument("--css", type=Path, action="append", default=[],
                        help="another stylesheet the component imports (repeatable)")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--no-serve", action="store_true", help="build only")
    args = parser.parse_args()
    index = build(args.capture, extra_css=tuple(args.css))
    print(f"built {index}")
    if not args.no_serve:
        serve(index.parent, args.port)


if __name__ == "__main__":
    main()
