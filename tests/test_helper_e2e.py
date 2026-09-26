import os, socket, sys, tempfile, threading, time, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from webapp import server
from helper import shorts_helper as helper


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HelperEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.dict(os.environ, {"SHORTS_ALLOW_REGISTRATION": "1"}), patch.object(server, "HELPER_MODE", True),
                  patch.object(helper, "HOME", Path(self.tmp.name) / "home"), patch.object(helper, "CONFIG", Path(self.tmp.name) / "home" / "config.json")):
            p.start(); self.addCleanup(p.stop)
        port = free_port()
        self.url = f"http://127.0.0.1:{port}"
        self.srv = uvicorn.Server(uvicorn.Config(server.create_app(Path(self.tmp.name) / "data", secure_cookie=False), host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=self.srv.run, daemon=True).start()
        for _ in range(50):
            if self.srv.started:
                break
            time.sleep(0.1)
        self.addCleanup(lambda: setattr(self.srv, "should_exit", True))
        self.web = httpx.Client(base_url=self.url, headers={"Origin": self.url, "X-Shorts-Request": "1"})

    def test_website_to_helper_round_trip(self):
        self.web.post("/api/auth/register", json={"email": "a@example.com", "password": "a long test password"}).raise_for_status()
        code = self.web.post("/api/helper/pair-code").json()["code"]
        helper.pair(self.url, code, "Test Mac")
        cfg = helper.load_config()
        self.assertEqual(self.web.get("/api/accounts").json()["helper"], {"paired": True, "online": False, "name": "Test Mac"})
        helper.request(cfg, "POST", "/api/helper-agent/poll", {"sessions": {"chrome": True, "youtube": True}})
        self.assertTrue(self.web.get("/api/accounts").json()["helper"]["online"])

        r = self.web.post("/api/schedule", data={"title": "Hello", "platforms": "youtube,tiktok", "mode": "now", "made_for_kids": "false", "dry": "true"},
                          files={"file": ("c.mp4", b"video-bytes", "video/mp4")})
        self.assertEqual(r.status_code, 200, r.text)
        jid = r.json()["id"]
        self.assertEqual(self.web.get("/api/jobs").json()[0]["steps"]["youtube"]["state"], "queued")

        reply = helper.request(cfg, "POST", "/api/helper-agent/poll", {})
        self.assertEqual(reply["job"]["id"], jid)
        seen = {}

        def fake_run_job(job):
            seen["bytes"] = Path(job["vid"]["file"]).read_bytes()
            for p in job["platforms"]:
                job["current_platform"] = p
                job["steps"][p]["log"].append({"t": time.time(), "msg": f"posting {p}"})
                job["steps"][p]["state"] = "done"
            job["current_platform"] = None

        fake_bot = SimpleNamespace(chrome_running=lambda: True, start_chrome=lambda: None)
        helper.run_job(cfg, reply["job"], fake_bot, SimpleNamespace(run_job=fake_run_job))
        self.assertEqual(seen["bytes"], b"video-bytes")
        job = self.web.get("/api/jobs").json()[0]
        self.assertEqual({p: job["steps"][p]["state"] for p in job["steps"]}, {"youtube": "done", "tiktok": "done"})
        self.assertIsNotNone(job["finished"])
        self.assertEqual(job["steps"]["tiktok"]["log"][-1]["msg"], "posting tiktok")

    def test_helper_with_wrong_token_is_rejected(self):
        with self.assertRaises(helper.ServerError):
            helper.request({"server": self.url, "token": "nope"}, "POST", "/api/helper-agent/poll", {})


if __name__ == "__main__":
    unittest.main()
