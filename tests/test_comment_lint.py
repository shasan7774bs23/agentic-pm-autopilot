import unittest

from pmauto.comment_lint import problems

GOOD = """## What the client needs
On the Order Summary page, the customer's delivery address should print automatically under
"Ship to", on both the standard and the gift versions of the receipt.

## Acceptance criteria
1. When a customer opens the receipt for an ACME order, the delivery address is filled in.
2. The address fits on one line on the printed PDF.
"""

BAD = """Change OrderSummaryDto in Reports/V5/OrderSummary.cs and add a SQL script.
The controller calls the API. `ShipTo` is null.
DEV - 0.75 / REV - 0.25
"""


class LintTest(unittest.TestCase):
    def test_plain_business_english_passes(self):
        self.assertEqual(problems(GOOD, ["ACME"]), [])   # the team's own acronym, from config "allowed_terms"

    def test_team_product_names_can_be_allowed(self):
        text = "Open the PayNow screen and check the total."
        self.assertTrue(problems(text))                       # CamelCase looks like code ...
        self.assertEqual(problems(text, ["PayNow"]), [])      # ... unless the team lists it in allowed_terms

    def test_technical_text_is_caught(self):
        kinds = {what for _, what, _ in problems(BAD, estimate_labels=["DEV", "REV", "QA"])}
        for expected in ("code-style identifier (e.g. CamelCase)", "file name", "file path", "technical term",
                         "code formatting (backticks)", "internal estimate"):
            self.assertIn(expected, kinds)

    def test_estimates_are_only_checked_for_the_teams_own_labels(self):
        self.assertNotIn("internal estimate", {w for _, w, _ in problems("DEV - 2", estimate_labels=[])})


if __name__ == "__main__":
    unittest.main()
