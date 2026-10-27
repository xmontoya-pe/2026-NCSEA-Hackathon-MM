"""Read a floor-plan sketch photo with Claude and return what is drawn and written on it."""
import base64
import io
import json
import os

from PIL import Image, ImageOps

DEFAULT_MODEL = "claude-sonnet-5-5"   # "claude-opus-5-5" reads messy sketches more carefully, at higher cost
MAX_SIDE = 1568                       # images are sent at most this many pixels on the long side

PROMPT = """You are reading a hand-drawn FLOOR PLAN sketch made by a structural engineer (the attached image). Extract the geometry and the written dimensions as JSON.

Coordinates: give every point as [x, y] normalized to the full image: x = 0 at the left edge to 1000 at the right edge, y = 0 at the top edge to 1000 at the bottom edge. Be precise about where the lines are actually drawn, not where they "should" be.

Reply with ONLY one JSON object with these keys:
{
  "units": "m" | "mm" | "ft",
  "outline": [[x,y], ...],
  "dimensions": [ {"text": "9.0 m", "value": 9.0, "p1": [x,y], "p2": [x,y], "orientation": "h" | "v" | "a"} ],
  "grid_x": [x, ...],
  "grid_y": [y, ...],
  "columns": [[x,y], ...],
  "cores": [ [[x,y], ...] ],
  "levels": {"count": number or null, "storey_height": number or null},
  "notes": ["..."],
  "warnings": ["..."]
}

Meaning of each key:
- units: the unit the written dimensions use. If none is written, infer it from the magnitudes (e.g. 7500 -> mm, 7.5 -> m, 24'-6" -> ft). A ' mark means feet. Convert feet-and-inches to decimal feet (24'-6" -> 24.5).
- outline: the corners of the floor plate / slab edge, in order around the perimeter, without repeating the first point. Follow the heaviest outer line. Where pen lines do not quite meet, give the corner where they would intersect.
- dimensions: every written dimension. p1 and p2 are the two points ON THE PLAN that the dimension measures between (where its extension lines, ticks or arrows line up with the building edge or grid line), not the position of the text and not the end of the dimension line if it is drawn away from the building. orientation: "h" measures a horizontal distance, "v" a vertical one, "a" anything else. value is the number in the stated units.
- grid_x / grid_y: positions of vertical / horizontal structural grid lines, if drawn (dashed lines, often with bubbles).
- columns: centres of column marks (filled dots or squares, crosses).
- cores: hatched or labelled core / shear-wall areas, each as a polygon.
- levels: only if written, e.g. "6 storeys", "F-F 3.6 m".
- notes: any other text written on the sketch, verbatim.
- warnings: anything ambiguous, such as illegible numbers or dimensions you could not place.

Rules: report only what is drawn or written; use empty arrays for things that are absent; never invent dimensions. If a number is hard to read, give your best reading and add a warning."""


def load_image(path):
    """Open the photo upright (phone EXIF rotation applied). Returns the PIL image."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _encode(img):
    w, h = img.size
    k = min(1.0, MAX_SIDE / max(w, h))
    if k < 1:
        img = img.resize((round(w * k), round(h * k)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    if buf.tell() > 4_500_000:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"
    return base64.b64encode(buf.getvalue()).decode(), "image/png"


def _parse_json(text):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    if "```" in text:
        for part in text.split("```")[1::2]:
            part = part.split("\n", 1)[1] if part.lstrip().startswith("json") else part
            try:
                return json.loads(part)
            except ValueError:
                continue
    a, b = text.find("{"), text.rfind("}")
    if a >= 0 and b > a:
        return json.loads(text[a:b + 1])
    raise ValueError("The reply did not contain JSON:\n" + text[:500])


def read_with_claude(image_path, model=DEFAULT_MODEL, api_key=None):
    """Send the photo to Claude. Returns the raw reading (normalized 0-1000 coordinates)."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt")
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("No API key. Set the ANTHROPIC_API_KEY environment variable or enter the key in the app.")
    img = load_image(image_path)
    data, media = _encode(img)
    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=model,
        max_tokens=8000,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
            {"type": "text", "text": PROMPT},
        ]}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    raw = _parse_json(text)
    raw["_meta"] = {"model": model, "image": os.path.basename(image_path), "image_size": list(img.size)}
    return raw


def normalize_reading(raw, W, H):
    """Convert a raw reading (0-1000 coordinates) to image pixels and one unit system."""
    def pt(p):
        try:
            return [float(p[0]) * W / 1000, float(p[1]) * H / 1000]
        except (TypeError, ValueError, IndexError):
            return None

    def pts(a):
        return [q for q in (pt(p) for p in (a or [])) if q]

    units = str(raw.get("units") or "m").lower().strip()
    k = 1.0
    if units.startswith("in"):
        units, k = "ft", 1 / 12
    elif units.startswith("f") or units in ("'", "feet"):
        units = "ft"
    elif units == "cm":
        units, k = "mm", 10.0
    elif units != "mm":
        units = "m"
    dims = []
    for i, d in enumerate(raw.get("dimensions") or []):
        p1, p2 = pt(d.get("p1")), pt(d.get("p2"))
        try:
            v = float(d.get("value")) * k
        except (TypeError, ValueError):
            v = None
        if p1 and p2:
            o = str(d.get("orientation") or "").lower()
            dims.append({"id": "D%d" % (len(dims) + 1), "text": str(d.get("text") or ""), "value": v,
                         "p1": p1, "p2": p2, "orient": o if o in ("h", "v") else "a"})
    lv = raw.get("levels") or {}
    count = lv.get("count")
    height = lv.get("storey_height")
    return {
        "W": W, "H": H, "units": units,
        "outline": pts(raw.get("outline")),
        "dims": dims,
        "grids_x": [float(v) * W / 1000 for v in (raw.get("grid_x") or [])],
        "grids_y": [float(v) * H / 1000 for v in (raw.get("grid_y") or [])],
        "columns": pts(raw.get("columns")),
        "cores": [c for c in (pts(c) for c in (raw.get("cores") or [])) if len(c) >= 3],
        "levels": {"count": int(count) if count else None, "storey_height": float(height) * k if height else None},
        "notes": [str(n) for n in (raw.get("notes") or [])],
        "warnings": [str(w) for w in (raw.get("warnings") or [])],
    }
