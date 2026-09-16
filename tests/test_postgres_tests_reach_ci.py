"""A PostgreSQL test that CI never runs is a test that passes by skipping.

`tests/test_reinvite_after_cancellation_pg.py` was written, merged and reported
green while every one of its cases skipped: the suite step does not set
AUDIT_TEST_DATABASE_URL, and the step that does named a single file. Nothing
failed, and the SQL it was written to prove had never executed.

The file name is the wiring. These two checks keep it that way.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
DATABASE_VARIABLE = "AUDIT_TEST_DATABASE_URL"


#: Reading the variable is what makes a test need the database. This file only
#: names it, which is why the check is "reads" rather than "mentions".
READS_DATABASE = re.compile(r"(?:getenv|environ(?:\.get)?)\s*[(\[]\s*[\"']" + "AUDIT_TEST_DATABASE_URL")


def postgres_tests() -> list[Path]:
    return sorted(path for path in (ROOT / "tests").glob("test_*.py")
                  if READS_DATABASE.search(path.read_text(encoding="utf-8")))


def test_every_postgres_test_is_named_so_ci_selects_it():
    assert postgres_tests(), "the guard is pointless once no test needs PostgreSQL"
    misnamed = [path.name for path in postgres_tests() if not path.name.endswith("_pg.py")]
    assert not misnamed, (
        f"{misnamed} need PostgreSQL but are outside tests/test_*_pg.py, so CI runs them "
        f"without {DATABASE_VARIABLE} and every case skips")


def test_the_workflow_step_selects_all_of_them():
    step = next((block for block in WORKFLOW.split("\n      - ") if DATABASE_VARIABLE in block
                 and "pytest" in block), None)
    assert step, f"no CI step runs pytest with {DATABASE_VARIABLE}"
    selection = re.search(r"pytest\s+(\S+)", step)
    assert selection and selection.group(1) == "tests/test_*_pg.py", (
        "the PostgreSQL step must select every tests/test_*_pg.py, not a fixed list; "
        f"it runs: {selection.group(1) if selection else step!r}")
