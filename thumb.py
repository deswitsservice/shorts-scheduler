"""Compose a 1080x1920 thumbnail: picture (cover-cropped) + centered title text."""
import os, re
from PIL import Image, ImageDraw, ImageFont, ImageOps

W, H = 1080, 1920
FONTS = ["/System/Library/Fonts/Supplemental/Georgia Bold Italic.ttf", "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"]
EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def _font(size):
    for f in FONTS:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if draw.textlength(t, font=font) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + [cur]


def compose(src, title, out, max_lines=5):
    img = ImageOps.fit(Image.open(src).convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.5))
    text = " ".join(EMOJI.sub("", title).split())
    if text:
        img = Image.alpha_composite(img.convert("RGBA"), Image.new("RGBA", (W, H), (0, 0, 0, 90))).convert("RGB")
        d = ImageDraw.Draw(img)
        max_w = int(W * 0.8)
        size = 150
        while size > 48:
            font = _font(size)
            lines = _wrap(d, text, font, max_w)
            if len(lines) <= max_lines and all(d.textlength(l, font=font) <= max_w for l in lines):
                break
            size -= 6
        gap = int(size * 0.22)
        heights = [d.textbbox((0, 0), l, font=font)[3] for l in lines]
        total = sum(heights) + gap * (len(lines) - 1)
        y = (H - total) / 2
        for l, h in zip(lines, heights):
            x = (W - d.textlength(l, font=font)) / 2
            d.text((x, y), l, font=font, fill=(255, 255, 255), stroke_width=max(3, size // 24), stroke_fill=(0, 0, 0))
            y += h + gap
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img.save(out, quality=95)
    return out


SCRIPTS = {
    "brush": ("/System/Library/Fonts/Supplemental/Brush Script.ttf", 0),
    "signpainter": ("/System/Library/Fonts/SignPainter.ttc", 0),
    "hand": ("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf", 0),
    "chancery": ("/System/Library/Fonts/Supplemental/Apple Chancery.ttf", 0),
}


def _balanced_lines(words, n):
    """Split words into n lines with the most even character length, keeping word order."""
    best, best_cost = None, 1e9
    import itertools
    for cuts in itertools.combinations(range(1, len(words)), n - 1):
        parts, prev = [], 0
        for c in list(cuts) + [len(words)]:
            parts.append(" ".join(words[prev:c])); prev = c
        lens = [len(p) for p in parts]
        cost = max(lens) - min(lens)
        if cost < best_cost:
            best, best_cost = parts, cost
    return best


def _heart(d, cx, cy, s, fill):
    d.polygon([(cx, cy + s), (cx - s * 1.1, cy - s * 0.1), (cx - s * 0.6, cy - s * 0.75), (cx, cy - s * 0.25),
               (cx + s * 0.6, cy - s * 0.75), (cx + s * 1.1, cy - s * 0.1)], fill=fill)


def compose_script(src, title, out, style="brush", region=(0.05, 0.43), mode=None):
    """Handwritten title in the top area (default 5-43% of height); ink colour follows the sky brightness."""
    img = ImageOps.fit(Image.open(src).convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.5))
    text = " ".join(w[:1].upper() + w[1:] for w in EMOJI.sub("", title).split())
    words = text.split()
    top, bottom = int(H * region[0]), int(H * region[1])
    avail_h, max_w = bottom - top - 90, int(W * 0.88)
    path, idx = SCRIPTS[style]
    d = ImageDraw.Draw(img)
    n_opts = [2, 3] if len(words) >= 3 else [1]
    best = None
    for n in n_opts:
        lines = _balanced_lines(words, n) if n > 1 else [text]
        size = 330
        while size > 90:
            f = ImageFont.truetype(path, size, index=idx)
            widths = [d.textlength(l, font=f) for l in lines]
            asc, desc = f.getmetrics()
            total = len(lines) * (asc + desc) * 0.86
            if max(widths) <= max_w and total <= avail_h:
                break
            size -= 6
        if best is None or size > best[0]:
            best = (size, lines, f)
    size, lines, f = best
    region_img = img.crop((0, top, W, bottom)).convert("L")
    lum = sum(region_img.resize((1, 1)).getdata()) / 1
    bright = (lum > 70) if mode is None else (mode == 'dark')
    ink = (43, 23, 13) if bright else (255, 243, 220)
    accent = (112, 30, 14) if bright else (255, 201, 138)
    asc, desc = f.getmetrics()
    lh = (asc + desc) * 0.86
    block = lh * len(lines) + 70
    y = top + (bottom - top - block) / 2
    from PIL import ImageFilter
    glow = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(glow)
    gy = y
    for l in lines:
        gd.text(((W - d.textlength(l, font=f)) / 2, gy), l, font=f, fill=255, stroke_width=max(6, size // 18))
        gy += lh
    glow = glow.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.55))
    halo = (255, 236, 200) if bright else (0, 0, 0)
    img.paste(Image.new("RGB", (W, H), halo), (0, 0), glow)
    d = ImageDraw.Draw(img)
    for i, l in enumerate(lines):
        col = accent if (i == len(lines) - 1 and len(lines) > 1) else ink
        x = (W - d.textlength(l, font=f)) / 2
        d.text((x, y), l, font=f, fill=col, stroke_width=max(2, size // 40), stroke_fill=col)
        y += lh
    ly = y + 25
    d.line([(W * 0.30, ly), (W * 0.45, ly)], fill=ink, width=5)
    d.line([(W * 0.55, ly), (W * 0.70, ly)], fill=ink, width=5)
    _heart(d, W / 2, ly - 2, 18, accent)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img.save(out, quality=95)
    return out


CAVEAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "Caveat.ttf")
MARKER_FONTS = {
    "caveat": (CAVEAT, 600),          # Caveat SemiBold (variable font weight)
    "caveat_medium": (CAVEAT, 500),
    "marker": ("/System/Library/Fonts/MarkerFelt.ttc", 1),      # Marker Felt Wide
    "marker_thin": ("/System/Library/Fonts/MarkerFelt.ttc", 0),
    "hand": ("/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf", 0),
}


def compose_marker(src, title, out, style="marker", emphasis=None, region=(0.13, 0.49)):
    """Hand-lettered look: image kept exactly as is, only the title is drawn (off-white marker feel, per-letter jitter,
    one emphasised word, faint soft shadow). Title text is used exactly as given."""
    import random
    from PIL import ImageFilter
    img = Image.open(src).convert("RGB")
    w, h = img.size
    words = title.split()
    path, idx = MARKER_FONTS[style]
    variable = path.endswith("Caveat.ttf")

    def mk(size):
        f_ = ImageFont.truetype(path, size, index=0 if variable else idx)
        if variable:
            f_.set_variation_by_axes([idx])
        return f_

    top, bottom = int(h * region[0]), int(h * region[1])
    max_w, avail_h = int(w * 0.90), bottom - top
    rnd = random.Random(sum(map(ord, title)))
    emph = emphasis if emphasis is not None else words[-1]
    d = ImageDraw.Draw(img)

    def layout(n, base):
        lines = _balanced_lines(words, n) if n > 1 else [title]
        f = mk(base)
        fe = mk(int(base * 1.28))
        def wlen(line):
            return sum(d.textlength(t + " ", font=(fe if t == emph else f)) for t in line.split()) - d.textlength(" ", font=f)
        return lines, f, fe, max(wlen(l) for l in lines)

    best = None
    for n in ([2, 3] if len(words) >= 3 else [1]):
        base = int(w * 0.22)
        while base > 40:
            lines, f, fe, mw = layout(n, base)
            total = len(lines) * base * 1.32
            if mw <= max_w and total <= avail_h:
                break
            base -= 4
        if best is None or base > best[0]:
            best = (base, lines, f, fe)
    base, lines, f, fe = best
    lh = base * 1.32
    y0 = top + (avail_h - lh * len(lines)) / 2
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ink = (251, 243, 226, 255) if variable else (252, 248, 240, 255)
    for li, line in enumerate(lines):
        toks = line.split()
        def tlen(t):
            return sum(d.textlength(c, font=(fe if t == emph else f)) for c in t)
        lw = sum(tlen(t) for t in toks) + d.textlength(" ", font=f) * (len(toks) - 1)
        x = (w - lw) / 2
        y = y0 + li * lh
        for t in toks:
            fnt = fe if t == emph else f
            for c in t:
                cw = d.textlength(c, font=fnt)
                size = int(fnt.size * 1.5)
                tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
                ImageDraw.Draw(tile).text((size * 0.25, size * 0.1), c, font=fnt, fill=ink)
                tile = tile.rotate(rnd.uniform(-3.2, 3.2), resample=Image.BICUBIC)
                layer.alpha_composite(tile, (int(x - size * 0.25 + rnd.uniform(-1.5, 1.5)),
                                             int(y - size * 0.1 + rnd.uniform(-5, 5) - (fnt.size - base) * 0.25)))
                x += cw * rnd.uniform(0.96, 1.03)
            x += d.textlength(" ", font=f)
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow.paste((60, 25, 10, 255), (0, 0), layer.split()[3].point(lambda v: int(v * 0.38)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(5))
    base_img = img.convert("RGBA")
    base_img.alpha_composite(shadow, (2, 4))
    base_img.alpha_composite(layer)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    base_img.convert("RGB").save(out, quality=95)
    return out


if __name__ == "__main__":
    import sys
    print(compose(sys.argv[1], sys.argv[2], sys.argv[3]))
