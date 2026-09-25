import os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from webapp.engine import fit_caption, fit_title, count_hashtags, CAPTION_LIMITS, INSTAGRAM_MAX_HASHTAGS


class FitCaptionTests(unittest.TestCase):
    def test_short_caption_untouched(self):
        text = "Hello world.\n\n#a #b"
        self.assertEqual(fit_caption("instagram", text), (text, None))

    def test_trims_body_and_keeps_hashtags(self):
        tags = "#healing #selfcare #shorts"
        text = ("word " * 800).strip() + "\n\n" + tags
        out, note = fit_caption("instagram", text)
        self.assertLessEqual(len(out), CAPTION_LIMITS["instagram"])
        self.assertTrue(out.endswith(tags))
        self.assertIn("…", out)
        self.assertIn("trimmed", note)

    def test_each_platform_uses_its_own_limit(self):
        text = "x " * 2500  # 5000 chars
        ig, _ = fit_caption("instagram", text)
        tt, _ = fit_caption("tiktok", text)
        yt, _ = fit_caption("youtube", text)
        self.assertLessEqual(len(ig), 2200)
        self.assertLessEqual(len(tt), 4000)
        self.assertGreater(len(tt), len(ig))
        self.assertLessEqual(len(yt), 5000)

    def test_facebook_has_no_cap(self):
        text = "y " * 6000
        self.assertEqual(fit_caption("facebook", text), (text, None))

    def test_instagram_drops_extra_hashtags(self):
        tags = " ".join(f"#t{i}" for i in range(40))
        out, note = fit_caption("instagram", "Body text.\n\n" + tags)
        self.assertEqual(count_hashtags(out), INSTAGRAM_MAX_HASHTAGS)
        self.assertIn("10 hashtags dropped", note)
        self.assertTrue(out.startswith("Body text."))
        self.assertIn("#t0", out)
        self.assertNotIn("#t39", out)

    def test_huge_hashtag_block_is_dropped_not_the_whole_caption(self):
        text = "Body. " * 50 + "\n\n" + " ".join(f"#verylongtagname{i}" for i in range(200))
        out, _ = fit_caption("tiktok", text)
        self.assertLessEqual(len(out), 4000)
        self.assertIn("Body.", out)

    def test_title_trim(self):
        self.assertEqual(fit_title("short"), ("short", None))
        title, note = fit_title("word " * 40)
        self.assertLessEqual(len(title), 100)
        self.assertTrue(title.endswith("…"))
        self.assertIn("YouTube's limit is 100", note)


if __name__ == "__main__":
    unittest.main()
