"""Bones on FastAPI: the Bones web UI, its sketch and hazard routes, and the gravity optimizer.

Run:   python -m bones_service.api                 (then open http://127.0.0.1:8765)
       python -m bones_service.api --port 9000 --out C:\\path\\to\\outputs
   or  uvicorn bones_service.api:app --port 8765 --reload

Serves the same page and /api/* routes as bones_service/server.py (which still works on its
own) by calling server.py's functions, and adds:

  GET  /api/occupancies   occupancy keys for the load-area dropdown (config/occupancies.json)
  POST /api/gravity       {state: BonesPad.state(), options?: {constraints?, objective_weights?, systems?}}
                          -> {input, notes, output}: the gravity input built from the pad,
                          notes on the translation, and gravity.run's output (spec 3.1).

Errors come back as {"error": "plain message"}, the shape the UI's post() reads.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from bones_service import server as srv   # loads .env and puts the repo root on sys.path
from bones_service import gravity_bridge

app = FastAPI(title="Bones", description="Sketch to plan, site hazards and gravity schemes.")

if os.environ.get("BONES_OUT"):
    srv.OUT = os.path.abspath(os.environ["BONES_OUT"])
os.makedirs(srv.OUT, exist_ok=True)


# ---------------------------------------------------------------- errors: {"error": "..."}
def _error(message: str, code: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=code, headers={"Cache-Control": "no-store"})


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    return _error(str(exc.detail), exc.status_code)


@app.exception_handler(RequestValidationError)
async def _request_error(request: Request, exc: RequestValidationError):
    return _error("The request is not valid JSON of the expected shape.", 422)


@app.exception_handler(ValidationError)
async def _contract_error(request: Request, exc: ValidationError):
    return _error("The gravity input does not match the contract: " + "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()), 422)


async def _plain_error(request: Request, exc: Exception):
    # Like server.py: a plain message for the UI, not a traceback.
    return _error(str(exc) or exc.__class__.__name__, 400)


# Expected failures (bad input, missing files, a failed lookup) are handled as plain 400s;
# anything else also gets the plain message, via Starlette's server-error handler.
for _exc in (ValueError, KeyError, TypeError, OSError, Exception):
    app.add_exception_handler(_exc, _plain_error)


def _json(obj: Any) -> JSONResponse:
    return JSONResponse(obj, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- page and files
@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    return FileResponse(os.path.join(srv.STATIC, "index.html"), media_type="text/html; charset=utf-8")


@app.get("/api/file")
def file(job: str = "", name: str = ""):
    job, folder = srv.job_dir(job)
    name = os.path.basename(name)
    path = os.path.join(folder, name)
    if not name.startswith(job) or not os.path.isfile(path):
        return _error("File not found.", 404)
    ctype = {".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml",
             ".csv": "text/csv", ".txt": "text/plain; charset=utf-8"}.get(os.path.splitext(name)[1],
                                                                         "application/octet-stream")
    return FileResponse(path, media_type=ctype, filename=name)


@app.get("/api/health")
def health():
    return _json({"ok": True, "api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
                  "asce_key": bool(srv.HZ.asce_token()),
                  "example": os.path.isfile(srv.EXAMPLE_READING), "models": srv.MODELS,
                  "gravity": True})


# ---------------------------------------------------------------- sketch reading (server.py logic)
def _stories(req: dict):
    levels, height = srv.to_int(req.get("levels")), srv.to_float(req.get("storey_height_ft"))
    snow = req.get("snow") if isinstance(req.get("snow"), dict) else None
    return levels, height, snow


@app.post("/api/read")
def read(req: dict = Body(default={})):
    if not req.get("image"):
        raise ValueError("No image was sent.")
    levels, height, snow = _stories(req)
    model = req.get("model") if req.get("model") in srv.MODELS else srv.DEFAULT_MODEL
    job, img = srv.stage_image(req["image"], req.get("filename"))
    raw = srv.R.read_with_claude(img, model=model)   # raises a plain message without a key
    return _json(srv.process(img, raw, job, levels, height, snow))


@app.post("/api/rebuild")
def rebuild(req: dict = Body(default={})):
    levels, height, snow = _stories(req)
    job, folder = srv.job_dir(req.get("job"))
    reading = os.path.join(folder, "%s-reading.json" % job)
    if not os.path.isfile(reading):
        raise ValueError("No saved reading for this sketch yet.")
    with open(reading) as f:
        raw = json.load(f)
    return _json(srv.process(srv.find_image(folder, job), raw, job, levels, height, snow))


@app.post("/api/example")
def example(req: dict = Body(default={})):
    levels, height, snow = _stories(req)
    job, folder = srv.job_dir("notched-sketch")
    os.makedirs(folder, exist_ok=True)
    img = os.path.join(folder, "notched-sketch.png")
    shutil.copyfile(srv.EXAMPLE_IMG, img)
    with open(srv.EXAMPLE_READING) as f:
        raw = json.load(f)
    return _json(srv.process(img, raw, job, levels, height, snow))


# ---------------------------------------------------------------- site hazards (server.py logic)
@app.post("/api/geocode")
def geocode(req: dict = Body(default={})):
    if not (req.get("address") or "").strip():
        raise ValueError("Type the site address first.")
    return _json(srv.HZ.geocode(req["address"].strip()))


@app.post("/api/hazard")
def hazard(req: dict = Body(default={})):
    return _json(srv.HZ.lookup(req.get("lat"), req.get("lon"), req.get("edition") or "7-22",
                               req.get("risk_category") or "II", req.get("site_class") or "Default"))


# ---------------------------------------------------------------- gravity
@app.get("/api/occupancies")
def occupancies():
    return _json({"occupancies": gravity_bridge.occupancies()})


@app.post("/api/gravity")
def run_gravity(req: dict = Body(default={})):
    state = req.get("state")
    if not isinstance(state, dict):
        raise ValueError("Open a plan in the drawing pad first.")
    return _json(gravity_bridge.run(state, req.get("options") or {}))


def main(argv=None):
    import uvicorn

    ap = argparse.ArgumentParser(description="Bones on FastAPI")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to share on your network")
    ap.add_argument("--out", default=srv.OUT, help="output folder (default: %(default)s)")
    a = ap.parse_args(argv)
    srv.OUT = os.path.abspath(a.out)
    os.makedirs(srv.OUT, exist_ok=True)
    print("Bones (FastAPI) on http://%s:%d  (outputs: %s)" % (a.host, a.port, srv.OUT))
    uvicorn.run(app, host=a.host, port=a.port)


if __name__ == "__main__":
    main()
