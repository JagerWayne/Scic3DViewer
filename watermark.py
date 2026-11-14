"""Bakes the watermark into every picture of a 360 pack, on the server, so clean pictures never reach a visitor.
Pack layout: b'S3IM' + u32 index length + JSON index + pictures; the index lists [offset, length] for `frames` and `thumbs`."""
import io, json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _overlay(size, text, tile, cross):
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fs = max(13, round(min(w, h) / 24))
    font = ImageFont.load_default(fs)
    if cross:  # a faint X over the whole picture
        for a, b in (((0, 0), (w, h)), ((w, 0), (0, h))):
            d.line([a, b], fill=(255, 255, 255, 50), width=max(1, fs // 12))
    if tile and text:  # diagonal rows of text across the picture
        span = int((w * w + h * h) ** .5) + fs * 4
        layer = Image.new("RGBA", (span, span), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        step_x = int(ld.textlength(text, font=font)) + fs * 3
        for row, y in enumerate(range(0, span, fs * 4)):
            for x in range(-step_x * (row % 2) // 2, span, step_x):
                ld.text((x, y), text, font=font, fill=(255, 255, 255, 58))
        layer = layer.rotate(26, resample=Image.BICUBIC)
        ox, oy = (span - w) // 2, (span - h) // 2
        img.alpha_composite(layer.crop((ox, oy, ox + w, oy + h)))
    elif text:  # one line in the bottom right corner
        tw = d.textlength(text, font=font)
        d.text((w - fs - tw, h - fs * 2), text, font=font, fill=(255, 255, 255, 215), stroke_width=max(1, fs // 14), stroke_fill=(0, 0, 0, 160))
    return img


def bake(path: Path, text: str, tile: bool, cross: bool):
    """Rewrite the pack at `path` with the watermark drawn into all pictures and previews."""
    raw = path.read_bytes()
    n = int.from_bytes(raw[4:8], "little")
    ix = json.loads(raw[8:8 + n])
    data = memoryview(raw)[8 + n:]
    webp = ix["mime"] == "image/webp"
    overlays, out, new = {}, [], {}
    off = 0
    for key, q in (("thumbs", 74), ("frames", 82)):  # previews first, so a viewer can start before the whole file has arrived
        placed = []
        for o, ln in ix.get(key) or []:
            im = Image.open(io.BytesIO(bytes(data[o:o + ln])))
            im.load()
            im = im.convert("RGBA")
            ov = overlays.get(im.size) or overlays.setdefault(im.size, _overlay(im.size, text, tile, cross))
            im.alpha_composite(ov)
            buf = io.BytesIO()
            if webp:
                im.save(buf, "WEBP", quality=q, method=2)
            else:
                im.convert("RGB").save(buf, "JPEG", quality=q)
            b = buf.getvalue()
            placed.append([off, len(b)])
            out.append(b)
            off += len(b)
        new[key] = placed
    ix["frames"] = new["frames"]
    if ix.get("thumbs"):
        ix["thumbs"] = new["thumbs"]
    head = json.dumps(ix).encode()
    path.write_bytes(b"S3IM" + len(head).to_bytes(4, "little") + head + b"".join(out))
