import unittest

from pmauto.adf import strip_machine_block, to_adf

SAMPLE = """# Requirement Analysis - APP-1

## Assessment
READY - the **form** field `CustomerName` exists.

| Rule | Check |
|---|---|
| R1 | name prints |

- one
- two

1. first
2. second

<!-- machine-readable, keep last -->
FILES: a.cs
VERDICT: READY
QUESTIONS: 0
"""


class AdfTest(unittest.TestCase):
    def test_machine_block_never_posted(self):
        out = strip_machine_block(SAMPLE)
        self.assertNotIn("VERDICT", out)
        self.assertNotIn("<!--", out)

    def test_structure(self):
        doc = to_adf(strip_machine_block(SAMPLE))
        kinds = [n["type"] for n in doc["content"]]
        self.assertEqual(kinds, ["heading", "heading", "paragraph", "table", "bulletList", "orderedList"])
        marks = [t.get("marks") for t in doc["content"][2]["content"]]
        self.assertIn([{"type": "strong"}], marks)
        self.assertIn([{"type": "code"}], marks)
        table = doc["content"][3]
        self.assertEqual(len(table["content"]), 2)            # separator row dropped
        self.assertEqual(table["content"][0]["content"][0]["type"], "tableHeader")


if __name__ == "__main__":
    unittest.main()
