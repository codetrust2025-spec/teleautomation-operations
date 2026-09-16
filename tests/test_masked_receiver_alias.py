"""A masked payee handle is read against the registry, never trusted on its own.

PhonePe renders the payee as ``XXXXXX4573@ybl``. The mask hides the prefix by
policy, but the provider domain and the trailing digits survive, and together
with the receiver name they either pick out one registered account or none.

Every test here that accepts a mask requires three independent agreements --
name, provider domain, visible tail -- against the same registered record. The
tests that matter more are the refusals: a mask alone, a mask with the wrong
name, a mask at the wrong provider, and above all a mask whose visible digits
CONTRADICT the registered handle. The unmasked part is evidence too.
"""

from __future__ import annotations

import pytest

from features import payment_verification_engine as eng


REGISTERED = ("raviarvind1111@ybl",)


class TestWhatTheMaskStillTells:
    def test_a_matching_tail_and_domain_resolve_to_the_registered_handle(self):
        assert eng._masked_upi_alias_match("XXXXXX1111@ybl", REGISTERED) == "raviarvind1111@ybl"

    @pytest.mark.parametrize("mask", ["******1111@ybl", "xxxxxx1111@ybl", "XXXX1111@ybl"])
    def test_the_mask_character_does_not_matter(self, mask):
        assert eng._masked_upi_alias_match(mask, REGISTERED) == "raviarvind1111@ybl"


class TestWhatItRefuses:
    def test_a_contradicting_tail_matches_nothing(self):
        """lavanya's actual screenshots: ...4573@ybl against a registered
        ...1111@ybl. Same provider, same person's name -- and the one part of
        the handle the mask left disagrees. This is the case the whole rule
        exists to refuse."""
        assert eng._masked_upi_alias_match("XXXXXX4573@ybl", REGISTERED) == ""

    def test_a_different_provider_matches_nothing(self):
        assert eng._masked_upi_alias_match("XXXXXX1111@okaxis", REGISTERED) == ""

    def test_too_few_visible_characters_matches_nothing(self):
        """Three digits cannot distinguish two accounts at one provider."""
        assert eng._masked_upi_alias_match("XXXXXX111@ybl", REGISTERED) == ""

    def test_a_fully_masked_handle_matches_nothing(self):
        assert eng._masked_upi_alias_match("XXXXXXXXXX@ybl", REGISTERED) == ""

    def test_no_domain_matches_nothing(self):
        assert eng._masked_upi_alias_match("XXXXXX1111", REGISTERED) == ""

    def test_an_empty_registry_matches_nothing(self):
        assert eng._masked_upi_alias_match("XXXXXX1111@ybl", ()) == ""

    def test_it_never_matches_a_handle_that_merely_contains_the_tail(self):
        """`endswith`, not `in`: 1111 in the middle of another account is a
        different account."""
        assert eng._masked_upi_alias_match("XXXXXX1111@ybl", ("acc1111xyz@ybl",)) == ""


class TestTheNameIsStillRequired:
    def test_the_engine_pairs_the_mask_with_a_name_match(self):
        """The helper alone is not the rule. classify_receiver only reaches it
        for a record whose aliases already contain the receiver name, so a mask
        can never identify an account on its own."""
        import inspect

        source = inspect.getsource(eng.classify_receiver)
        assert 'name in record["aliases"]' in source
        assert "_masked_record_alias_match(masked_upi" in source
        # and the branch demands both, in one condition
        branch = source[source.index("upi_masked"):]
        assert branch.index('name in record["aliases"]') < branch.index("_masked_record_alias_match")

    def test_an_unmasked_identifier_still_wins_outright(self):
        """A real handle is matched on its own terms; the masked path is only
        for screenshots that never showed one."""
        import inspect

        source = inspect.getsource(eng.classify_receiver)
        assert 'if upi and _valid_upi(upi) and upi in record["upi_ids"]:' in source


class TestItCountsAsAStableIdentifier:
    def test_a_registry_backed_mask_satisfies_the_stable_match_gate(self):
        import inspect

        source = inspect.getsource(eng)
        # Both the completeness flag and the stable-match gate accept it, or the
        # match would resolve and then be discarded as incomplete evidence.
        assert '"upi", "phone", "account", "masked_upi_alias",' in source
        assert source.count('"masked_upi_alias",') >= 2

    def test_the_other_checks_are_untouched(self):
        """UTR, fraud, duplicate and amount checks are unrelated to receiver
        identification and must keep their own say."""
        import inspect

        source = inspect.getsource(eng._verification_state)
        for code in ("TRANSACTION_FAILED", "AMOUNT_UNREADABLE", "AMOUNT_INSUFFICIENT",
                     "TRANSACTION_REFERENCE_MISSING"):
            assert code in source


class TestOnePayeeWithTwoRegisteredHandles:
    """J Ravinder receives on two company accounts: raviarvind1111@ybl, and a
    second whose prefix PhonePe masks, registered as XXXXXX4573@ybl on the
    operator's confirmation. Both are the same payee, on one record, so neither
    resolves through the ambiguity branch that a second record would create.
    """

    BOTH = ("raviarvind1111@ybl", "xxxxxx4573@ybl")

    @pytest.mark.parametrize("mask,expected", [
        ("XXXXXX1111@ybl", "raviarvind1111@ybl"),
        ("XXXXXX4573@ybl", "xxxxxx4573@ybl"),
    ])
    def test_a_mask_of_either_account_resolves(self, mask, expected):
        assert eng._masked_upi_alias_match(mask, self.BOTH) == expected

    def test_each_mask_resolves_to_its_own_account_only(self):
        """Two handles at one provider must not collapse into each other."""
        assert eng._masked_upi_alias_match("XXXXXX1111@ybl", self.BOTH) != "xxxxxx4573@ybl"
        assert eng._masked_upi_alias_match("XXXXXX4573@ybl", self.BOTH) != "raviarvind1111@ybl"

    @pytest.mark.parametrize("mask,why", [
        ("XXXXXX9999@ybl", "a tail belonging to neither account"),
        ("XXXXXX4573@okaxis", "the registered tail at a provider it is not registered for"),
        ("XXXXXX1111@okaxis", "the other tail, likewise"),
        ("XXXXXXXXXX@ybl", "nothing left visible to match on"),
    ])
    def test_registering_a_second_handle_widens_nothing_else(self, mask, why):
        assert eng._masked_upi_alias_match(mask, self.BOTH) == "", why

    def test_adding_a_handle_does_not_make_masks_trusted_in_general(self):
        """The rule is per-registered-handle. An account registered nowhere is
        still refused however ordinary its mask looks."""
        assert eng._masked_upi_alias_match("XXXXXX4573@ybl", ("someoneelse0000@ybl",)) == ""
