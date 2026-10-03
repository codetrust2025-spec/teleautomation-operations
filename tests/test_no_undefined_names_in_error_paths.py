"""An error handler must not itself crash.

`logger.exception(...)` was called in three `except` blocks of the candidate and
expense routers, but those modules never defined `logger`. The first failure of a
payment-verification call therefore raised NameError inside the handler and the
caller got a 500 instead of the intended {"status": "error", ...} answer; the
reconciliation scan did the same. `Path` was used in an annotation of the
candidate store without being imported.

The guard is structural, so the next module that logs without defining a logger
fails here rather than in production.
"""
import ast
import asyncio
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACKAGES = ("api", "core", "features", "services", "workers")


def _py_files():
    for pkg in PACKAGES:
        for base, _dirs, files in os.walk(os.path.join(ROOT, pkg)):
            for name in files:
                if name.endswith(".py"):
                    yield os.path.join(base, name)
    yield os.path.join(ROOT, "main.py")


def _assigned_names(node):
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for alias in n.names:
                names.add((alias.asname or alias.name).split(".")[0])
    return names


def _module_level_names(tree):
    names = set()
    for stmt in tree.body:
        names |= _assigned_names(stmt) if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) else {stmt.name}
    return names


@pytest.mark.parametrize("path", sorted(_py_files()), ids=lambda p: os.path.relpath(p, ROOT))
def test_every_module_that_uses_logger_defines_it(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    uses = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == "logger" and isinstance(n.ctx, ast.Load)
    ]
    if not uses:
        return
    if "logger" in _module_level_names(tree):
        return
    # Allowed only when every use sits in a function that defines it for itself.
    unprotected = []
    for use in uses:
        owner = None
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(c is use for c in ast.walk(fn)):
                if "logger" in _assigned_names(fn):
                    owner = fn
        if owner is None:
            unprotected.append(use.lineno)
    assert not unprotected, f"{os.path.relpath(path, ROOT)} uses `logger` at lines {unprotected} but never defines it"


def test_the_candidate_store_imports_what_its_annotations_name():
    from features import candidate_store

    assert candidate_store.Path is not None


def test_the_reconciliation_route_reports_a_failed_scan_instead_of_crashing(monkeypatch):
    from api.routers import expenses
    from features import financial_reconciliation

    def boom():
        raise RuntimeError("scan unavailable")

    monkeypatch.setattr(financial_reconciliation, "reconciliation_report", boom)
    result = asyncio.run(expenses.handler_expenses_reconciliation())
    assert result["status"] == "error" and "scan unavailable" in result["message"]


def test_both_routers_have_a_module_logger():
    from api.routers import candidates, expenses

    assert candidates.logger.name == "api.routers.candidates"
    assert expenses.logger.name == "api.routers.expenses"
