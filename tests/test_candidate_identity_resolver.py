"""Regression tests for candidate identity resolution.

Every test here drives the real resolver. ``_canonical_identity`` and
``identity_cluster`` are never monkeypatched — the cursor is faked, the logic
is not — because the defect these cover is in the resolution logic itself and
the previous suite stubbed exactly that function out.

None of these tests assert which record should survive a merge. They assert
only whether two ids describe one person, which is the question reconciliation
actually has to answer.
"""

from __future__ import annotations

import itertools

import pytest

from features import candidate_identity
from features.candidate_identity import IdentityClusterTooLarge
from tests.identity_fakes import FakeIdentityCursor as FakeCursor, profile_row as profile


# ── the incident shape ───────────────────────────────────────────────────────


def test_name_alias_rows_are_one_identity_without_naming_a_survivor():
    """The Ram Charan shape: one row carries the phone, the other the name.

    Deliberately asserts only that the two ids are the same person. Which id
    should survive is a data decision that needs production evidence, and this
    test must keep passing whichever way that decision goes.
    """
    cur = FakeCursor(
        candidates=[
            profile("bbb11111", "Ram Charan M S", phone=""),
            profile("aaa22222", "Reddy Charan M S", phone="9000000102"),
        ],
    )

    cluster = candidate_identity.identity_cluster(cur, "bbb11111")

    assert cluster == {"bbb11111", "aaa22222"}
    assert candidate_identity.same_identity(cur, "bbb11111", "aaa22222")
    assert candidate_identity.same_identity(cur, "aaa22222", "bbb11111")


# ── non-idempotent chains ────────────────────────────────────────────────────


def test_non_idempotent_link_chain_resolves_to_one_identity():
    """canonical(canonical(x)) != canonical(x) must not split one person.

    A single COALESCE hop returns 'b' for 'a' and 'c' for 'b', so comparing
    hops decides these are different people. The closure reaches the whole
    chain from any member.
    """
    cur = FakeCursor(
        candidates=[
            profile("a", "Person One", phone="9000000001"),
            profile("b", "Person One Clone"),
            profile("c", "Person One Clone Two"),
        ],
        links=[("a", "b"), ("b", "c")],
    )

    assert candidate_identity.identity_cluster(cur, "a") == {"a", "b", "c"}
    assert candidate_identity.identity_cluster(cur, "c") == {"a", "b", "c"}
    assert candidate_identity.same_identity(cur, "a", "c")


def test_cluster_is_identical_from_every_seed():
    """Idempotence: the component is a property of the person, not the seed."""
    cur = FakeCursor(
        candidates=[
            profile("r1", "Someone", phone="9111111111"),
            profile("r2", "Someone Else", phone="9111111111"),
            profile("r3", "Third Row", email="shared@example.com"),
            profile("r4", "Fourth Row", email="shared@example.com"),
        ],
        links=[("r2", "r3")],
    )

    clusters = {
        seed: candidate_identity.identity_cluster(cur, seed)
        for seed in ("r1", "r2", "r3", "r4")
    }

    assert len(set(clusters.values())) == 1
    assert clusters["r1"] == {"r1", "r2", "r3", "r4"}


# ── sticky same-priority re-derivation ───────────────────────────────────────


def test_sticky_same_priority_link_does_not_split_a_phone_group():
    """Migration 010 cannot lower a canonical at equal priority.

    ``WHERE EXCLUDED.match_priority < candidate_identity_links.match_priority``
    makes re-deriving VERIFIED_PHONE over VERIFIED_PHONE a no-op (3 < 3 is
    false), so an older row keeps pointing at itself while a newly arrived,
    lexicographically smaller row becomes its own canonical. Both rows share a
    phone, so they are one person regardless of what the table froze.
    """
    cur = FakeCursor(
        candidates=[
            profile("zz_old", "Older Row", phone="9333333333"),
            profile("aa_new", "Newer Row", phone="+91 9333333333"),
        ],
        # The stale snapshot: each row is its own canonical.
        links=[("zz_old", "zz_old"), ("aa_new", "aa_new")],
    )

    assert candidate_identity.same_identity(cur, "zz_old", "aa_new")
    assert candidate_identity.identity_cluster(cur, "zz_old") == {"zz_old", "aa_new"}


# ── transitivity across every relationship kind ──────────────────────────────


def test_transitivity_across_phone_email_mailbox_name_and_explicit():
    """A chain joined by a different key at every step is still one person."""
    cur = FakeCursor(
        candidates=[
            profile("n1", "Chain Start", phone="9444444444"),
            profile("n2", "Chain Second", phone="09444444444"),
            profile("n3", "Chain Third", email="chain@example.com"),
            profile("n4", "Reddy Charan M S"),
            profile("n5", "Ram Charan M S"),
            profile("n6", "Explicit Clone", canonical_candidate_id="n5"),
        ],
        # n2 -- personal email --> n3, and n3 -- mailbox --> n4
        links=[],
        mailboxes=[("n3", "Shared@Example.com"), ("n4", "shared@example.com")],
    )
    cur.candidates[1]["email"] = "chain@example.com"
    cur.candidates[3]["phone"] = ""

    cluster = candidate_identity.identity_cluster(cur, "n1")

    assert cluster == {"n1", "n2", "n3", "n4", "n5", "n6"}
    assert candidate_identity.same_identity(cur, "n1", "n6")


def test_round_wise_row_sharing_a_name_is_not_pulled_in():
    """A support row repeating a name is not that person's identity.

    Mirrors candidate_store.candidate_identity_ids, which only joins by name
    when both rows are profile rows.
    """
    cur = FakeCursor(
        candidates=[
            profile("p1", "Same Name"),
            {
                "id": "rw1",
                "name": "Same Name",
                "phone": "",
                "email": "",
                "service_type": "round_wise",
            },
        ],
    )

    assert candidate_identity.identity_cluster(cur, "p1") == {"p1"}
    assert not candidate_identity.same_identity(cur, "p1", "rw1")


# ── genuinely different people ───────────────────────────────────────────────


# ── name is a weak edge: it must never fuse two real identities ──────────────


def test_same_name_different_phones_stay_separate():
    """Two real people share a name. A name alone must not merge them."""
    cur = FakeCursor(
        candidates=[
            profile("a1", "Rahul Sharma", phone="9111111111"),
            profile("b1", "Rahul Sharma", phone="9222222222"),
        ],
    )

    assert candidate_identity.identity_cluster(cur, "a1") == {"a1"}
    assert candidate_identity.identity_cluster(cur, "b1") == {"b1"}
    assert not candidate_identity.same_identity(cur, "a1", "b1")


def test_same_name_different_personal_emails_stay_separate():
    cur = FakeCursor(
        candidates=[
            profile("a2", "Priya Nair", email="priya.one@example.com"),
            profile("b2", "Priya Nair", email="priya.two@example.com"),
        ],
    )

    assert not candidate_identity.same_identity(cur, "a2", "b2")


def test_same_name_different_mailboxes_stay_separate():
    cur = FakeCursor(
        candidates=[
            profile("a3", "Vikram Iyer", phone="9333333331"),
            profile("b3", "Vikram Iyer", phone="9333333332"),
        ],
        mailboxes=[("a3", "vikram.one@example.com"), ("b3", "vikram.two@example.com")],
    )

    assert not candidate_identity.same_identity(cur, "a3", "b3")


def test_shared_name_does_not_bridge_two_strong_clusters():
    """The dangerous shape: X--phone--X2, X2 shares a name with Y2, Y2--phone--Y.

    Without a guard, transitivity turns one shared name into a bridge and
    fuses two unrelated people's entire histories.
    """
    cur = FakeCursor(
        candidates=[
            profile("x1", "Person X", phone="9444444441"),
            profile("x2", "Bridge Name", phone="9444444441"),
            profile("y2", "Bridge Name", phone="9444444442"),
            profile("y1", "Person Y", phone="9444444442"),
        ],
    )

    x_cluster = candidate_identity.identity_cluster(cur, "x1")
    y_cluster = candidate_identity.identity_cluster(cur, "y1")

    assert x_cluster == {"x1", "x2"}
    assert y_cluster == {"y1", "y2"}
    assert not candidate_identity.same_identity(cur, "x1", "y1")


def test_keyless_row_matching_two_conflicting_people_joins_neither():
    """An unidentifiable row must not become a bridge either.

    A row with no phone and no email whose name matches two people holding
    different phones is genuinely ambiguous. Attaching it to whichever side
    was visited first would be arbitrary and would fuse both clusters, so the
    name yields nothing and every side keeps its own identity.
    """
    cur = FakeCursor(
        candidates=[
            profile("k1", "Ambiguous Name", phone="9555555551"),
            profile("k2", "Ambiguous Name", phone=""),
            profile("k3", "Ambiguous Name", phone="9555555552"),
        ],
    )

    assert not candidate_identity.same_identity(cur, "k1", "k3")
    assert not candidate_identity.same_identity(cur, "k1", "k2")
    assert candidate_identity.identity_cluster(cur, "k2") == {"k2"}


def test_name_edge_still_joins_when_only_one_identity_is_present():
    """The guard must not break the case it exists to allow.

    One row carries the phone, the other carries none — a single identity, so
    the name edge is safe and must still apply.
    """
    cur = FakeCursor(
        candidates=[
            profile("g1", "Ram Charan M S", phone=""),
            profile("g2", "Reddy Charan M S", phone="9000000102"),
        ],
    )

    assert candidate_identity.same_identity(cur, "g1", "g2")


def test_strong_evidence_still_joins_rows_with_different_phones():
    """The conflict guard applies to name edges only.

    Two rows joined by a shared mailbox are the same person even if their
    phone fields disagree — that is strong evidence, not a name coincidence.
    """
    cur = FakeCursor(
        candidates=[
            profile("s1", "Someone", phone="9666666661"),
            profile("s2", "Someone Else Entirely", phone="9666666662"),
        ],
        mailboxes=[("s1", "shared.account@example.com"), ("s2", "shared.account@example.com")],
    )

    assert candidate_identity.same_identity(cur, "s1", "s2")


# ── weak-edge confluence: order must not decide who keeps the bridge ─────────


def test_ambiguous_weak_chain_joins_nothing():
    """A(111) — B(nothing) — C(222), joined only by weak edges.

    Accepting A-B first makes B-C conflicting; accepting B-C first makes A-B
    conflicting. Either way the survivor would be chosen by iteration order,
    not by evidence. The whole weakly connected region is ambiguous, so none
    of it is applied and B joins neither side.
    """
    cur = FakeCursor(
        candidates=[
            profile("A", "Person A", phone="111111111"),
            profile("B", "Bridge Row"),
            profile("C", "Person C", phone="222222222"),
        ],
        links=[
            ("B", "A", "GMAIL_ACCOUNT_MAPPING", True),
            ("B", "C", "GMAIL_ACCOUNT_MAPPING", True),
        ],
    )

    assert candidate_identity.identity_cluster(cur, "A") == {"A"}
    assert candidate_identity.identity_cluster(cur, "B") == {"B"}
    assert candidate_identity.identity_cluster(cur, "C") == {"C"}
    assert not candidate_identity.same_identity(cur, "A", "B")
    assert not candidate_identity.same_identity(cur, "B", "C")
    assert not candidate_identity.same_identity(cur, "A", "C")


def test_ambiguous_weak_chain_of_mixed_kinds_joins_nothing():
    """The same hole must not reopen when the two weak edges differ in kind.

    Here one side is a canonical-name edge and the other a derived link, so a
    guard applied per-kind rather than per-region would still let B attach.
    """
    cur = FakeCursor(
        candidates=[
            profile("m1", "Shared Name", phone="333333331"),
            profile("m2", "Shared Name"),
            profile("m3", "Different Person", phone="333333332"),
        ],
        links=[("m2", "m3", "VERIFIED_PHONE", True)],
    )

    assert candidate_identity.identity_cluster(cur, "m1") == {"m1"}
    assert candidate_identity.identity_cluster(cur, "m2") == {"m2"}
    assert candidate_identity.identity_cluster(cur, "m3") == {"m3"}


def test_weak_chain_without_contradiction_still_merges_end_to_end():
    """The guard must reject ambiguity, not weak evidence as such."""
    cur = FakeCursor(
        candidates=[
            profile("w1", "Person W", phone="444444441"),
            profile("w2", "Bridge One"),
            profile("w3", "Bridge Two"),
        ],
        links=[
            ("w2", "w1", "GMAIL_ACCOUNT_MAPPING", True),
            ("w2", "w3", "GMAIL_ACCOUNT_MAPPING", True),
        ],
    )

    assert candidate_identity.identity_cluster(cur, "w1") == {"w1", "w2", "w3"}
    assert candidate_identity.same_identity(cur, "w1", "w3")


def test_ambiguous_weak_chain_result_is_independent_of_row_and_link_order():
    """Prove the answer comes from evidence, not from iteration order."""
    rows = [
        profile("A", "Person A", phone="111111111"),
        profile("B", "Bridge Row"),
        profile("C", "Person C", phone="222222222"),
    ]
    links = [
        ("B", "A", "GMAIL_ACCOUNT_MAPPING", True),
        ("B", "C", "GMAIL_ACCOUNT_MAPPING", True),
    ]

    seen = set()
    for row_order in itertools.permutations(rows):
        for link_order in itertools.permutations(links):
            cur = FakeCursor(candidates=list(row_order), links=list(link_order))
            seen.add(
                tuple(
                    tuple(sorted(candidate_identity.identity_cluster(cur, seed)))
                    for seed in ("A", "B", "C")
                )
            )

    assert seen == {(("A",), ("B",), ("C",))}


# ── derived link rows are history, not proof ─────────────────────────────────


def test_stale_derived_link_cannot_merge_two_conflicting_people():
    """The exact scenario the link table makes possible.

    candidate_identity_links is the artifact we already know goes stale and
    inconsistent. If a row in it were treated as proof of identity, one bad
    historical mapping would bypass every phone, email and name protection.
    """
    cur = FakeCursor(
        candidates=[
            profile("A", "Rahul Sharma", phone="9000000001", email="rahul.a@example.com"),
            profile("B", "Rahul Sharma", phone="9000000002", email="rahul.b@example.com"),
        ],
        links=[("A", "B", "VERIFIED_PHONE", True)],
    )

    assert candidate_identity.identity_cluster(cur, "A") == {"A"}
    assert candidate_identity.identity_cluster(cur, "B") == {"B"}
    assert not candidate_identity.same_identity(cur, "A", "B")


@pytest.mark.parametrize(
    "method",
    ["VERIFIED_PHONE", "VERIFIED_PERSONAL_EMAIL", "GMAIL_ACCOUNT_MAPPING", "SELF"],
)
def test_no_derived_link_method_overrides_contradictory_evidence(method):
    cur = FakeCursor(
        candidates=[
            profile("d1", "Person One", phone="9000000011", email="one@example.com"),
            profile("d2", "Person Two", phone="9000000012", email="two@example.com"),
        ],
        links=[("d1", "d2", method, True)],
    )

    assert not candidate_identity.same_identity(cur, "d1", "d2")


def test_derived_link_still_joins_when_nothing_contradicts_it():
    """Downgrading derived links must not make them useless.

    With no conflicting identifier anywhere in the reachable set, a derived
    mapping is still the best evidence available and should be honoured.
    """
    cur = FakeCursor(
        candidates=[
            profile("e1", "Person One", phone="9000000021"),
            profile("e2", "Clone Row"),
        ],
        links=[("e2", "e1", "GMAIL_ACCOUNT_MAPPING", True)],
    )

    assert candidate_identity.same_identity(cur, "e1", "e2")


def test_explicit_profile_relationship_joins_despite_name_and_email_drift():
    """A human-declared relationship is the one mapping that outranks drift."""
    cur = FakeCursor(
        candidates=[
            profile("f1", "Ananya Krishnan", phone="9000000031", email="old@example.com"),
            profile("f2", "Ananya K", phone="9000000032", email="new@example.com"),
        ],
        links=[("f2", "f1", "EXPLICIT_PROFILE_RELATIONSHIP", True)],
    )

    assert candidate_identity.same_identity(cur, "f1", "f2")
    assert candidate_identity.identity_cluster(cur, "f1") == {"f1", "f2"}


def test_explicit_payload_relationship_joins_despite_drift():
    """The same assertion recorded on the row itself, not in the link table."""
    cur = FakeCursor(
        candidates=[
            profile("g10", "Ananya Krishnan", phone="9000000041", email="old@example.com"),
            profile(
                "g11",
                "Ananya K",
                phone="9000000042",
                email="new@example.com",
                profile_candidate_id="g10",
            ),
        ],
    )

    assert candidate_identity.same_identity(cur, "g10", "g11")


def test_unverified_explicit_link_is_treated_as_derived():
    """verified=false is not a decision anyone stands behind."""
    cur = FakeCursor(
        candidates=[
            profile("h1", "Person One", phone="9000000051", email="one@example.com"),
            profile("h2", "Person Two", phone="9000000052", email="two@example.com"),
        ],
        links=[("h1", "h2", "EXPLICIT_PROFILE_RELATIONSHIP", False)],
    )

    assert not candidate_identity.same_identity(cur, "h1", "h2")


def test_two_different_people_never_share_an_identity():
    cur = FakeCursor(
        candidates=[
            profile("x1", "Asha Rao", phone="9555555551", email="asha@example.com"),
            profile("y1", "Bala Menon", phone="9555555552", email="bala@example.com"),
        ],
        mailboxes=[("x1", "asha@example.com"), ("y1", "bala@example.com")],
    )

    assert not candidate_identity.same_identity(cur, "x1", "y1")
    assert candidate_identity.identity_cluster(cur, "x1") == {"x1"}


def test_blank_identity_keys_do_not_merge_strangers():
    """Empty phone/email must not become a join key."""
    cur = FakeCursor(
        candidates=[
            profile("b1", "Person A", phone="", email=""),
            profile("b2", "Person B", phone="", email=""),
            profile("b3", "Person C", phone="   ", email=""),
        ],
    )

    assert candidate_identity.identity_cluster(cur, "b1") == {"b1"}
    assert not candidate_identity.same_identity(cur, "b1", "b2")
    assert not candidate_identity.same_identity(cur, "b2", "b3")


def test_short_phone_fragments_are_not_identity():
    """candidate_phone_identity requires >= 8 digits; extensions must not join."""
    cur = FakeCursor(
        candidates=[
            profile("s1", "Person A", phone="1234"),
            profile("s2", "Person B", phone="1234"),
        ],
    )

    assert not candidate_identity.same_identity(cur, "s1", "s2")


def test_runaway_closure_is_refused_rather_than_guessed():
    shared = [profile(f"m{index}", f"Person {index}", phone="9666666666")
              for index in range(candidate_identity.MAX_CLUSTER_SIZE + 5)]
    cur = FakeCursor(candidates=shared)

    with pytest.raises(IdentityClusterTooLarge):
        candidate_identity.identity_cluster(cur, "m0")


# ── the label is not an identity ruling ──────────────────────────────────────


def test_representative_prefers_a_human_declared_relationship_over_min_id():
    """min(id) is an implementation artifact and must not outrank a decision."""
    cur = FakeCursor(
        candidates=[
            profile("zzz_declared", "Declared Profile", phone="9777777777"),
            profile(
                "aaa_clone",
                "Declared Profile Clone",
                phone="9777777777",
                canonical_candidate_id="zzz_declared",
            ),
        ],
    )

    cluster = candidate_identity.identity_cluster(cur, "aaa_clone")

    assert candidate_identity.cluster_representative(cur, cluster) == "zzz_declared"


def test_representative_is_stable_when_nothing_is_declared():
    cur = FakeCursor(
        candidates=[
            profile("q2", "Undeclared", phone="9888888888"),
            profile("q1", "Undeclared Clone", phone="9888888888"),
        ],
    )

    cluster = candidate_identity.identity_cluster(cur, "q2")

    assert candidate_identity.cluster_representative(cur, cluster) == "q1"
    assert candidate_identity.cluster_representative(cur, cluster) == "q1"
