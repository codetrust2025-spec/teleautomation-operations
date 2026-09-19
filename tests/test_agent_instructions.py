"""Production is the default end goal, and that has to be written down.

Every change in this project is finished when it is live and verified, not when
a PR is open or CI is green. That rule only holds if it is in the repository
rather than repeated in each request, so ``CLAUDE.md`` carries it and this file
keeps it honest.

The deploy pipeline itself lives in the Marketing repository, which owns
``docker-compose.production.yml`` and the release anchor. These tests assert
that this repository's instructions point there and describe the same stages,
so someone working only in Operations still knows how the change reaches
production. The stage list is verified against the script in the Marketing
repository's own tests; here it only has to agree.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS = ROOT / "CLAUDE.md"

# The pin opens straight after the Operations PR, so both repositories' CI
# run at the same time; nothing merges until both have passed.
STAGES = [
    "ops_pr", "pin", "ops_ci", "pin_ci",
    "ops_merge", "preflight", "pin_merge",
    "sync", "build", "deploy", "verify",
]


def body() -> str:
    assert INSTRUCTIONS.exists(), "CLAUDE.md is missing — the deploy rule has no home"
    return INSTRUCTIONS.read_text(encoding="utf-8")


def flowed() -> str:
    """Emphasis and line wrapping removed, so a phrase that wraps mid-sentence
    is still found."""
    return re.sub(r"\s+", " ", body().replace("*", "").replace('"', "")).lower()


def test_it_states_that_production_is_the_end_goal():
    text = flowed()
    assert "default end goal is live production" in text
    assert "do not stop at" in text


def test_it_names_where_a_task_actually_ends():
    text = flowed()
    assert "/version" in text
    assert "healthy" in text
    assert "live site" in text


def test_it_names_the_only_ways_out():
    """A rule with vague exceptions is a rule that gets talked out of."""
    text = flowed()
    for opt_out in ("do not deploy", "local only", "pr only"):
        assert opt_out in text, f"the opt-out {opt_out!r} is not written down"
    for pause in ("credential", "destructive", "unrecoverable"):
        assert pause in text, f"the pause condition {pause!r} is not written down"


def test_it_says_how_this_repository_reaches_production():
    """Operations does not deploy itself: the compose file and release anchor
    live in the Marketing repository. Someone working only here needs to know
    that, or they will look for a deploy script that does not exist."""
    text = flowed()
    assert "teleautomation-messaging" in text
    assert "fix_and_deploy.sh" in text
    assert "anchor" in text


def test_it_describes_the_same_stages_as_the_pipeline():
    blocks = re.findall(r"```[a-z]*\r?\n(.*?)\r?\n```", body(), re.S)
    listed = [b.split() for b in blocks if set(STAGES) <= set(b.split())]
    assert listed, "CLAUDE.md has no block listing every deploy stage"
    assert listed[0] == STAGES, "the stage list disagrees with the deploy pipeline"


def test_it_keeps_environment_specifics_out_of_the_repository():
    """The README promises no production domain is assumed here. Instructions
    are the easiest place for an address to slip in and become permanent."""
    text = body()
    ips = re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text)
    assert not ips, f"an IP address is written into the instructions: {ips}"
    assert "_ed25519" not in text and "id_rsa" not in text, "an ssh key path is hardcoded"
    assert "KVM1_SSH" in text, "the host is not described as coming from the environment"


def test_it_records_the_traps_this_codebase_has_actually_hit():
    """These are the mistakes that cost real production time here. They are in
    the instructions so the next agent does not rediscover them."""
    text = flowed()
    assert "pass while the path it describes never runs" in text
    assert "env file decides" in text
