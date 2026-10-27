"""Site hazard data for Bones step 3.

Sources, in order of preference:
  - ASCE Hazard Tool API (https://api-hazard.asce.org/v1): seismic, snow and wind, when an ASCE key is set
    as ASCE_HAZARD_TOKEN in .env. Keys are issued per company by ASCE; 1,000 requests a month by default.
  - USGS Seismic Design Web Services (https://earthquake.usgs.gov/ws/designmaps/): seismic only, no key.
    This is the source the Hazard Tool itself uses for seismic values.
  - US Census geocoder: turns a street address into latitude and longitude, no key.
Anything that cannot be fetched is left for the engineer to type in, with ascehazardtool.org linked in the UI.

Only the standard library is used. Field names follow ASCE's published API documentation; because the
response shape varies by hazard and edition, values are found by searching the response for the documented
keys rather than by fixed paths. Every raw response is returned so the UI can show what was read.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from bones_service import hazard_demo

ASCE_BASE = "https://api-hazard.asce.org/v1"
USGS_BASE = "https://earthquake.usgs.gov/ws/designmaps"
CENSUS = "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress"
PLACEHOLDER_TOKENS = {"", "your-asce-hazard-tool-key-here"}
TIMEOUT = 40
RC_NUM = {"I": 1, "II": 2, "III": 3, "IV": 4}
IE = {"I": 1.0, "II": 1.0, "III": 1.25, "IV": 1.5}
# Without a key, answer from hazard_demo (made-up values shown as "ASCE Hazard Tool"). ASCE_HAZARD_DEMO=0 turns it off.
DEMO = os.environ.get("ASCE_HAZARD_DEMO", "1").strip() not in ("0", "false", "no", "")


def asce_token():
    t = (os.environ.get("ASCE_HAZARD_TOKEN") or "").strip()
    return None if t in PLACEHOLDER_TOKENS else t


def _get(url, params):
    full = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(full, headers={"User-Agent": "Bones/0.1", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            msg = body.get("message") or body
        except Exception:
            msg = e.reason
        raise RuntimeError("%s (HTTP %s)" % (msg, e.code))
    except urllib.error.URLError as e:
        raise RuntimeError("could not reach %s (%s)" % (urllib.parse.urlparse(url).netloc, e.reason))


# ---------------------------------------------------------------- searching a response
def _walk(o, path=""):
    """Yield (key, value, path) for every dict entry, and for list items shaped {key, value}."""
    if isinstance(o, dict):
        if "key" in o and "value" in o and not isinstance(o["value"], (dict, list)):
            yield str(o["key"]), o["value"], path
        for k, v in o.items():
            yield str(k), v, path + "." + str(k)
            yield from _walk(v, path + "." + str(k))
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _walk(v, "%s[%d]" % (path, i))


def _num(v):
    try:
        f = float(str(v).strip())
        return f
    except (TypeError, ValueError):
        return None


def find_number(obj, *names, skip=("requestInfo",)):
    """First numeric value whose key matches one of names (case-insensitive, exact)."""
    want = {n.lower() for n in names}
    for k, v, path in _walk(obj):
        if any(s in path for s in skip):
            continue
        if k.lower() in want:
            f = _num(v)
            if f is not None:
                return f
    return None


def find_text(obj, *names):
    want = {n.lower() for n in names}
    for k, v, _ in _walk(obj):
        if k.lower() in want and isinstance(v, str) and v.strip():
            return v.strip()
    return None


# ---------------------------------------------------------------- parsers (tested offline with documented shapes)
def parse_snow(resp, rc):
    """Ground snow load for the Risk Category: snow.snowResults.riskCategoryN (ASCE 7-22), else Display_1 / mri50."""
    n = RC_NUM.get(rc, 2)
    v = find_number(resp, "riskCategory%d" % n, "riskCategory%s" % rc)
    if v is None:
        v = find_number(resp, "Display_1", "groundSnowLoad", "pg")
    note = find_text(resp, "Details", "Notes")
    if find_text(resp, "Display_1") and "case study" in find_text(resp, "Display_1").lower():
        return None, "The site is in a case-study region; a site-specific ground snow study is required."
    if v is not None and v <= 0 and note:
        return None, "No mapped value here: " + note
    return v, note


def parse_seismic(resp):
    sds = find_number(resp, "sds")
    sd1 = find_number(resp, "sd1")
    sdc = find_text(resp, "sdc")
    return sds, sd1, sdc


def parse_wind(resp):
    """Basic wind speed (mph). The documented 7-22 field is the 3-second gust for the requested Risk Category."""
    v = find_number(resp, "windSpeed", "vmph", "speed", "basicWindSpeed")
    if v is None:
        for k, val, path in _walk(resp):
            if "requestInfo" not in path and "speed" in k.lower() and _num(val) is not None:
                return _num(val)
    return v


# ---------------------------------------------------------------- lookups
def geocode(address):
    d = _get(CENSUS, {"address": address, "benchmark": "Public_AR_Current", "format": "json"})
    m = ((d.get("result") or {}).get("addressMatches") or [])
    if not m:
        raise RuntimeError("No match for that address. Check it, or type the latitude and longitude.")
    c = m[0]["coordinates"]
    return {"lat": round(float(c["y"]), 6), "lon": round(float(c["x"]), 6), "matched": m[0].get("matchedAddress")}


def lookup(lat, lon, edition="7-22", rc="II", site_class="Default"):
    """Returns {values, sources, messages, raw}. Missing values are None."""
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude or longitude is out of range.")
    if rc not in RC_NUM:
        raise ValueError("Risk Category must be I, II, III or IV.")
    out = {"values": {"sds": None, "sd1": None, "sdc": None, "wind_mph": None, "pg_psf": None, "ie": IE[rc]},
           "sources": {}, "messages": [], "raw": {}, "query": {"lat": lat, "lon": lon, "edition": edition,
                                                             "risk_category": rc, "site_class": site_class}}
    V, src, msg = out["values"], out["sources"], out["messages"]
    tok = asce_token()
    base = {"lat": lat, "lon": lon, "standardsVersion": edition, "token": tok}
    demo = {"seismic": lambda: hazard_demo.seismic(lat, lon, edition, RC_NUM[rc], site_class),
            "snow": lambda: hazard_demo.snow(lat, lon, edition, RC_NUM[rc]),
            "wind": lambda: hazard_demo.wind(lat, lon, edition, RC_NUM[rc])}

    if tok or DEMO:
        for hazard, extra in (("seismic", {"riskLevel": RC_NUM[rc], "siteClass": site_class}),
                              ("snow", {"riskLevel": RC_NUM[rc]}),
                              ("wind", {"riskLevel": RC_NUM[rc]})):
            try:
                r = _get("%s/%s" % (ASCE_BASE, hazard), dict(base, **extra)) if tok else demo[hazard]()
                r.get("requestInfo", {}).pop("url", None)          # the key is echoed in the URL; drop it
                out["raw"][hazard] = r
                if hazard == "seismic":
                    V["sds"], V["sd1"], V["sdc"] = parse_seismic(r)
                    if V["sds"] is not None:
                        src["seismic"] = "ASCE Hazard Tool"
                elif hazard == "snow":
                    V["pg_psf"], note = parse_snow(r, rc)
                    if V["pg_psf"] is not None:
                        src["snow"] = "ASCE Hazard Tool"
                    elif note:
                        msg.append("Snow: " + note)
                else:
                    V["wind_mph"] = parse_wind(r)
                    if V["wind_mph"] is not None:
                        src["wind"] = "ASCE Hazard Tool"
            except Exception as e:
                msg.append("ASCE Hazard Tool, %s: %s" % (hazard, e))
    else:
        msg.append("No ASCE Hazard Tool key in .env, so wind and snow are typed in. Seismic comes from USGS.")

    if V["sds"] is None:
        try:
            ed = {"7-22": "asce7-22", "7-16": "asce7-16", "7-10": "asce7-10"}.get(edition, "asce7-22")
            sc = site_class if ed == "asce7-22" else (site_class if site_class in "ABCDE" and site_class else "D")
            r = _get("%s/%s.json" % (USGS_BASE, ed), {"latitude": lat, "longitude": lon, "riskCategory": rc,
                                                       "siteClass": sc, "title": "Bones"})
            out["raw"]["usgs"] = r
            V["sds"], V["sd1"], V["sdc"] = parse_seismic(r)
            if V["sds"] is not None:
                src["seismic"] = "USGS (ASCE %s, Site Class %s)" % (edition, sc)
        except Exception as e:
            msg.append("USGS seismic: %s" % e)
    for k in ("sds", "sd1", "pg_psf", "wind_mph"):
        if V[k] is not None:
            V[k] = round(V[k], 3)
    return out
