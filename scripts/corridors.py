"""
Helpers for the bus lane analysis: coordinates, lane direction matching,
grouping stop-to-stop segments into corridors, and the recommendation rule.
Used by notebooks 02 and 03 and by export_for_blender.py.
"""

import math, re
import pandas as pd

K = 111320 * math.cos(math.radians(42.33)); L = 110540
def xy(lon, lat): return (lon * K, lat * L)

SEP = re.compile(r" (@|opp) ")
def street(n):
    m = SEP.search(n)
    return n[:m.start()] if m else ""
def cross(n):
    m = SEP.search(n)
    return n[m.end():] if m else n

def bearing(x1, y1, x2, y2):
    return (math.degrees(math.atan2(x2 - x1, y2 - y1)) + 360) % 360

def angle_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)

# Lanes drawn opposite to the direction in their name
REVERSED = {"Malden - Centre St (EB)", "Malden - Centre St WB",
            "Washington St Kneeland to Marginal (SB)"}
# Highways and busways: not street lanes
SKIP = ("I-93", "Tobin", "Haul Road", "Silver Line Way", "SL3", "Alewife")
DIR = re.compile(r"\b(NB|SB|EB|WB|Inbound|Outbound)\b", re.I)

def load_lanes(geojson):
    """Turn the MBTA bus lane GeoJSON into (line, mode) pairs in meters."""
    from shapely.geometry import LineString
    parts = []
    for feat in geojson["features"]:
        name = feat["properties"]["Name"]
        if any(s in name for s in SKIP):
            continue
        mode = "reversed" if name in REVERSED else ("drawn" if DIR.search(name) else "unknown")
        g = feat["geometry"]
        lines = [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]
        for p in lines:
            parts.append((LineString([xy(x, y) for x, y in p]), mode))
    return parts

def classify(lane_parts, lon1, lat1, lon2, lat2, near_m=30):
    """'Bus lane' if the bus travels the same way as a lane it runs along,
    'Unsure' if it runs along a lane with no known direction, else 'No lane'."""
    from shapely.geometry import Point
    a, b = xy(lon1, lat1), xy(lon2, lat2)
    seg_b = bearing(*a, *b)
    mid = Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    unsure = False
    for line, mode in lane_parts:
        if line.distance(Point(a)) > near_m or line.distance(Point(b)) > near_m:
            continue
        if mode == "unknown":
            unsure = True
            continue
        d = line.project(mid)
        p1 = line.interpolate(max(d - 15, 0))
        p2 = line.interpolate(min(d + 15, line.length))
        diff = angle_diff(seg_b, bearing(p1.x, p1.y, p2.x, p2.y))
        if (mode == "drawn" and diff < 45) or (mode == "reversed" and diff > 135):
            return "Bus lane"
    return "Unsure" if unsure else "No lane"


def build_corridors(seg, link_m=150, return_segments=False):
    seg = seg.reset_index(drop=True).copy()
    labels, on_street = [], []
    for f, t in zip(seg.f, seg.t):
        a, b = street(f), street(t)
        if a and a == b:
            labels.append(a); on_street.append(True)
        elif a and not b:
            labels.append(f"{a} into {t}"); on_street.append(False)
        elif b and not a:
            labels.append(f"{b} out of {f}"); on_street.append(False)
        elif not a and not b:
            labels.append(f"{f} to {t}"); on_street.append(False)
        else:
            labels.append(f"{a} to {b}"); on_street.append(False)
    seg["label"], seg["on_street"] = labels, on_street

    out = []
    seg["corridor_id"] = -1
    for (label, town), g in seg.groupby(["label", "town"]):
        orig_idx = g.index.to_numpy()
        g = g.reset_index(drop=True)
        pts = [[xy(r.lon1, r.lat1), xy(r.lon2, r.lat2)] for r in g.itertuples()]
        parent = list(range(len(g)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i
        for i in range(len(g)):
            for j in range(i + 1, len(g)):
                if min(math.dist(p, q) for p in pts[i] for q in pts[j]) <= link_m:
                    parent[find(i)] = find(j)
        g["comp"] = [find(i) for i in range(len(g))]

        for _, c in g.groupby("comp"):
            cid = len(out)
            seg.loc[orig_idx[c.index.to_numpy()], "corridor_id"] = cid
            stops = []
            for r in c.itertuples():
                stops.append((r.f, *xy(r.lon1, r.lat1)))
                stops.append((r.t, *xy(r.lon2, r.lat2)))
            xs = [s[1] for s in stops]; ys = [s[2] for s in stops]
            k = 1 if max(xs) - min(xs) > max(ys) - min(ys) else 2
            lo = cross(min(stops, key=lambda s: s[k])[0])
            hi = cross(max(stops, key=lambda s: s[k])[0])
            name = f"{label}: {lo} to {hi}" if c.on_street.iloc[0] and lo != hi else label
            routes = sorted({r.strip() for rs in c.routes for r in rs.split(",")},
                            key=lambda r: (not r.isdigit(), int(r) if r.isdigit() else 0, r))
            lo_pt = min(stops, key=lambda s: s[k]); hi_pt = max(stops, key=lambda s: s[k])
            miles = math.dist(lo_pt[1:], hi_pt[1:]) / 1609.34
            row = {
                "corridor_id": cid,
                "corridor": name, "town": town, "segments": len(c),
                "rider_minutes_lost": round(c.rider_min.clip(lower=0).sum()),
                "miles": round(miles, 2),
                "routes": ", ".join(routes),
            }
            if "lane_type" in c:
                row["pct_lane"] = round(100 * (c.lane_type == "Bus lane").mean())
                row["pct_unsure"] = round(100 * (c.lane_type == "Unsure").mean())
            out.append({**row,
                "lon": (c.lon1.mean() + c.lon2.mean()) / 2,
                "lat": (c.lat1.mean() + c.lat2.mean()) / 2,
            })
    corr = (pd.DataFrame(out)
            .sort_values("rider_minutes_lost", ascending=False)
            .reset_index(drop=True))
    return (corr, seg) if return_segments else corr



# ---------- recommendation groups (used by notebook 03 and export_for_blender.py) ----------

FIX_COLORS = {
    "Build new lanes":        "#2a78d6",   # blue
    "Spot fix (choke point)": "#eb6834",   # orange
    "Extend existing lanes":  "#1baf7a",   # green
}

def fix_type(r):
    """Which kind of fix a corridor needs. r = one row of the corridors table."""
    if r.miles < 0.75 or r.segments == 1:
        return "Spot fix (choke point)"
    if r.pct_lane + r.pct_unsure >= 15:
        return "Extend existing lanes"
    return "Build new lanes"
