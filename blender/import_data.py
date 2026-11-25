"""
Import the bus corridor data into Blender as plain geometry. No styling.

Creates a "BusLaneData" collection with:
  - Corridor_01 ... Corridor_20   curves along each corridor (no thickness yet)
  - ExistingLanes                 curves where a bus lane already exists
  - Label_01 ... Label_20         empties marking where each corridor's number goes
  - Place_<name>                  empties at place names (Harvard Sq, Nubian, ...)

Each Corridor object carries custom properties (Object Properties > Custom Properties):
  rank, name, group, color, rider_hours, and slowdown_00 ... slowdown_23
  (how much slower than late night buses are in each hour; 0.63 = 63% slower).

Rerun any time: it rebuilds only the BusLaneData collection.
"""

import bpy, json

JSON_PATH = r"C:\Users\pmd04601\Desktop\bus-lane-timelapse\blender\corridors.json"
SCALE = 0.01          # 1 Blender unit = 100 meters
COLL_NAME = "BusLaneData"

with open(JSON_PATH) as f:
    data = json.load(f)

old = bpy.data.collections.get(COLL_NAME)
if old:
    for ob in list(old.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    bpy.data.collections.remove(old)
coll = bpy.data.collections.new(COLL_NAME)
bpy.context.scene.collection.children.link(coll)

def curve(name, polylines):
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    for pts in polylines:
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        for p, (x, y) in zip(sp.points, pts):
            p.co = (x * SCALE, y * SCALE, 0, 1)
    ob = bpy.data.objects.new(name, cu)
    coll.objects.link(ob)
    return ob

def empty(name, x, y):
    ob = bpy.data.objects.new(name, None)
    ob.location = (x * SCALE, y * SCALE, 0)
    ob.empty_display_type = "PLAIN_AXES"
    ob.empty_display_size = 0.5
    coll.objects.link(ob)
    return ob

for c in data["corridors"]:
    ob = curve(f"Corridor_{c['rank']:02d}", c["lines"])
    ob["rank"] = c["rank"]
    ob["name"] = c["name"]
    ob["group"] = c["group"]
    ob["color"] = c["color"]
    ob["rider_hours"] = c["rider_hours"]
    for h, v in enumerate(c["slowdown_by_hour"]):
        ob[f"slowdown_{h:02d}"] = v
    lab = empty(f"Label_{c['rank']:02d}", *c["label_at"])
    lab["rank"] = c["rank"]

if data["lanes"]:
    curve("ExistingLanes", data["lanes"])

for p in data["places"]:
    empty(f"Place_{p['name'].replace(' ', '_')}", *p["at"])

print(f"Imported {len(data['corridors'])} corridors, {len(data['lanes'])} lane pieces, "
      f"{len(data['places'])} places into '{COLL_NAME}'.")