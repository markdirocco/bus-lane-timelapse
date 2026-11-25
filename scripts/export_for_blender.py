"""
Export the analysis for the Blender animation.

Writes three files to blender/, all in Massachusetts State Plane meters (EPSG:26986),
centered on a point near Nubian Square so Blender works near its origin:

  corridors.json  top 20 corridors: geometry, fix type, color, rider-hours, hourly slowdown (Act 2)
  buses.csv       every bus position once a minute, Tuesday Nov 17, 5:00am to 9:59pm (moving dots)
  routes.json     every bus route along its official shape, cut at stops, with the average
                  weekday slowdown for each hour (the glowing street network)

Run from the project folder AFTER notebooks 02 and 03 (it reads the tables they save):
    python scripts/export_for_blender.py

Blender importers: blender/import_data.py, import_buses.py, import_routes.py
"""

import json, os, sys
from pathlib import Path

import duckdb
import numpy as np
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import substring, unary_union

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "scripts"))
from corridors import xy, K, L, fix_type, FIX_COLORS

G = "data/gtfs"
OUT = Path("blender")
SILVER_LINE_TUNNEL = ("741", "742", "743", "746")
BUS_CATEGORIES = ("Local Bus", "Frequent Bus", "Coverage Bus", "Supplemental Bus")

to_m = Transformer.from_crs("EPSG:4326", "EPSG:26986", always_xy=True)
CX, CY = to_m.transform(-71.09, 42.335)      # map center, near Nubian

def m(lon, lat):
    x, y = to_m.transform(lon, lat)
    return [round(x - CX, 1), round(y - CY, 1)]

def to_xy(df):
    x, y = to_m.transform(df.lon.values, df.lat.values)
    return np.column_stack([x - CX, y - CY])


def export_corridors(con):
    corr = con.sql("SELECT * FROM corridors ORDER BY rider_minutes_lost DESC").df()
    seg = con.sql("SELECT * FROM corridor_segments").df()
    top = corr.head(20).copy()
    top["fix"] = top.apply(fix_type, axis=1)
    top["rank"] = range(1, len(top) + 1)

    # hour-by-hour slowdown for each corridor vs late night (weekdays)
    con.register("seg_ids", seg[["corridor_id", "from_stop_id", "to_stop_id"]])
    hourly = con.sql("""
    WITH by_hour AS (
        SELECT from_stop_id, to_stop_id, hour(depart_local) AS h, avg(run_secs) AS secs
        FROM segments
        WHERE dayofweek(depart_local) BETWEEN 1 AND 5 AND run_secs > 0 AND run_secs <= 900
        GROUP BY ALL
    ),
    night AS (
        SELECT from_stop_id, to_stop_id, avg(secs) AS night_secs
        FROM by_hour WHERE h BETWEEN 20 AND 22 GROUP BY ALL
    )
    SELECT i.corridor_id, b.h, sum(b.secs) / sum(n.night_secs) - 1 AS slowdown
    FROM seg_ids i
    JOIN by_hour b USING (from_stop_id, to_stop_id)
    JOIN night n USING (from_stop_id, to_stop_id)
    GROUP BY ALL
    """).df()

    out = {"crs": "EPSG:26986", "origin": [CX, CY], "corridors": [], "lanes": [], "places": []}
    for r in top.itertuples():
        s = seg[seg.corridor_id == r.corridor_id]
        hrs = hourly[hourly.corridor_id == r.corridor_id].set_index("h").slowdown
        out["corridors"].append({
            "rank": int(r.rank), "name": r.corridor, "group": r.fix, "color": FIX_COLORS[r.fix],
            "rider_hours": float(r.rider_hours), "label_at": m(r.lon, r.lat),
            "slowdown_by_hour": [round(max(float(hrs.get(h, 0)), 0), 3) for h in range(24)],
            "lines": [[m(a, b), m(c, d)] for a, b, c, d in zip(s.lon1, s.lat1, s.lon2, s.lat2)]})

    # existing bus lanes, clipped to the recommended corridors
    with open("data/mbta_bus_lanes.geojson") as f:
        lanes_gj = json.load(f)
    top_seg = seg[seg.corridor_id.isin(top.corridor_id)]
    zone = unary_union([LineString([xy(a, b), xy(c, d)]) for a, b, c, d in
                        zip(top_seg.lon1, top_seg.lat1, top_seg.lon2, top_seg.lat2)]).buffer(60)
    skip = ("I-93", "Tobin", "Haul Road", "Silver Line Way", "SL3", "Alewife")
    for feat in lanes_gj["features"]:
        if any(s in feat["properties"]["Name"] for s in skip):
            continue
        g = feat["geometry"]
        for p in [g["coordinates"]] if g["type"] == "LineString" else g["coordinates"]:
            clip = LineString([xy(x, y) for x, y in p]).intersection(zone)
            for piece in getattr(clip, "geoms", [clip]):
                if piece.geom_type == "LineString" and piece.length > 20:
                    out["lanes"].append([m(x / K, y / L) for x, y in piece.coords])

    places = {"Harvard Sq": (-71.119, 42.373), "Nubian": (-71.083, 42.331),
              "Mattapan": (-71.092, 42.268), "Chelsea": (-71.033, 42.392),
              "Roslindale": (-71.127, 42.287), "Longwood": (-71.108, 42.338)}
    out["places"] = [{"name": n, "at": m(x, y)} for n, (x, y) in places.items()]

    with open(OUT / "corridors.json", "w") as f:
        json.dump(out, f)
    print(f"corridors.json: {len(out['corridors'])} corridors, {len(out['lanes'])} lane pieces")


def export_buses(con):
    dots = con.sql("""
    WITH win AS (   -- Tuesday Nov 17, 5:00am to 10:00pm Boston time, as unix seconds
        SELECT epoch(timezone('America/New_York', TIMESTAMP '2026-11-17 05:00:00'))::BIGINT AS t0,
               epoch(timezone('America/New_York', TIMESTAMP '2026-11-17 22:00:00'))::BIGINT AS t1
    ),
    pings AS (
        SELECT vehicle_id, route_id, vehicle_ts AS ts, lat, lon
        FROM 'data/bus_positions.parquet', win
        WHERE vehicle_ts BETWEEN t0 - 300 AND t1 + 300
    ),
    span AS (
        SELECT vehicle_id, min(ts) AS first_ts, max(ts) AS last_ts FROM pings GROUP BY vehicle_id
    ),
    grid AS (   -- every whole minute each bus was active
        SELECT s.vehicle_id, w.t0 + m * 60 AS t
        FROM span s, win w,
             range((greatest(s.first_ts, w.t0) - w.t0 + 59) // 60,
                   (least(s.last_ts, w.t1 - 1) - w.t0) // 60 + 1) AS r(m)
    ),
    before AS (   -- nearest ping at or before each minute
        SELECT g.vehicle_id, g.t, p.ts AS ts0, p.lat AS lat0, p.lon AS lon0, p.route_id
        FROM grid g ASOF JOIN pings p ON g.vehicle_id = p.vehicle_id AND g.t >= p.ts
    ),
    after AS (    -- nearest ping at or after each minute
        SELECT g.vehicle_id, g.t, p.ts AS ts1, p.lat AS lat1, p.lon AS lon1
        FROM grid g ASOF JOIN pings p ON g.vehicle_id = p.vehicle_id AND g.t <= p.ts
    )
    SELECT b.vehicle_id, b.route_id, (b.t - w.t0) // 60 AS minute,
           CASE WHEN ts1 = ts0 THEN lat0 ELSE lat0 + (lat1 - lat0) * (b.t - ts0) / (ts1 - ts0) END AS lat,
           CASE WHEN ts1 = ts0 THEN lon0 ELSE lon0 + (lon1 - lon0) * (b.t - ts0) / (ts1 - ts0) END AS lon
    FROM before b JOIN after a USING (vehicle_id, t), win w
    WHERE ts1 - ts0 <= 180          -- skip gaps over 3 minutes (bus offline or out of service)
    ORDER BY minute, vehicle_id
    """).df()

    x, y = to_m.transform(dots.lon.values, dots.lat.values)
    dots["x"], dots["y"] = (x - CX).round(1), (y - CY).round(1)
    dots[["minute", "x", "y", "vehicle_id", "route_id"]].to_csv(OUT / "buses.csv", index=False)
    print(f"buses.csv: {len(dots):,} dots, {dots.vehicle_id.nunique():,} buses, "
          f"busiest minute has {dots.groupby('minute').size().max():,} buses")


def export_routes(con):
    # 1. average weekday slowdown for every stop-to-stop pair, by hour
    tunnel = ", ".join(f"'{r}'" for r in SILVER_LINE_TUNNEL)
    hourly = con.sql(f"""
    WITH seg_h AS (
        SELECT from_stop_id, to_stop_id, hour(depart_local) AS h, avg(run_secs) AS secs, count(*) AS n
        FROM segments
        WHERE dayofweek(depart_local) BETWEEN 1 AND 5 AND run_secs > 0 AND run_secs <= 900
          AND route_id NOT IN ({tunnel})
        GROUP BY ALL
    ),
    night AS (
        SELECT from_stop_id, to_stop_id, sum(secs * n) / sum(n) AS night_secs
        FROM seg_h WHERE h BETWEEN 20 AND 22 GROUP BY ALL HAVING sum(n) >= 10
    )
    SELECT b.from_stop_id, b.to_stop_id, b.h,
           CASE WHEN b.n >= 3 THEN least(greatest(b.secs / n.night_secs - 1, 0), 3) ELSE 0 END AS slowdown
    FROM seg_h b JOIN night n USING (from_stop_id, to_stop_id)
    """).df()
    slow = {}
    for r in hourly.itertuples():
        slow.setdefault((r.from_stop_id, r.to_stop_id), [0.0] * 24)[r.h] = round(float(r.slowdown), 3)

    # 2. the most-used shape for each bus route and direction, plus one trip to read its stops from
    cats = ", ".join(f"'{c}'" for c in BUS_CATEGORIES)
    picks = con.sql(f"""
    WITH bus AS (
        SELECT route_id, route_desc FROM read_csv('{G}/routes.txt', all_varchar = true, quote = '"')
        WHERE route_desc IN ({cats}) AND route_id NOT IN ({tunnel})
    ),
    t AS (
        SELECT t.route_id, t.direction_id, t.shape_id, any_value(t.trip_id) AS trip_id, count(*) AS trips
        FROM read_csv('{G}/trips.txt', all_varchar = true, quote = '"') t JOIN bus USING (route_id)
        GROUP BY ALL
    )
    SELECT route_id, direction_id, shape_id, trip_id, bus.route_desc
    FROM t JOIN bus USING (route_id)
    QUALIFY row_number() OVER (PARTITION BY route_id, direction_id ORDER BY trips DESC) = 1
    """).df()
    con.register("picks", picks)

    shape_pts = con.sql(f"""
        SELECT shape_id, try_cast(shape_pt_lon AS DOUBLE) AS lon, try_cast(shape_pt_lat AS DOUBLE) AS lat
        FROM read_csv('{G}/shapes.txt', all_varchar = true, quote = '"')
        WHERE shape_id IN (SELECT shape_id FROM picks)
        ORDER BY shape_id, try_cast(shape_pt_sequence AS INT)
    """).df()
    trip_stops = con.sql(f"""
        SELECT st.trip_id, st.stop_id,
               try_cast(s.stop_lon AS DOUBLE) AS lon, try_cast(s.stop_lat AS DOUBLE) AS lat
        FROM read_csv('{G}/stop_times.txt', all_varchar = true, quote = '"') st
        JOIN read_csv('{G}/stops.txt', all_varchar = true, quote = '"') s USING (stop_id)
        WHERE st.trip_id IN (SELECT trip_id FROM picks)
        ORDER BY st.trip_id, try_cast(st.stop_sequence AS INT)
    """).df()

    shapes = {sid: to_xy(g) for sid, g in shape_pts.groupby("shape_id", sort=False)}
    stops_by_trip = {tid: (g.stop_id.tolist(), to_xy(g)) for tid, g in trip_stops.groupby("trip_id", sort=False)}

    # 3. cut each shape at its stops and attach each piece's slowdown
    routes = {}
    for p in picks.itertuples():
        if p.shape_id not in shapes or p.trip_id not in stops_by_trip:
            continue
        line = LineString(shapes[p.shape_id])
        ids, pts = stops_by_trip[p.trip_id]
        dist = [line.project(Point(pt)) for pt in pts]
        pieces = []
        for i in range(len(ids) - 1):
            d0, d1 = dist[i], dist[i + 1]
            if d1 <= d0:
                continue
            geom = substring(line, d0, d1)
            if geom.geom_type != "LineString" or geom.length < 1:
                continue
            vals = slow.get((ids[i], ids[i + 1]))
            pieces.append({"from": ids[i], "to": ids[i + 1],
                           "line": [[round(x, 1), round(y, 1)] for x, y in geom.coords],
                           "has_data": vals is not None,
                           "slowdown_by_hour": vals or [0.0] * 24})
        r = routes.setdefault(p.route_id, {"route_id": p.route_id, "route_desc": p.route_desc, "segments": []})
        r["segments"] += pieces

    out = {"crs": "EPSG:26986", "origin": [CX, CY], "routes": list(routes.values())}
    with open(OUT / "routes.json", "w") as f:
        json.dump(out, f)
    segs = [s for r in out["routes"] for s in r["segments"]]
    print(f"routes.json: {len(out['routes'])} routes, {len(segs):,} pieces, "
          f"{sum(s['has_data'] for s in segs) / len(segs):.0%} with slowdown data")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    con = duckdb.connect("data/analysis.duckdb")
    export_corridors(con)
    export_buses(con)
    export_routes(con)
    con.close()
