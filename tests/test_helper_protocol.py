import os, tempfile, time, unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from webapp import server
from webapp.server import create_app


class HelperProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.dict(os.environ, {'SHORTS_ALLOW_REGISTRATION': '1'}), patch.object(server, 'HELPER_MODE', True)):
            p.start(); self.addCleanup(p.stop)
        self.app = create_app(self.tmp.name, secure_cookie=False)
        self.user = TestClient(self.app)
        self.other = TestClient(self.app)
        self.h = {'Origin': 'http://testserver', 'X-Shorts-Request': '1'}
        self.uid = self.register(self.user, 'a@example.com')
        self.oid = self.register(self.other, 'b@example.com')

    def register(self, client, email):
        r = client.post('/api/auth/register', headers=self.h, json={'email': email, 'password': 'a long test password'})
        return r.json()['id']

    def pair(self, client=None):
        code = (client or self.user).post('/api/helper/pair-code', headers=self.h).json()['code']
        r = TestClient(self.app).post('/api/helper/pair', json={'code': code, 'name': 'Test Mac'})
        self.assertEqual(r.status_code, 200, r.text)
        return {'Authorization': 'Bearer ' + r.json()['token']}

    def add_job(self, uid, jid='j1'):
        engine = self.app.state.tenants.workspace(uid).engine
        video = os.path.join(self.tmp.name, jid + '.mp4')
        open(video, 'wb').write(b'video-bytes')
        engine.jobs[jid] = dict(id=jid, title='T', when='Now', platforms=['youtube', 'tiktok'], dry=False, created=time.time(),
                                steps={p: {'state': 'queued', 'log': []} for p in ('youtube', 'tiktok')}, started=None, finished=None,
                                current_platform=None, thumbnail_url=None, accounts={}, helper_claim=None,
                                vid={'file': video, 'title': 'T', 'description': 'd', 'schedule': None, 'publish_now': True})
        return engine.jobs[jid]

    def test_pairing_code_is_one_time_and_bad_codes_fail(self):
        code = self.user.post('/api/helper/pair-code', headers=self.h).json()['code']
        c = TestClient(self.app)
        self.assertEqual(c.post('/api/helper/pair', json={'code': 'WRONG-CODE'}).status_code, 400)
        self.assertEqual(c.post('/api/helper/pair', json={'code': code, 'name': 'x'}).status_code, 200)
        self.assertEqual(c.post('/api/helper/pair', json={'code': code, 'name': 'x'}).status_code, 400)

    def test_pair_code_requires_sign_in(self):
        self.assertEqual(TestClient(self.app).post('/api/helper/pair-code', headers=self.h).status_code, 401)

    def test_agent_routes_reject_missing_or_bad_tokens(self):
        c = TestClient(self.app)
        self.assertEqual(c.post('/api/helper-agent/poll', json={}).status_code, 401)
        self.assertEqual(c.post('/api/helper-agent/poll', json={}, headers={'Authorization': 'Bearer nope'}).status_code, 401)

    def test_poll_claims_job_once_and_marks_helper_online(self):
        auth = self.pair()
        self.add_job(self.uid)
        self.assertFalse(self.user.get('/api/accounts').json()['helper']['online'])
        c = TestClient(self.app)
        first = c.post('/api/helper-agent/poll', headers=auth, json={'sessions': {'youtube': True}}).json()['job']
        self.assertEqual(first['id'], 'j1')
        self.assertEqual(first['platforms'], ['youtube', 'tiktok'])
        self.assertIsNone(c.post('/api/helper-agent/poll', headers=auth, json={}).json()['job'])
        acc = self.user.get('/api/accounts').json()
        self.assertTrue(acc['helper']['online']); self.assertTrue(acc['youtube']); self.assertFalse(acc['tiktok'])

    def test_helpers_only_see_their_own_users_jobs_and_media(self):
        auth_other = self.pair(self.other)
        self.add_job(self.uid)
        c = TestClient(self.app)
        self.assertIsNone(c.post('/api/helper-agent/poll', headers=auth_other, json={}).json()['job'])
        self.assertEqual(c.get('/api/helper-agent/jobs/j1/video', headers=auth_other).status_code, 404)
        self.assertEqual(c.post('/api/helper-agent/jobs/j1/sync', headers=auth_other, json={'finished': True}).status_code, 404)

    def test_sync_updates_job_and_video_download_works_for_claimer_only(self):
        auth = self.pair()
        job = self.add_job(self.uid)
        c = TestClient(self.app)
        self.assertEqual(c.get('/api/helper-agent/jobs/j1/video', headers=auth).status_code, 404)  # not claimed yet
        c.post('/api/helper-agent/poll', headers=auth, json={})
        self.assertEqual(c.get('/api/helper-agent/jobs/j1/video', headers=auth).content, b'video-bytes')
        r = c.post('/api/helper-agent/jobs/j1/sync', headers=auth, json={
            'steps': {'youtube': {'state': 'done', 'log': [{'t': 1, 'msg': 'ok'}]}, 'tiktok': {'state': 'failed', 'error': 'nope', 'attempt': 4},
                      'bogus': {'state': 'done'}, 'x': 'y'},
            'current_platform': None, 'finished': True, 'accounts': {'youtube': 'Amoura'}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(job['steps']['youtube']['state'], 'done')
        self.assertEqual(job['steps']['tiktok']['error'], 'nope')
        self.assertIsNotNone(job['finished'])
        self.assertEqual(job['accounts'], {'youtube': 'Amoura'})
        self.assertEqual(self.user.get('/api/jobs').json()[0]['steps']['youtube']['state'], 'done')

    def test_invalid_state_is_ignored(self):
        auth = self.pair(); job = self.add_job(self.uid); c = TestClient(self.app)
        c.post('/api/helper-agent/poll', headers=auth, json={})
        c.post('/api/helper-agent/jobs/j1/sync', headers=auth, json={'steps': {'youtube': {'state': 'hacked'}}})
        self.assertEqual(job['steps']['youtube']['state'], 'queued')

    def test_revoked_helper_is_locked_out(self):
        auth = self.pair()
        hid = self.user.get('/api/helper/list').json()[0]['id']
        self.assertTrue(self.user.post(f'/api/helper/revoke/{hid}', headers=self.h).json()['ok'])
        self.assertEqual(TestClient(self.app).post('/api/helper-agent/poll', headers=auth, json={}).status_code, 401)

    def test_schedule_refused_when_helper_offline(self):
        r = self.user.post('/api/schedule', headers=self.h, data={'title': 'T', 'platforms': 'youtube', 'mode': 'now', 'made_for_kids': 'false'},
                           files={'file': ('c.mp4', b'x', 'video/mp4')})
        self.assertEqual(r.status_code, 409)
        self.assertIn('helper is offline', r.json()['error'])

    def test_job_history_survives_restart_and_stale_claim_is_reassigned(self):
        auth = self.pair(); self.add_job(self.uid); c = TestClient(self.app)
        c.post('/api/helper-agent/poll', headers=auth, json={})
        restarted = TestClient(create_app(self.tmp.name, secure_cookie=False))
        restarted.cookies.set(server.COOKIE, self.user.cookies.get(server.COOKIE))
        jobs = restarted.get('/api/jobs').json()
        self.assertEqual([j['id'] for j in jobs], ['j1'])
        with patch.object(server, 'CLAIM_STALE_SECONDS', -1):
            self.assertEqual(TestClient(restarted.app).post('/api/helper-agent/poll', headers=auth, json={}).json()['job']['id'], 'j1')


if __name__ == '__main__':
    unittest.main()
