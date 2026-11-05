"""The Selection view pages by candidate, so a candidate is never split.

Grouping the twenty rows of an ordinary page in the browser would have been
less code. It would also have been wrong: a candidate whose alerts straddle a
page boundary would be drawn as a group on page one and a second group of the
same name on page two, which is the one thing the grouped view exists to stop.
So `group_by_candidate` chooses the page of candidates first and then returns
every matching row those candidates hold.

The other half of this file is the half that matters more. Grouping must not
become a filter: no mail may be dropped, merged or deduplicated on its way into
a group, and two mails a candidate received on the same day from the same
company are two alerts. A grouping that quietly collapsed them would look
tidier and lose an offer.
"""

from __future__ import annotations

import pytest

from core import recruitment_mail_store as store


class GroupingCursor:
    """Answers the three grouped queries in the order the store issues them."""

    def __init__(self, candidate_ids=("cand-a", "cand-b"), rows=()):
        self.executed: list[tuple[str, list]] = []
        self._candidate_ids = list(candidate_ids)
        self._rows = list(rows)
        self.description = [type("D", (), {"name": name})
                            for name in ("id", "candidate_id", "classification")]

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), list(params or [])))

    def fetchone(self):
        return (len(self._candidate_ids),)

    def fetchall(self):
        last = self.executed[-1][0]
        if "SELECT candidate_id" in last:
            return [(value,) for value in self._candidate_ids]
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def grouped(monkeypatch):
    cursor = GroupingCursor()
    monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
    return cursor


class TestThePageUnitIsTheCandidate:
    def test_the_total_counts_candidates_not_rows(self, grouped):
        """The footer pages through candidates, so the count must match."""
        _, total = store.list_notifications(
            filters={"classification_group": "selection"}, group_by_candidate=True)
        assert "count(DISTINCT candidate_id)" in grouped.executed[0][0]
        assert total == 2

    def test_the_candidate_page_is_ordered_by_newest_alert(self, grouped):
        store.list_notifications(
            filters={"classification_group": "selection"}, group_by_candidate=True)
        sql = grouped.executed[1][0]
        assert "GROUP BY candidate_id" in sql
        assert "ORDER BY max(COALESCE(email_received_at, created_at)) DESC" in sql
        assert "LIMIT %s OFFSET %s" in sql

    def test_limit_and_offset_apply_to_candidates_not_rows(self, grouped):
        store.list_notifications(
            filters={"classification_group": "selection"},
            limit=20, offset=40, group_by_candidate=True)
        assert grouped.executed[1][1][-2:] == [20, 40]

    def test_every_row_of_the_paged_candidates_is_returned_unpaged(self, grouped):
        """No LIMIT on the row query: a candidate's tenth mail must not fall
        off the page and disappear from their group."""
        store.list_notifications(
            filters={"classification_group": "selection"}, group_by_candidate=True)
        sql = grouped.executed[2][0]
        assert "candidate_id IN (%s, %s)" in sql
        assert "LIMIT" not in sql
        assert grouped.executed[2][1][-2:] == ["cand-a", "cand-b"]

    def test_the_row_query_keeps_every_filter(self, grouped):
        """Grouping must narrow nothing: the same WHERE clause applies."""
        store.list_notifications(
            filters={"classification_group": "selection", "candidate_id": "cand-a"},
            group_by_candidate=True)
        rows_sql = grouped.executed[2][0]
        assert "candidate_id=%s" in rows_sql
        for value in store.SELECTION_RELATED_CLASSIFICATIONS:
            assert value in grouped.executed[2][1]

    def test_oldest_first_reverses_both_orders(self, grouped):
        store.list_notifications(
            filters={"classification_group": "selection", "sort": "oldest"},
            group_by_candidate=True)
        assert "ORDER BY max(COALESCE(email_received_at, created_at)) ASC" in grouped.executed[1][0]
        assert "ORDER BY " + store.grouped_notification_order_sql("ASC") in grouped.executed[2][0]

    def test_no_candidates_skips_the_row_query(self, monkeypatch):
        cursor = GroupingCursor(candidate_ids=())
        monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
        rows, total = store.list_notifications(
            filters={"classification_group": "selection"}, group_by_candidate=True)
        assert (rows, total) == ([], 0)
        assert len(cursor.executed) == 2


class TestGroupingIsNotAFilter:
    def test_the_flat_path_is_untouched(self, grouped):
        """Interview Related and the unfiltered view still page by row."""
        store.list_notifications(filters={"classification_group": "interview"})
        assert "count(*)" in grouped.executed[0][0]
        assert "GROUP BY candidate_id" not in " ".join(s for s, _ in grouped.executed)

    def test_two_mails_from_one_candidate_both_survive(self, monkeypatch):
        """The point of the view: same candidate, same company, two outcomes.
        Deduplicating these would hide an offer behind a joining confirmation."""
        rows = [("n-1", "cand-a", "offer_received"),
                ("n-2", "cand-a", "joining_confirmed")]
        cursor = GroupingCursor(candidate_ids=("cand-a",), rows=rows)
        monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
        returned, total = store.list_notifications(
            filters={"classification_group": "selection"}, group_by_candidate=True)
        assert total == 1, "one candidate is one page entry"
        assert [row["id"] for row in returned] == ["n-1", "n-2"]
        assert [row["classification"] for row in returned] == [
            "offer_received", "joining_confirmed"]


class TestInterviewMailsCannotReachTheSelectionView:
    def test_the_two_groups_are_disjoint(self):
        """The view relies on this rather than filtering interviews out again
        in the browser."""
        assert not (store.SELECTION_RELATED_CLASSIFICATIONS
                    & store.INTERVIEW_RELATED_CLASSIFICATIONS)

    def test_no_interview_classification_is_bound_to_the_grouped_query(self, grouped):
        store.list_notifications(
            filters={"classification_group": "selection"}, group_by_candidate=True)
        for sql, params in grouped.executed:
            for value in store.INTERVIEW_RELATED_CLASSIFICATIONS:
                assert value not in params, f"{value} reached {sql[:40]}"


class TestTheComposedSqlIsWellFormed:
    """A fake cursor accepts any string, so these assert what it cannot.

    The grouped path builds three statements from one shared WHERE clause and
    appends the candidate ids to the third. If the parameter list and the
    placeholders drift apart the query fails at execution time only - never in
    a test that records SQL without running it. That is the same shape as the
    dead `exact` filter set: present, plausible, and never exercised.
    """

    @pytest.mark.parametrize("filters", [
        {"classification_group": "selection"},
        {"classification_group": "selection", "search": "gopi", "is_read": False},
        {"classification_group": "selection", "candidate_id": "cand-a",
         "priority": "review_required", "confidence_min": 0.5},
    ])
    def test_every_statement_binds_exactly_its_placeholders(self, monkeypatch, filters):
        cursor = GroupingCursor()
        monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
        store.list_notifications(filters=filters, group_by_candidate=True)
        assert len(cursor.executed) == 3
        for sql, params in cursor.executed:
            assert sql.count("%s") == len(params), sql

    def test_the_candidate_id_list_grows_with_the_page(self, monkeypatch):
        """The IN list is built from the ids actually returned, not from limit."""
        cursor = GroupingCursor(candidate_ids=("a", "b", "c", "d", "e"))
        monkeypatch.setattr(store, "get_connection", lambda: FakeConn(cursor))
        store.list_notifications(
            filters={"classification_group": "selection"}, group_by_candidate=True)
        sql, params = cursor.executed[2]
        assert "candidate_id IN (%s, %s, %s, %s, %s)" in sql
        assert sql.count("%s") == len(params)
