#!/usr/bin/env python3
"""Build a vertical love-quote short from config.json.

  python3 build.py --detect     print speech segments found in the voice file
  python3 build.py --preview    silent preview (no audio needed)
  python3 build.py              full render with narration
"""
import argparse, json, os, re, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def detect_segments(path, db, min_len):
    total = duration(path)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-af",
                        f"silencedetect=noise={db}dB:d={min_len}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    segs, cur = [], 0.0
    for i, s in enumerate(starts):
        if s - cur > 0.05:
            segs.append([cur, s])
        cur = ends[i] if i < len(ends) else total
    if total - cur > 0.05:
        segs.append([cur, total])
    return segs, total


def align_segments(segs, texts):
    """Group speech segments into one group per caption: DP over expected duration
    (from character count) with a penalty for splitting at short pauses."""
    n, k = len(segs), len(texts)
    chars = [len(t) for t in texts]
    rate = sum(b - a for a, b in segs) / sum(chars)
    INF = float("inf")
    dp = [[INF] * (n + 1) for _ in range(k + 1)]
    back = [[0] * (n + 1) for _ in range(k + 1)]
    dp[0][0] = 0.0
    for j in range(1, k + 1):
        e = rate * chars[j - 1]
        for i in range(j, n + 1):
            for l in range(j - 1, i):
                if dp[j - 1][l] == INF:
                    continue
                actual = sum(b - a for a, b in segs[l:i])
                cost = ((actual - e) / (0.3 * e + 0.5)) ** 2
                if l > 0:
                    gap = segs[l][0] - segs[l - 1][1]
                    cost += 2.0 * max(0.0, 1 - gap / 1.0) ** 2
                if dp[j - 1][l] + cost < dp[j][i]:
                    dp[j][i] = dp[j - 1][l] + cost
                    back[j][i] = l
    out, i = [], n
    for j in range(k, 0, -1):
        l = back[j][i]
        out.append([segs[l][0], segs[i - 1][1]])
        i = l
    return out[::-1]


def wrap(text, font, max_w):
    words = text.split()

    def lines_for(width):
        out, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if font.getlength(t) <= width or not cur:
                cur = t
            else:
                out.append(cur)
                cur = w
        out.append(cur)
        return out

    base = lines_for(max_w)
    lo, hi = 1, max_w
    while lo < hi:  # narrowest width that keeps the same line count = balanced lines
        mid = (lo + hi) // 2
        if len(lines_for(mid)) <= len(base):
            hi = mid
        else:
            lo = mid + 1
    return lines_for(hi)


def render_caption(text, cfg, out):
    font = ImageFont.truetype(cfg["font"], cfg["font_size"])
    lines = wrap(text, font, cfg["caption_max_width"])
    px, py, gap = 45, 32, 16
    boxes = [font.getbbox(l) for l in lines]
    heights = [b[3] - b[1] for b in boxes]
    widths = [b[2] - b[0] for b in boxes]
    w = max(widths) + 2 * px
    h = sum(heights) + gap * (len(lines) - 1) + 2 * py
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=24, fill=(0, 0, 0, 120))
    y = py
    for l, b, lh, lw in zip(lines, boxes, heights, widths):
        d.text(((w - lw) / 2 - b[0], y - b[1]), l, font=font, fill=(255, 255, 255, 255))
        y += lh + gap
    img.save(out)
    return img.size, len(lines)


ENDCARD_LINES = ["Support these videos", "ko-fi.com/amouraquotes"]
ENDCARD_SECONDS = 3.0


def render_endcard(cfg, out):
    """Two-line Ko-fi card in the caption style, smaller; shown near the top over the last few seconds."""
    lines = cfg.get("endcard_lines", ENDCARD_LINES)
    fonts = [ImageFont.truetype(cfg["font"], int(cfg["font_size"] * 0.75)),
             ImageFont.truetype(cfg["font"], int(cfg["font_size"] * 0.9))]
    px, py, gap = 40, 26, 14
    boxes = [f.getbbox(l) for f, l in zip(fonts, lines)]
    w = max(b[2] - b[0] for b in boxes) + 2 * px
    h = sum(b[3] - b[1] for b in boxes) + gap + 2 * py
    img = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=24, fill=(0, 0, 0, 140))
    y = py
    for f, l, b, col in zip(fonts, lines, boxes, [(255, 255, 255, 220), (255, 214, 140, 255)]):
        d.text(((w - (b[2] - b[0])) / 2 - b[0], y - b[1]), l, font=f, fill=col)
        y += b[3] - b[1] + gap
    img.save(out)
    return img.size


def unique_path(path):
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base}_v{n}{ext}"):
        n += 1
    return f"{base}_v{n}{ext}"


def audio_graph(cfg, ai, total):
    inputs = ["-i", cfg["voice"]]
    af = (f"[{ai}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
          f"apad=whole_dur={total:.3f}[voice]")
    if cfg.get("bed") and os.path.exists(cfg["bed"]):
        # Start early enough that the bed never runs out under a long recording; loop only if it is shorter than the video.
        bed_len = duration(cfg["bed"])
        start = min(cfg.get("bed_start", 0), max(0.0, bed_len - total))
        inputs += (["-stream_loop", "-1"] if total > bed_len else []) + ["-ss", f"{start:.3f}", "-i", cfg["bed"]]
        af += (f";[{ai + 1}:a]aformat=sample_rates=44100:channel_layouts=stereo,"
               f"volume={cfg['bed_gain']},atrim=0:{total:.3f},afade=t=in:d=1.5,"
               f"afade=t=out:st={total - 2:.3f}:d=2[bed];"
               f"[voice][bed]amix=inputs=2:normalize=0,afade=t=out:st={total - 0.5:.3f}:d=0.5[aout]")
    else:
        af += f";[voice]afade=t=out:st={total - 0.5:.3f}:d=0.5[aout]"
    return inputs, af


def latest_render(name):
    base, ext = os.path.splitext(name)
    best, n = None, 1
    while True:
        cand = name if n == 1 else f"{base}_v{n}{ext}"
        if os.path.exists(cand):
            best = cand
        elif n > 1:
            return best
        n += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(SCRIPT_DIR, "config.json"))
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--remix", action="store_true", help="re-mix audio (music level etc.) onto the latest render without re-rendering video")
    args = ap.parse_args()
    cfg = json.load(open(args.config))
    global HERE, BUILD
    HERE = os.path.dirname(os.path.abspath(args.config))
    BUILD = os.path.join(HERE, "build")
    os.chdir(HERE)
    os.makedirs(BUILD, exist_ok=True)

    if args.remix:
        src = latest_render(cfg["output"])
        if not src:
            sys.exit(f"No existing render of {cfg['output']} to remix.")
        total = duration(cfg["voice"]) + cfg["end_pad"]
        extra, af = audio_graph(cfg, 1, total)
        out = unique_path(os.path.join(HERE, cfg["output"]))
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", src] + extra + [
            "-filter_complex", af, "-map", "0:v", "-map", "[aout]", "-t", f"{total:.3f}",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out])
        print("REMIXED", src, "->", out)
        return

    W, H = cfg["size"]
    fps, xf = cfg["fps"], cfg["crossfade"]
    # "captions" at the top level means images and subtitles are independent (images spread evenly over the audio);
    # otherwise each scene carries its own captions and lasts as long as they are spoken.
    even = "captions" in cfg
    caps = [(None, t) for t in cfg["captions"]] if even else [
        (si, t) for si, s in enumerate(cfg["scenes"]) for t in s["captions"]]

    have_voice = os.path.exists(cfg["voice"]) and not args.preview
    if args.detect:
        segs, total = detect_segments(cfg["voice"], cfg["silence_db"], cfg["silence_min"])
        print(f"{cfg['voice']}: {total:.2f}s, {len(segs)} speech segments (config has {len(caps)} captions)")
        for i, (a, b) in enumerate(segs, 1):
            print(f"  {i:2d}  {a:7.2f} -> {b:7.2f}")
        return

    if have_voice:
        vdur = duration(cfg["voice"])
        timings = cfg["timings"]
        if caps:
            if not timings:
                timings, _ = detect_segments(cfg["voice"], cfg["silence_db"], cfg["silence_min"])
            if len(timings) > len(caps):
                timings = align_segments(timings, [t for _, t in caps])
            if len(timings) != len(caps):
                sys.exit(f"{len(timings)} audio segments but {len(caps)} captions. Run --detect, "
                         f"then either edit the captions to match or set \"timings\" in config.json.")
        total = vdur + cfg["end_pad"]
        if total < 30:
            sys.exit(f"Total {total:.1f}s is under 30s: request a slower take rather than stretching.")
    else:
        est = [max(1.8, len(t) / 15.0) for _, t in caps]
        timings, t = [], 0.6
        for e in est:
            timings.append([t, t + e])
            t += e + 0.6
        total = t + cfg["end_pad"]
        print("No voice file used: building a SILENT PREVIEW with estimated timings.")

    n = len(cfg["scenes"])
    bounds = []
    if even:
        bounds = [total * k / n for k in range(1, n)]
    else:
        idx = 0
        for si, s in enumerate(cfg["scenes"]):
            first, last = idx, idx + len(s["captions"]) - 1
            if si > 0:
                bounds.append((timings[prev_last][1] + timings[first][0]) / 2)
            prev_last = last
            idx += len(s["captions"])
    offsets = [b - xf / 2 for b in bounds]
    edges = [0.0] + offsets + [total - xf]
    durs = [edges[k + 1] + xf - edges[k] for k in range(n)]
    if min(durs) < 2 * xf:
        sys.exit(f"Too many images for a {total:.0f}s video: each would show for only {min(durs):.1f}s.")

    print("Scene boundaries:", [round(b, 2) for b in bounds])
    print("Clip durations:  ", [round(d, 3) for d in durs], "-> total", round(total, 2))

    clips = []
    for k, s in enumerate(cfg["scenes"]):
        frames = int(round(durs[k] * fps)) + 2
        out = os.path.join(BUILD, f"scene{k + 1}.mp4")
        vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase:flags=lanczos,"
              f"crop={W * 2}:{H * 2},"
              f"zoompan=z='1+({cfg['zoom_end']}-1)*on/{frames}':"
              f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={fps},"
              f"format=yuv420p")
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", s["image"], "-vf", vf,
             "-frames:v", str(frames), "-c:v", "libx264", "-crf", "16", out])
        clips.append(out)

    if n == 1:
        base = clips[0]
    else:
        fc, prev = [], "[0:v]"
        for k in range(1, n):
            lab = f"[x{k}]" if k < n - 1 else "[vout]"
            fc.append(f"{prev}[{k}:v]xfade=transition=fade:duration={xf}:offset={offsets[k - 1]:.3f}{lab}")
            prev = lab
        base = os.path.join(BUILD, "base.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        for c in clips:
            cmd += ["-i", c]
        cmd += ["-filter_complex", ";".join(fc), "-map", "[vout]", "-c:v", "libx264",
                "-crf", "16", "-pix_fmt", "yuv420p", "-r", str(fps), base]
        run(cmd)

    cap_inputs, cap_filters = [], []
    prev_end = -1
    for i, ((si, text), (a, b)) in enumerate(zip(caps, timings)):
        png = os.path.join(BUILD, f"cap{i + 1}.png")
        (cw, ch), nl = render_caption(text, cfg, png)
        start = max(0, a - cfg["caption_lead"])
        end = b + cfg["caption_tail"]
        if i + 1 < len(timings):
            end = min(end, timings[i + 1][0] - cfg["caption_lead"] - 0.05)
        end = min(end, total - 0.3)
        d = end - start
        fi, fo = 0.15, 0.2
        cap_inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", png]
        cap_filters.append(
            f"[{i + 1}:v]format=rgba,fade=t=in:st=0:d={fi}:alpha=1,"
            f"fade=t=out:st={d - fo:.3f}:d={fo}:alpha=1,setpts=PTS+{start:.3f}/TB[c{i}]")
        print(f"  caption {i + 1:2d} {start:6.2f}-{end:6.2f}  {nl} line(s) {cw}x{ch}  {text[:48]}")
        prev_end = end

    chain, prev = [], "[0:v]"
    for i in range(len(caps)):
        lab = f"[o{i}]" if i < len(caps) - 1 else "[vfinal]"
        w, h = Image.open(os.path.join(BUILD, f"cap{i + 1}.png")).size
        y = H - h - cfg["caption_bottom_margin"]
        chain.append(f"{prev}[c{i}]overlay=x=(W-w)/2:y={y}:eof_action=pass{lab}")
        prev = lab

    inputs = ["-i", base] + cap_inputs
    filters = cap_filters + chain
    vlabel = "[vfinal]" if caps else "[0:v]"
    n_vid_inputs = 1 + len(caps)
    if cfg.get("endcard", True):
        png = os.path.join(BUILD, "endcard.png")
        _, eh = render_endcard(cfg, png)
        d = min(ENDCARD_SECONDS, total - 0.5)
        k = n_vid_inputs
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", png]
        filters.append(f"[{k}:v]format=rgba,fade=t=in:st=0:d=0.4:alpha=1,setpts=PTS+{total - d:.3f}/TB[ec];"
                       f"{vlabel}[ec]overlay=x=(W-w)/2:y={int(H * 0.12)}:eof_action=pass[vend]")
        vlabel = "[vend]"
        n_vid_inputs += 1
    maps = ["-map", vlabel if vlabel != "[0:v]" else "0:v"]
    if have_voice:
        extra, af = audio_graph(cfg, n_vid_inputs, total)
        inputs += extra
        filters.append(af)
        maps += ["-map", "[aout]"]

    name = cfg["output"] if have_voice else f"{os.path.splitext(cfg['output'])[0]}_silent_preview.mp4"
    out = unique_path(os.path.join(HERE, name))
    cmd = ["ffmpeg", "-y", "-loglevel", "error"] + inputs + [
        "-filter_complex", ";".join(filters)] + maps + [
        "-t", f"{total:.3f}", "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-r", str(fps)]
    if have_voice:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", out]
    run(cmd)
    print("WROTE", out)


if __name__ == "__main__":
    main()
