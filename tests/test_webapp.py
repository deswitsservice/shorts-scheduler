import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp import engine as server


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.uploads = patch.object(server, 'UPLOADS', self.tmp.name)
        self.uploads.start()
        self.addCleanup(self.uploads.stop)
        self.submit = patch.object(server.executor, 'submit').start()
        self.addCleanup(patch.stopall)
        server.jobs.clear()
        self.client = TestClient(server.app)

    def test_start_reports_failure_and_does_not_navigate_existing_tabs(self):
        with patch.object(server.bot, 'start_chrome'), patch.object(server.bot, 'chrome_running', return_value=False), patch.object(server.bot, 'show_idle_screen') as park:
            response = self.client.post('/api/start')
        self.assertEqual(response.status_code, 503)
        self.assertIn('error', response.json())
        park.assert_not_called()

    def request(self, **changes):
        data = dict(title='A small promise', platforms='youtube,youtube',
                    mode='now', made_for_kids='false', dry='true')
        data.update(changes)
        image = io.BytesIO()
        Image.new('RGB', (20, 30)).save(image, format='PNG')
        return self.client.post('/api/schedule', data=data, files={
            'file': ('clip.mp4', b'placeholder; browser execution mocked', 'video/mp4'),
            'thumbnail': ('cover.png', image.getvalue(), 'image/png')})

    def test_uploaded_thumbnail_is_used_exactly_as_given_by_default(self):
        response = self.request()
        self.assertEqual(response.status_code, 200)
        job = server.jobs[response.json()['id']]
        self.addCleanup(lambda: Path(job['vid']['thumbnail']).unlink(missing_ok=True))
        self.assertTrue(job['thumbnail_url'].startswith('/uploads/'))
        self.assertNotIn('_thumb_done', job['vid'])  # no title was composed onto it
        # (The /uploads route itself is mounted per workspace in server.py, so check the file, not an HTTP fetch.)
        self.assertTrue(job['vid']['thumbnail'].startswith(self.tmp.name))
        self.assertEqual(Image.open(job['vid']['thumbnail']).size, (20, 30))  # untouched, not the 1080x1920 composite

    def test_generated_thumbnail_served_and_platform_deduplicated(self):
        response = self.request(youtube_channel="UCtest", instagram_account="Creator", facebook_account="Page", thumb_title='true')
        self.assertEqual(response.status_code, 200)
        job = server.jobs[response.json()['id']]
        self.addCleanup(lambda: Path(job['vid']['thumbnail']).unlink(missing_ok=True))
        self.assertEqual(job['platforms'], ['youtube'])
        self.assertEqual(job['vid']['youtube_channel'], 'UCtest')
        self.assertEqual(job['vid']['instagram_account'], 'Creator')
        self.assertEqual(job['vid']['facebook_account'], 'Page')
        job['vid']['accounts']['youtube'] = 'UCtest'
        self.assertEqual(self.client.get('/api/jobs').json()[0]['accounts'], {'youtube': 'UCtest'})
        self.assertTrue(job['thumbnail_url'].startswith('/thumbs/'))
        image = self.client.get(job['thumbnail_url'])
        self.assertEqual(image.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(image.content)).size, (1080, 1920))
        self.submit.assert_called_once()

    def test_ai_label_is_off_unless_asked_for(self):
        job = server.jobs[self.request().json()['id']]
        self.addCleanup(lambda: Path(job['vid']['thumbnail']).unlink(missing_ok=True))
        self.assertFalse(job['vid']['ai_label'])
        job = server.jobs[self.request(ai_label='true').json()['id']]
        self.addCleanup(lambda: Path(job['vid']['thumbnail']).unlink(missing_ok=True))
        self.assertTrue(job['vid']['ai_label'])

    def test_invalid_mode_never_queues(self):
        self.assertEqual(self.request(mode='invalid').status_code, 400)
        self.submit.assert_not_called()

    def test_thumbnail_failure_cleans_uploads_and_does_not_queue(self):
        with patch.object(server.bot, 'prepare_thumbnail', side_effect=ValueError('bad image')):
            response = self.request()
        self.assertEqual(response.status_code, 400)
        self.assertIn('thumbnail', response.json()['error'])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])
        self.submit.assert_not_called()


class RetryTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)
        server.jobs.clear()
        self.job = {"id": "r1", "title": "T", "when": "Now", "platforms": ["youtube", "tiktok"], "dry": False,
                    "vid": {"schedule": None, "title": "T", "description": "d"}, "accounts": {}, "created": 1, "started": 1, "finished": 2, "current_platform": None,
                    "thumbnail_url": None, "steps": {"youtube": {"state": "done", "log": []}, "tiktok": {"state": "failed", "log": [], "error": "x"}}}
        server.jobs["r1"] = self.job
        self.submit = patch.object(server.executor, "submit").start()
        self.addCleanup(patch.stopall)

    def test_retry_requeues_only_failed_platforms(self):
        r = self.client.post("/api/jobs/r1/retry")
        self.assertEqual(r.json()["retrying"], ["tiktok"])
        self.assertEqual(self.job["steps"]["youtube"]["state"], "done")
        self.assertEqual(self.job["steps"]["tiktok"]["state"], "queued")
        self.assertIsNone(self.job["finished"])
        self.submit.assert_called_once()

    def test_cancel_before_start_finishes_job_and_skips_posting(self):
        self.job["started"] = None; self.job["finished"] = None
        self.job["steps"] = {p: {"state": "queued", "log": []} for p in ("youtube", "tiktok")}
        r = self.client.post("/api/jobs/r1/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(self.job["finished"])
        self.assertTrue(all(s["state"] == "failed" and s["error"] == "Cancelled" for s in self.job["steps"].values()))
        with patch.object(server.bot, "chrome_running", return_value=True), patch.object(server.bot, "youtube") as yt, patch.object(server.bot, "show_idle_screen"):
            server.run_job(self.job)
        yt.assert_not_called()

    def test_cancel_stops_a_running_flow_at_the_next_step_without_retrying(self):
        self.job["finished"] = None; self.job["started"] = None
        self.job["steps"] = {p: {"state": "queued", "log": []} for p in ("youtube", "tiktok")}
        self.job["platforms"] = ["youtube", "tiktok"]
        calls = []

        def flow(vid, commit, on_step):
            calls.append(1)
            on_step("uploading")
            self.job["cancel"] = True  # user clicks cancel mid-upload
            on_step("still uploading")  # next step boundary raises
            calls.append("unreachable")

        with patch.object(server.bot, "chrome_running", return_value=False), patch.object(server.bot, "youtube", side_effect=flow), \
                patch.object(server.bot, "tiktok") as tt, patch.object(server.bot, "show_idle_screen"), patch.object(server, "_close_active_tab") as close:
            with patch.object(server.bot, "chrome_running", return_value=True):
                server.run_job(self.job)
        self.assertEqual(calls, [1])
        tt.assert_not_called()
        self.assertEqual(self.job["steps"]["youtube"]["error"], "Cancelled")
        self.assertEqual(self.job["steps"]["tiktok"]["error"], "Cancelled")
        close.assert_called_once()
        self.assertIsNotNone(self.job["finished"])

    def test_cancel_rejected_for_finished_job(self):
        self.assertEqual(self.client.post("/api/jobs/r1/cancel").status_code, 409)

    def test_retry_rejected_when_running_or_nothing_failed(self):
        self.job["finished"] = None
        self.assertEqual(self.client.post("/api/jobs/r1/retry").status_code, 409)
        self.job["finished"] = 2; self.job["steps"]["tiktok"]["state"] = "done"
        self.assertEqual(self.client.post("/api/jobs/r1/retry").status_code, 400)

    def test_auto_retries_three_times_then_fails(self):
        self.job["steps"]["tiktok"] = {"state": "queued", "log": []}
        self.job["steps"]["youtube"]["state"] = "done"
        calls = []
        def boom(*a, **k): calls.append(1); raise SystemExit("nope")
        with patch.object(server.bot, "chrome_running", return_value=True), patch.object(server.bot, "tiktok", side_effect=boom), \
                patch.object(server.bot, "show_idle_screen"), patch.object(server.time, "sleep"):
            server.run_job(self.job)
        self.assertEqual(len(calls), 4)  # first try + 3 retries
        self.assertEqual(self.job["steps"]["tiktok"]["state"], "failed")

    def test_auto_retry_stops_on_success(self):
        self.job["steps"]["tiktok"] = {"state": "queued", "log": []}
        seq = [SystemExit("once"), None]
        def flaky(*a, **k):
            r = seq.pop(0)
            if r: raise r
        with patch.object(server.bot, "chrome_running", return_value=True), patch.object(server.bot, "tiktok", side_effect=flaky), \
                patch.object(server.bot, "show_idle_screen"), patch.object(server.time, "sleep"):
            server.run_job(self.job)
        self.assertEqual(self.job["steps"]["tiktok"]["state"], "done")


if __name__ == '__main__':
    unittest.main()
