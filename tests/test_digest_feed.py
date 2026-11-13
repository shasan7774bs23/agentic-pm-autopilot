import datetime as dt
import unittest

from pmauto import digest_feed

BODY = """# ACME daily PM digest — Wed 11 Nov 2026

## 1. Action needed

| Rule | Ticket | Summary | Owner | Age (bd) | Detail |
|---|---|---|---|---|---|
| Stuck in status | [APP-101](https://x/browse/APP-101) | Investigate | Dev One | 12.0 | In 'In Progress' since 02 Mar |
| Untriaged client ticket | [APP-102](https://x/browse/APP-102) | Reorganize | Dev One | 2.6 | No team comment |

## 2. Sprint health

| Sprint | a | b | c | d | e |
|---|---|---|---|---|---|
| S2 | 1 | 2 | 3 | 4 | 5 |
"""


class DigestFeed(unittest.TestCase):
    def test_action_items_only_from_section_1(self):
        rows = digest_feed.action_items(BODY)
        self.assertEqual([r["key"] for r in rows], ["APP-101", "APP-102"])
        self.assertEqual(rows[1]["rule"], "Untriaged client ticket")

    def test_is_for_matches_title_day(self):
        issue = {"title": "ACME daily PM digest — Wed 11 Nov 2026"}
        self.assertTrue(digest_feed.is_for(issue, dt.date(2026, 11, 11)))
        self.assertFalse(digest_feed.is_for(issue, dt.date(2026, 11, 12)))
        self.assertFalse(digest_feed.is_for(None, dt.date(2026, 11, 12)))
