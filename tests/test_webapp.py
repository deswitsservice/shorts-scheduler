import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp import server


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

    def test_generated_thumbnail_served_and_platform_deduplicated(self):
        response = self.request(youtube_channel="UCtest", instagram_account="Creator", facebook_account="Page")
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


if __name__ == '__main__':
    unittest.main()
