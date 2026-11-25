"""
Import every bus route into Blender as its own object.

Creates a "BusRoutes" collection with one object per route (Route_1, Route_28, ...).
Each route follows the MBTA's official route shape, cut at every stop. Every edge
stores 24 values, slowdown_00 ... slowdown_23 (how much slower than late night
buses are on that stretch in that hour; 0.6 = 60% slower), and has_data
(1 = measured, 0 = no data, so leave it dim).

Read those values with Geometry Nodes (Named Attribute node, Float, domain Edge).

Rerun any time: it rebuilds only the BusRoutes collection.
"""

import bpy, json

JSON_PATH = r"C:\Users\pmd04601\Desktop\bus-lane-timelapse\blender\routes.json"
SCALE = 0.01          # 1 Blender unit = 100 meters (same as import_data.py)
COLL_NAME = "BusRoutes"

with open(JSON_PATH) as f:
    data = json.load(f)

old = bpy.data.collections.get(COLL_NAME)
if old:
    for ob in list(old.objects):
        me = ob.data
        bpy.data.objects.remove(ob, do_unlink=True)
        if me and me.users == 0:
            bpy.data.meshes.remove(me)
    bpy.data.collections.remove(old)
coll = bpy.data.collections.new(COLL_NAME)
bpy.context.scene.collection.children.link(coll)

for route in data["routes"]:
    segs = route["segments"]
    verts, edges = [], []
    edge_seg = []                     # which segment each edge belongs to
    for k, s in enumerate(segs):
        start = len(verts)
        verts += [(x * SCALE, y * SCALE, 0) for x, y in s["line"]]
        for i in range(start, len(verts) - 1):
            edges.append((i, i + 1))
            edge_seg.append(k)

    me = bpy.data.meshes.new(f"Route_{route['route_id']}")
    me.from_pydata(verts, edges, [])
    for h in range(24):
        attr = me.attributes.new(f"slowdown_{h:02d}", "FLOAT", "EDGE")
        attr.data.foreach_set("value", [segs[k]["slowdown_by_hour"][h] for k in edge_seg])
    attr = me.attributes.new("has_data", "INT", "EDGE")
    attr.data.foreach_set("value", [int(segs[k]["has_data"]) for k in edge_seg])
    me.update()

    ob = bpy.data.objects.new(f"Route_{route['route_id']}", me)
    ob["route_id"] = route["route_id"]
    ob["route_desc"] = route["route_desc"]
    coll.objects.link(ob)

print(f"Imported {len(data['routes'])} routes, "
      f"{sum(len(r['segments']) for r in data['routes'])} segments into '{COLL_NAME}'.")