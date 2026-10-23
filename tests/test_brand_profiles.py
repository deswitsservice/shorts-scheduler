"""Brand profiles ("Post as"): the owner's brands fill in channel/Page/Instagram per post; nobody else can use them."""
import json, os, sys, tempfile, unittest

TMP = tempfile.mkdtemp()
os.environ["SHORTS_DATA_DIR"] = TMP
os.environ["SHORTS_OWNER_EMAIL"] = "Owner@Example.com"
os.environ["SHORTS_PROFILES"] = json.dumps({
    "shorts": {"label": "Paper Crypt (Shorts)", "format": "short", "platforms": ["youtube", "instagram", "facebook"],
               "youtube_channel": "UCshorts", "youtube_channel_name": "Paper Crypt", "meta_asset_id": "111",
               "facebook_account": "Paper Crypt", "instagram_account": "papercrypttales"},
    "long": {"label": "Cold Case (Long)", "format": "long", "platforms": ["youtube", "facebook"],
             "youtube_channel": "UClong", "youtube_channel_name": "Cold Case Chronicles", "meta_asset_id": "222",
             "facebook_account": "Cold Case Chronicles", "instagram_account": ""},
})
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "webapp")]

import engine  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def as_user(email):
    """The local helper's guard puts the signed-in Supabase user on scope state; do the same here."""
    async def app(scope, receive, send):
        if scope["type"] == "http":
            scope.setdefault("state", {})["user"] = {"id": "u1", "email": email}
        await engine.app(scope, receive, send)
    return TestClient(app)


class BrandProfileTests(unittest.TestCase):
    def setUp(self):
        self.submitted = []
        self.orig_submit = engine.executor.submit
        engine.executor.submit = lambda fn, job: self.submitted.append(job)  # never run a real posting job
        self.orig_thumb = engine.bot.prepare_thumbnail
        engine.bot.prepare_thumbnail = lambda vid: None

    def tearDown(self):
        # Restore both: other test modules share the same imported engine/bot.
        engine.executor.submit = self.orig_submit
        engine.bot.prepare_thumbnail = self.orig_thumb

    def post(self, client, **form):
        data = {"title": "T", "platforms": "youtube,facebook", "mode": "now", "made_for_kids": "false", **form}
        return client.post("/api/schedule", data=data, files={"file": ("v.mp4", b"x", "video/mp4")})

    def test_owner_gets_the_brand_accounts(self):
        r = self.post(as_user("owner@example.com"), profile="long")
        self.assertEqual(r.status_code, 200, r.text)
        vid = self.submitted[-1]["vid"]
        self.assertEqual(vid["youtube_channel"], "UClong")
        self.assertEqual(vid["youtube_channel_name"], "Cold Case Chronicles")
        self.assertEqual(vid["meta_asset_id"], "222")
        self.assertEqual(vid["facebook_account"], "Cold Case Chronicles")
        self.assertEqual(self.submitted[-1]["profile"], "Cold Case (Long)")

    def test_someone_else_cannot_use_a_brand(self):
        r = self.post(as_user("stranger@example.com"), profile="shorts")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.submitted, [])

    def test_brand_refuses_platforms_it_has_no_account_for(self):
        r = self.post(as_user("owner@example.com"), profile="long", platforms="youtube,instagram")
        self.assertEqual(r.status_code, 400)
        self.assertIn("instagram", r.json()["error"])

    def test_unknown_brand_is_refused(self):
        self.assertEqual(self.post(as_user("owner@example.com"), profile="nope").status_code, 403)

    def test_no_brand_behaves_as_before(self):
        r = self.post(as_user("stranger@example.com"))
        self.assertEqual(r.status_code, 200, r.text)
        vid = self.submitted[-1]["vid"]
        self.assertEqual(vid["meta_asset_id"], "")
        self.assertEqual(vid["youtube_channel_name"], "")


if __name__ == "__main__":
    unittest.main()
