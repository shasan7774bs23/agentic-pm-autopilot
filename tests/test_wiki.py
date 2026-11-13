import unittest
from pmauto.wiki import to_wiki


class TestWiki(unittest.TestCase):
    def test_headings_lists_bold_and_image_after_where(self):
        md = "## Summary\nText **bold**.\n\n## Where\nThe page.\n\n## What should happen\n1. One\n- Two\n"
        w = to_wiki(md, ["C:/x/shot.png"])
        self.assertIn("h3. Summary", w)
        self.assertIn("Text *bold*.", w)
        self.assertIn("# One", w)
        self.assertIn("* Two", w)
        self.assertLess(w.index("The page."), w.index("!shot.png|width=900!"))
        self.assertLess(w.index("!shot.png|width=900!"), w.index("h3. What should happen"))

    def test_image_at_end_when_no_where(self):
        self.assertTrue(to_wiki("## Summary\nx\n", ["a.png"]).rstrip().endswith("!a.png|width=900!"))


if __name__ == "__main__":
    unittest.main()
