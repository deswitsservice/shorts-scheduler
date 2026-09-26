import os, sys, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot


class ViewportTests(unittest.TestCase):
    def test_default_and_override_and_bad_values(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SHORTS_VIEWPORT", None)
            self.assertEqual(bot._viewport(), {"width": 1280, "height": 800})
        with patch.dict(os.environ, {"SHORTS_VIEWPORT": "1440x900"}):
            self.assertEqual(bot._viewport(), {"width": 1440, "height": 900})
        for bad in ("300x300", "abc", "1280", "99999x99999"):
            with patch.dict(os.environ, {"SHORTS_VIEWPORT": bad}):
                self.assertEqual(bot._viewport(), {"width": 1280, "height": 800})

    def test_every_automation_page_gets_the_fixed_viewport(self):
        pg = MagicMock()
        with patch.object(bot, "_restore_if_minimized"):
            bot._auto_dismiss_dialogs(pg)
        pg.set_viewport_size.assert_called_once_with({"width": 1280, "height": 800})

    def test_a_failing_viewport_call_never_blocks_a_job(self):
        pg = MagicMock(); pg.set_viewport_size.side_effect = RuntimeError("boom")
        with patch.object(bot, "_restore_if_minimized"):
            self.assertIs(bot._auto_dismiss_dialogs(pg), pg)


if __name__ == "__main__":
    unittest.main()
