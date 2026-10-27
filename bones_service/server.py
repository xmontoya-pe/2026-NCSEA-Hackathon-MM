"""Bones sketch service: the Bones web UI in front of the Sketch Reader.

Run:   python -m bones_service.server            (then open http://127.0.0.1:8765)
       python -m bones_service.server --port 9000 --out C:\\path\\to\\outputs

Only the Python standard library is added on top of Sketch Reader's own requirements.
The Anthropic API key is read on the server, from the .env file next to run_bones.bat or from the
ANTHROPIC_API_KEY environment variable, and never reaches the browser.

Endpoints (JSON in, JSON out):
  POST /api/read      {image: base64 or data URL, filename, levels?, storey_height_ft?, model?, snow?}
                      snow = {pg, Ce, Ct}: ground snow load (psf), exposure and thermal factors.
                      Reads the sketch with Claude (15-60 s), writes Sketch Reader's files plus
                      <name>-module2.json, and returns the review data.
  POST /api/rebuild   {job, levels?, storey_height_ft?}
                      Re-runs from the saved reading (no Claude call), e.g. after changing stories.
  POST /api/example   {levels?, storey_height_ft?}
                      Runs the bundled example with its saved reading (no API key needed).
  POST /api/geocode   {address}  ->  {lat, lon, matched}   (US Census geocoder, no key)
  POST /api/hazard    {lat, lon, edition, risk_category, site_class}
                      Seismic, wind and ground snow from the ASCE Hazard Tool API (ASCE_HAZARD_TOKEN in .env);
                      seismic falls back to USGS without a key. See hazard.py.
  GET  /api/file?job=<job>&name=<file>   Downloads one output file.
"""
import argparse
import base64
import json
import os
import re
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

PLACEHOLDER_KEY = "sk-ant-your-key-here"


def load_env(path):
    """Read KEY=value lines from a .env file into the environment (an existing variable wins).
    The placeholder key is ignored, so an unedited .env counts as no key."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and v != PLACEHOLDER_KEY and not os.environ.get(k):
                os.environ[k] = v


load_env(os.path.join(ROOT, ".env"))

from sketch_reader.reader import DEFAULT_MODEL                     # noqa: E402
from sketch_reader.outputs import write_all, plan_json             # noqa: E402
from sketch_reader import reader as R                              # noqa: E402
from bones_service.convert import to_module2, FT_PER               # noqa: E402
from bones_service import hazard as HZ                             # noqa: E402

STATIC = os.path.join(HERE, "static")
EXAMPLE_IMG = os.path.join(ROOT, "examples", "notched-sketch.png")
EXAMPLE_READING = os.path.join(ROOT, "examples", "notched-sketch-reading.json")
MODELS = [DEFAULT_MODEL, "claude-opus-5-5"]
MAX_UPLOAD = 20 * 1024 * 1024
OUT = os.path.join(ROOT, "outputs")
LOCK = threading.Lock()   # one write_all at a time per job folder


def safe_stem(name):
    stem = os.path.splitext(os.path.basename(name or "sketch"))[0]
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.") or "sketch"
    return stem[:80]


def job_dir(job):
    job = safe_stem(job)
    path = os.path.realpath(os.path.join(OUT, job))
    if not path.startswith(os.path.realpath(OUT) + os.sep):
        raise ValueError("Bad job name.")
    return job, path


def sketch_units_height(plan_units, storey_height_ft):
    """The reader takes storey height in the sketch's units; the UI works in feet."""
    if not storey_height_ft:
        return None
    return float(storey_height_ft) / FT_PER.get(plan_units, 1.0)


def process(image_path, raw, job, levels=None, storey_height_ft=None, snow=None):
    """Run Sketch Reader's writers, add the Module 2 file, and build the review payload."""
    units = R.normalize_reading(raw, 1, 1)["units"]
    job = os.path.splitext(os.path.basename(image_path))[0]   # write_all names everything after the photo
    with LOCK:
        folder, paths, B = write_all(image_path, raw, OUT, levels, sketch_units_height(units, storey_height_ft))
        plan = plan_json(B)
        conv = to_module2(plan, name=job, snow=snow)
        m2 = os.path.join(folder, "%s-module2.json" % job)
        with open(m2, "w", encoding="utf-8") as f:
            json.dump(conv["project"], f, indent=2)
        paths.append(m2)
        with open(os.path.join(folder, "%s-overlay.png" % job), "rb") as f:
            overlay = base64.b64encode(f.read()).decode()
    return {
        "job": job,
        "plan": plan,
        "module2": conv["project"],
        "checks": conv["checks"],
        "changes": conv["changes"],
        "ready": conv["ready"],
        "overlay_png": overlay,
        "files": [os.path.basename(p) for p in paths],
        "models": MODELS,
    }


def stage_image(data, filename):
    """Save an uploaded image (base64 or data URL) as <out>/<stem>/<stem>.<ext>; returns (job, path)."""
    if "," in data[:100] and data.startswith("data:"):
        data = data.split(",", 1)[1]
    blob = base64.b64decode(data, validate=False)
    if len(blob) > MAX_UPLOAD:
        raise ValueError("The image is larger than 20 MB.")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    job, folder = job_dir(filename)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, job + ext)
    with open(path, "wb") as f:
        f.write(blob)
    return job, path


def find_image(folder, job):
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        p = os.path.join(folder, job + ext)
        if os.path.isfile(p):
            return p
    raise ValueError("The photo for this job is missing; upload it again.")


def to_int(v):
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("Stories must be a whole number.")


def to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        raise ValueError("Story height must be a number.")


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "BonesSketch/0.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[bones] " + (fmt % args) + "\n")

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype, download=None):
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="%s"' % download)
        self.end_headers()
        self.wfile.write(body)

    def body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_UPLOAD * 1.4:
            raise ValueError("The upload is too large.")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self.send_file(os.path.join(STATIC, "index.html"), "text/html; charset=utf-8")
            if u.path == "/api/file":
                q = parse_qs(u.query)
                job, folder = job_dir((q.get("job") or [""])[0])
                name = os.path.basename((q.get("name") or [""])[0])
                path = os.path.join(folder, name)
                if not name.startswith(job) or not os.path.isfile(path):
                    return self.send_json({"error": "File not found."}, 404)
                ctype = {".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
                         ".csv": "text/csv", ".txt": "text/plain; charset=utf-8"}.get(os.path.splitext(name)[1], "application/octet-stream")
                return self.send_file(path, ctype, download=name)
            if u.path == "/api/health":
                return self.send_json({"ok": True, "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                                       "asce_key": bool(HZ.asce_token()),
                                       "example": os.path.isfile(EXAMPLE_READING), "models": MODELS})
            self.send_json({"error": "Not found."}, 404)
        except Exception as e:
            self.send_json({"error": str(e)}, 400)

    def do_POST(self):
        u = urlparse(self.path)
        try:
            req = self.body_json()
            levels, height = to_int(req.get("levels")), to_float(req.get("storey_height_ft"))
            snow = req.get("snow") if isinstance(req.get("snow"), dict) else None
            if u.path == "/api/read":
                if not req.get("image"):
                    raise ValueError("No image was sent.")
                model = req.get("model") if req.get("model") in MODELS else DEFAULT_MODEL
                job, img = stage_image(req["image"], req.get("filename"))
                raw = R.read_with_claude(img, model=model)   # raises a plain message without a key
                return self.send_json(process(img, raw, job, levels, height, snow))
            if u.path == "/api/rebuild":
                job, folder = job_dir(req.get("job"))
                reading = os.path.join(folder, "%s-reading.json" % job)
                if not os.path.isfile(reading):
                    raise ValueError("No saved reading for this sketch yet.")
                with open(reading) as f:
                    raw = json.load(f)
                return self.send_json(process(find_image(folder, job), raw, job, levels, height, snow))
            if u.path == "/api/geocode":
                if not (req.get("address") or "").strip():
                    raise ValueError("Type the site address first.")
                return self.send_json(HZ.geocode(req["address"].strip()))
            if u.path == "/api/hazard":
                return self.send_json(HZ.lookup(req.get("lat"), req.get("lon"), req.get("edition") or "7-22",
                                                req.get("risk_category") or "II", req.get("site_class") or "Default"))
            if u.path == "/api/example":
                job, folder = job_dir("notched-sketch")
                os.makedirs(folder, exist_ok=True)
                img = os.path.join(folder, "notched-sketch.png")
                shutil.copyfile(EXAMPLE_IMG, img)
                with open(EXAMPLE_READING) as f:
                    raw = json.load(f)
                return self.send_json(process(img, raw, job, levels, height, snow))
            self.send_json({"error": "Not found."}, 404)
        except Exception as e:   # plain message for the UI, not a traceback
            self.send_json({"error": str(e)}, 400)


def main(argv=None):
    global OUT
    ap = argparse.ArgumentParser(description="Bones sketch service")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to share on your network")
    ap.add_argument("--out", default=OUT, help="output folder (default: %(default)s)")
    a = ap.parse_args(argv)
    OUT = os.path.abspath(a.out)
    os.makedirs(OUT, exist_ok=True)
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("Bones sketch service on http://%s:%d  (outputs: %s)" % (a.host, a.port, OUT))
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No API key yet: paste it into the .env file next to run_bones.bat, then restart.")
        print("'Try the example sketch' works without one.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
