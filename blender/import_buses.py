"""
Import Tuesday's bus positions into Blender as one point cloud.

Creates a "Buses" object (in a "BusDots" collection): one point per bus per minute,
5:00am to 9:59pm on Tuesday, Nov 17, 2026. Each point has these attributes
(read them in Geometry Nodes with a Named Attribute node, domain Point):
  minute    0 = 5:00am ... 1019 = 9:59pm  (use 1 frame = 1 minute)
  vehicle   a number for each bus, same bus = same number (handy for trails)
  route     a number for each route; the lookup is in the object's custom
            property "route_lookup" (for example, which number is route 28)

Rerun any time: it rebuilds only the BusDots collection.
"""

import bpy, csv, json

CSV_PATH = r"C:\Users\pmd04601\Desktop\bus-lane-timelapse\blender\buses.csv"
SCALE = 0.01          # 1 Blender unit = 100 meters (same as the other importers)
COLL_NAME = "BusDots"

minutes, coords, vehicles, routes = [], [], [], []
veh_ids, route_ids = {}, {}
with open(CSV_PATH, newline="") as f:
    for row in csv.DictReader(f):
        minutes.append(int(row["minute"]))
        coords += [float(row["x"]) * SCALE, float(row["y"]) * SCALE, 0.0]
        vehicles.append(veh_ids.setdefault(row["vehicle_id"], len(veh_ids)))
        routes.append(route_ids.setdefault(row["route_id"], len(route_ids)))

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

me = bpy.data.meshes.new("Buses")
me.vertices.add(len(minutes))
me.vertices.foreach_set("co", coords)
for name, values in (("minute", minutes), ("vehicle", vehicles), ("route", routes)):
    attr = me.attributes.new(name, "INT", "POINT")
    attr.data.foreach_set("value", values)
me.update()

ob = bpy.data.objects.new("Buses", me)
ob["route_lookup"] = json.dumps({r: i for r, i in route_ids.items()})
coll.objects.link(ob)

print(f"Imported {len(minutes):,} bus dots ({len(veh_ids):,} buses, {len(route_ids)} routes) into '{COLL_NAME}'.")