"""The layout harness stays usable: real shell, real stylesheets, every width.

It is a local tool, so nothing else would notice it rotting -- a renamed shell
class or a stylesheet main.jsx stopped importing would leave it measuring a
layout production does not have.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import layout_harness  # noqa: E402


def test_it_lays_the_capture_out_in_the_real_shell(tmp_path: Path) -> None:
    capture = tmp_path / "row.html"
    capture.write_text('<div class="mail-filters mail-filters--compact"><select aria-label="x"></select></div>',
                       encoding="utf-8")
    index = layout_harness.build(capture, out=tmp_path / "out")
    frame = (index.parent / "frame.html").read_text(encoding="utf-8")
    assert 'class="mail-filters mail-filters--compact"' in frame
    assert '<div class="desktop-app business-desktop-app">' in frame
    for sheet in layout_harness.STYLESHEETS:
        assert f'href="{sheet}"' in frame
        assert (index.parent / sheet).exists()


def test_the_shell_it_uses_is_the_one_the_app_styles() -> None:
    shell = (ROOT / "dashboard" / "src" / "businessShell.css").read_text(encoding="utf-8")
    assert ".desktop-app.business-desktop-app {" in shell
    assert "--shell-sidebar:" in shell
    assert ".desktop-body.business-body {" in shell


def test_the_stylesheets_are_the_ones_main_imports_in_its_order() -> None:
    main = (ROOT / "dashboard" / "src" / "main.jsx").read_text(encoding="utf-8")
    imported = re.findall(r"^import '\./([\w.]+\.css)'", main, re.M)
    assert tuple(imported) == layout_harness.STYLESHEETS


def test_one_page_measures_every_width(tmp_path: Path) -> None:
    capture = tmp_path / "x.html"
    capture.write_text("<p>x</p>", encoding="utf-8")
    index = layout_harness.build(capture, out=tmp_path / "out").read_text(encoding="utf-8")
    widths = [int(w) for w in re.findall(r'<iframe src="frame.html" width="(\d+)"', index)]
    assert widths == list(layout_harness.WIDTHS)
    assert "window.layoutReport = async function layoutReport()" in index
