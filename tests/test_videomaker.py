import io, sys, time, unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "webapp"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import videomaker
from webapp import engine as server


class SplitTests(unittest.TestCase):
    def test_scenes_split_on_blank_lines_and_captions_are_short(self):
        scenes = videomaker.split_script("One. Two is a bit longer, with a comma in it, and it keeps going on and on for a while.\n\nThree.")
        self.assertEqual(len(scenes), 2)
        self.assertTrue(all(len(c) <= 64 for s in scenes for c in s))
        self.assertEqual(scenes[1], ["Three."])

    def test_align_rejects_wrong_audio(self):
        words = [(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate("totally different words being said here".split())]
        with self.assertRaises(videomaker.BuildError):
            videomaker.align(["I used to think one more conversation would help"], words)

    def test_align_times_each_caption_to_speech(self):
        caps = ["hello there friend", "how are you"]
        words = [(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate("hello there friend how are you".split())]
        timings, ratio = videomaker.align(caps, words)
        self.assertEqual(ratio, 1.0)
        self.assertEqual(timings[0][0], 0.0)
        self.assertAlmostEqual(timings[1][0], 1.5)

    def test_image_count_must_match_scene_count(self):
        with self.assertRaises(videomaker.BuildError) as ctx:
            videomaker.build("/tmp/x_unused", "A.\n\nB.", ["only_one.png"], "v.mp3")
        self.assertIn("2 scene", str(ctx.exception))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patch.object(server, "UPLOADS", self.tmp.name).start()
        self.submit = patch.object(server.build_executor, "submit").start()
        self.addCleanup(patch.stopall)
        server.video_jobs.clear()
        self.client = TestClient(server.app)

    def files(self, voice="voice.mp3", image="a.png"):
        return [("voice", (voice, b"x", "audio/mpeg")), ("images", (image, b"x", "image/png"))]

    def test_rejects_bad_types_and_empty_script(self):
        self.assertEqual(self.client.post("/api/video/create", data={"script": " "}, files=self.files()).status_code, 400)
        self.assertEqual(self.client.post("/api/video/create", data={"script": "Hi."}, files=self.files(voice="v.exe")).status_code, 400)
        self.assertEqual(self.client.post("/api/video/create", data={"script": "Hi."}, files=self.files(image="a.gif")).status_code, 400)
        self.submit.assert_not_called()

    def test_accepts_and_reports_status(self):
        r = self.client.post("/api/video/create", data={"script": "Hi."}, files=self.files())
        self.assertEqual(r.status_code, 200)
        self.submit.assert_called_once()
        self.assertEqual(self.client.get("/api/video/" + r.json()["id"]).json()["state"], "running")

    def test_failed_build_surfaces_the_reason_and_cleans_up(self):
        r = self.client.post("/api/video/create", data={"script": "Hi."}, files=self.files())
        vj = server.video_jobs[r.json()["id"]]
        with patch.object(server.videomaker, "build", side_effect=videomaker.BuildError("The recording doesn't match your script (similarity 10%).")):
            server._run_video_build(vj)
        st = self.client.get("/api/video/" + vj["id"]).json()
        self.assertEqual(st["state"], "failed")
        self.assertIn("doesn't match", st["error"])
        self.assertFalse(Path(vj["dir"]).exists())


if __name__ == "__main__":
    unittest.main()
