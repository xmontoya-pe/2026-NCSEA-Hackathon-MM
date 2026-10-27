"""Demo stand-in for the ASCE Hazard Tool API, used when no ASCE_HAZARD_TOKEN is set.

NOT REAL HAZARD DATA. Values are interpolated from a small table of rough, illustrative city values so the
demo looks plausible across the US (high seismic on the West Coast, hurricane wind on the Gulf and Atlantic,
heavy snow in the north). Never use them for design.

Each function returns a dict shaped like the matching ASCE API response, so hazard.py reads it with the same
parsers it uses for the real API.
"""
import math
import time

# (name, lat, lon, SDS g, SD1 g, V mph, pg psf): ASCE 7-22-ish, Risk Category II, default site class. Illustrative.
SITES = [
    ("Los Angeles",    34.05, -118.25, 1.30, 0.75,  95,  0),
    ("San Francisco",  37.77, -122.42, 1.20, 0.70,  98,  0),
    ("Seattle",        47.61, -122.33, 1.10, 0.62,  98, 15),
    ("Portland",       45.52, -122.68, 0.85, 0.50,  98, 12),
    ("Salt Lake City", 40.76, -111.89, 1.05, 0.48, 103, 30),
    ("Denver",         39.74, -104.99, 0.20, 0.08, 109, 30),
    ("Phoenix",        33.45, -112.07, 0.17, 0.07, 103,  0),
    ("Dallas",         32.78,  -96.80, 0.10, 0.06, 108,  5),
    ("Houston",        29.76,  -95.37, 0.08, 0.05, 135,  0),
    ("New Orleans",    29.95,  -90.07, 0.07, 0.04, 145,  0),
    ("Miami",          25.76,  -80.19, 0.05, 0.03, 170,  0),
    ("Atlanta",        33.75,  -84.39, 0.20, 0.10, 105, 10),
    ("Charleston",     32.78,  -79.93, 0.85, 0.40, 145,  5),
    ("Memphis",        35.15,  -90.05, 0.75, 0.40, 107, 10),
    ("Chicago",        41.88,  -87.63, 0.12, 0.08, 107, 25),
    ("Minneapolis",    44.98,  -93.27, 0.06, 0.04, 109, 50),
    ("Buffalo",        42.89,  -78.88, 0.20, 0.07, 108, 55),
    ("New York",       40.71,  -74.01, 0.28, 0.08, 117, 25),
    ("Boston",         42.36,  -71.06, 0.20, 0.07, 128, 40),
    ("Anchorage",      61.22, -149.90, 1.10, 0.70, 110, 50),
    ("Honolulu",       21.31, -157.86, 0.40, 0.20, 130,  0),
]
# Rough multipliers on the table, for the inputs the table does not cover.
SITE_CLASS_SDS = {"A": 0.65, "B": 0.75, "BC": 0.85, "C": 0.90, "CD": 0.95, "D": 1.00, "DE": 1.00, "E": 0.95}
SITE_CLASS_SD1 = {"A": 0.45, "B": 0.55, "BC": 0.70, "C": 0.80, "CD": 0.90, "D": 1.00, "DE": 1.15, "E": 1.35}
RC_WIND = {1: 0.94, 2: 1.00, 3: 1.06, 4: 1.09}
RC_SNOW = {1: 0.80, 2: 1.00, 3: 1.20, 4: 1.30}
LATENCY = 0.4   # seconds per call, so the pull feels like a network request


def _miles(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 3959 * math.acos(max(-1.0, min(1.0, c)))


def _blend(lat, lon):
    """Inverse-distance blend (power 3, so the nearest city dominates) of SDS, SD1, V and pg."""
    w, acc = 0.0, [0.0, 0.0, 0.0, 0.0]
    for _, la, lo, *vals in SITES:
        d = _miles(lat, lon, la, lo)
        if d < 1:
            return list(vals)
        k = 1 / d ** 3
        w += k
        acc = [a + k * v for a, v in zip(acc, vals)]
    return [a / w for a in acc]


def _sdc(sds, sd1, rc):
    """ASCE 7 Tables 11.6-1 and 11.6-2 (the S1 >= 0.75 g check for E and F is not modeled)."""
    iv = rc == 4
    def cat(v, limits):
        for lim, low, high in zip(limits, "ABC", "ACD"):
            if v < lim:
                return high if iv and low != "A" else low
        return "D"
    return max(cat(sds, (0.167, 0.33, 0.50)), cat(sd1, (0.067, 0.133, 0.20)))


def _request_info(hazard, lat, lon, edition, rc, **extra):
    info = {"hazard": hazard, "lat": lat, "lon": lon, "standardsVersion": edition, "riskLevel": rc}
    info.update(extra)
    return info


def seismic(lat, lon, edition, rc, site_class):
    time.sleep(LATENCY)
    sds, sd1, _, _ = _blend(lat, lon)
    sds *= SITE_CLASS_SDS.get(site_class, 1.0)
    sd1 *= SITE_CLASS_SD1.get(site_class, 1.0)
    return {"requestInfo": _request_info("seismic", lat, lon, edition, rc, siteClass=site_class),
            "data": {"sms": round(1.5 * sds, 3), "sm1": round(1.5 * sd1, 3),
                     "sds": round(sds, 3), "sd1": round(sd1, 3), "sdc": _sdc(sds, sd1, rc)}}


def wind(lat, lon, edition, rc):
    time.sleep(LATENCY)
    v = _blend(lat, lon)[2] * RC_WIND[rc]
    return {"requestInfo": _request_info("wind", lat, lon, edition, rc),
            "wind": {"windSpeed": int(round(v)), "units": "mph", "description": "3-second gust at 33 ft, Exposure C"}}


def snow(lat, lon, edition, rc):
    time.sleep(LATENCY)
    pg = _blend(lat, lon)[3]
    pg = round(pg * RC_SNOW[rc]) if edition == "7-22" else round(pg / 5) * 5
    return {"requestInfo": _request_info("snow", lat, lon, edition, rc),
            "snow": {"snowResults": {"riskCategory%d" % rc: pg}, "units": "psf"}}
