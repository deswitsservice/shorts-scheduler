"""Turn a voice recording + any number of images (+ optional script subtitles and music) into a vertical short.

The heavy lifting is video_build.py (ffmpeg); this module prepares its config. Images are spread evenly over the
recording. With subtitles on, it times each caption to the actual speech with faster-whisper and refuses to build
when the voice does not say the script.
"""
import difflib, json, os, re, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
MATCH_MIN = 0.85
END_PAD = 0.5
MIN_IMAGE_SECONDS = 1.0
GEORGIA = "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"
_model = None


class BuildError(Exception):
    pass


def _norm(text):
    return re.sub(r"[^a-z0-9' ]", "", text.lower().replace("’", "'")).split()


def split_captions(text, limit=64):
    """One scene's text -> short caption lines, broken at sentence ends, then commas, then by length."""
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    out = []
    for p in parts:
        while len(p) > limit:
            cut = max((m.end() for m in re.finditer(r"[,;:—]\s", p[:limit + 1])), default=0)
            if cut < 20:
                cut = p.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def split_script(script):
    scenes = [s.strip() for s in re.split(r"\n\s*\n", script.strip()) if s.strip()]
    return [split_captions(s) for s in scenes]


def transcribe(path):
    global _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise BuildError("The speech checker (faster-whisper) is not installed on this computer.") from exc
    if _model is None:
        _model = WhisperModel("base.en", device="cpu", compute_type="int8")
    segments, _ = _model.transcribe(str(path), word_timestamps=True, language="en")
    return [(re.sub(r"[^a-z0-9']", "", w.word.lower().replace("’", "'")), w.start, w.end)
            for s in segments for w in s.words]


def align(captions, words):
    """Return ([start, end] per caption in voice time, similarity 0-1)."""
    cw, owner = [], []
    for i, c in enumerate(captions):
        for w in _norm(c):
            cw.append(w)
            owner.append(i)
    sm = difflib.SequenceMatcher(None, cw, [w for w, _, _ in words], autojunk=False)
    mp = {}
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            mp[a + k] = b + k
    starts, ends = {}, {}
    for ci, oi in enumerate(owner):
        if ci in mp:
            starts.setdefault(oi, words[mp[ci]][1])
            ends[oi] = words[mp[ci]][2]
    ratio = sm.ratio()
    missing = [i for i in range(len(captions)) if i not in starts]
    if missing:
        raise BuildError(f"Couldn't find {len(missing)} of your {len(captions)} caption lines in the recording "
                         f"(similarity {ratio:.0%}). Check that the audio matches the script.")
    timings = []
    for i in range(len(captions)):
        start = 0.0 if i == 0 else starts[i]
        end = ends[i]
        if i + 1 < len(captions):
            end = max(end, min(starts[i + 1], end + 0.4))
        timings.append([round(start, 2), round(end, 2)])
    return timings, ratio


def audio_seconds(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise BuildError("Couldn't read the voice recording. Try exporting it again as mp3 or wav.") from None


def build(workdir, script, images, voice, subtitles=True, music=True, on_step=lambda m: None):
    """images: any number of paths, shown in order and spread evenly over the recording.
    Returns (final video path, report dict)."""
    workdir = Path(workdir)
    total = audio_seconds(voice) + END_PAD
    if total / len(images) < MIN_IMAGE_SECONDS:
        most = int(total // MIN_IMAGE_SECONDS)
        raise BuildError(f"Too many images for a {total:.0f}-second recording. Use {most} or fewer, "
                         f"so each image shows for at least {MIN_IMAGE_SECONDS:g} second.")
    captions, timings, ratio = [], [], None
    if subtitles:
        captions = [c for s in split_script(script) for c in s]
        if not captions:
            raise BuildError("Paste your script to add subtitles, or turn subtitles off.")
        on_step("Listening to your recording")
        words = transcribe(voice)
        timings, ratio = align(captions, words)
        if ratio < MATCH_MIN:
            raise BuildError(f"The recording doesn't match your script (similarity {ratio:.0%}). "
                             "Make sure you uploaded the right audio for this script.")
    font = GEORGIA if os.path.exists(GEORGIA) else str(ROOT / "fonts" / "Caveat.ttf")
    cfg = {"output": "final.mp4", "size": [1080, 1920], "fps": 30, "crossfade": 0.3, "zoom_end": 1.06, "end_pad": END_PAD,
           "voice": str(voice), "bed": str(ROOT / "music.wav") if music else None, "bed_start": 0, "bed_gain": 0.3,
           "font": font, "font_size": 50 if font == GEORGIA else 62, "caption_bottom_margin": 480,
           "caption_max_width": 760, "caption_lead": 0.05, "caption_tail": 0.3, "silence_db": -35, "silence_min": 0.25,
           "timings": timings, "captions": captions, "scenes": [{"image": str(img), "captions": []} for img in images]}
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "config.json").write_text(json.dumps(cfg, indent=2))
    on_step("Building the video")
    r = subprocess.run([sys.executable, str(HERE / "video_build.py"), "--config", str(workdir / "config.json")],
                       capture_output=True, text=True)
    if r.returncode:
        tail = (r.stdout + r.stderr).strip().splitlines()[-1:] or ["unknown error"]
        raise BuildError(tail[0][:300])
    m = re.findall(r"WROTE (.+)", r.stdout)
    if not m:
        raise BuildError("The video builder finished without a result.")
    return m[-1].strip(), {"match": round(ratio, 3) if ratio is not None else None, "captions": len(captions),
                           "images": len(images), "music": bool(music)}
