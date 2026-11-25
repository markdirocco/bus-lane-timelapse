"""
MBTA vehicle position logger.

Polls the MBTA GTFS-Realtime VehiclePositions feed every 15 seconds and
saves every vehicle's position to a local SQLite database. Duplicate
reports (same vehicle, same timestamp) are skipped automatically.

Setup:
    pip install requests gtfs-realtime-bindings

Run:
    python mbta_logger.py
Stop with Ctrl+C.
"""

import logging
import sqlite3
import time
from datetime import datetime, timezone

import requests
from google.transit import gtfs_realtime_pb2

FEED_URL = "https://cdn.mbta.com/realtime/VehiclePositions.pb"
DB_PATH = "mbta_positions.db"
POLL_SECONDS = 15
TIMEOUT_SECONDS = 10

logging.basicConfig(
    filename="mbta_logger.log",
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    vehicle_id      TEXT NOT NULL,
    vehicle_label   TEXT,
    route_id        TEXT,
    trip_id         TEXT,
    direction_id    INTEGER,
    lat             REAL,
    lon             REAL,
    bearing         REAL,
    speed_mps       REAL,
    current_status  INTEGER,
    stop_id         TEXT,
    stop_sequence   INTEGER,
    occupancy       INTEGER,
    vehicle_ts      INTEGER NOT NULL,   -- when the vehicle reported (unix seconds)
    fetched_ts      INTEGER NOT NULL,   -- when we downloaded it (unix seconds)
    PRIMARY KEY (vehicle_id, vehicle_ts)
);
"""


def open_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")  # safer if the script is killed mid-write
    conn.execute(SCHEMA)
    return conn


def fetch_feed():
    resp = requests.get(FEED_URL, timeout=TIMEOUT_SECONDS)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def feed_to_rows(feed, fetched_ts):
    rows = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        if not v.HasField("position") or not v.vehicle.id:
            continue
        rows.append((
            v.vehicle.id,
            v.vehicle.label or None,
            v.trip.route_id or None,
            v.trip.trip_id or None,
            v.trip.direction_id if v.trip.HasField("direction_id") else None,
            v.position.latitude,
            v.position.longitude,
            v.position.bearing if v.position.HasField("bearing") else None,
            v.position.speed if v.position.HasField("speed") else None,
            v.current_status if v.HasField("current_status") else None,
            v.stop_id or None,
            v.current_stop_sequence if v.HasField("current_stop_sequence") else None,
            v.occupancy_status if v.HasField("occupancy_status") else None,
            v.timestamp or fetched_ts,
            fetched_ts,
        ))
    return rows


def save_rows(conn, rows):
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO positions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def main():
    conn = open_db(DB_PATH)
    print(f"Logging to {DB_PATH} every {POLL_SECONDS}s. Ctrl+C to stop.")
    logging.info("Logger started")
    polls = 0
    try:
        while True:
            start = time.time()
            try:
                fetched_ts = int(start)
                rows = feed_to_rows(fetch_feed(), fetched_ts)
                new = save_rows(conn, rows)
                polls += 1
                if polls % 40 == 0:  # roughly every 10 minutes
                    stamp = datetime.now(timezone.utc).astimezone().strftime("%a %H:%M")
                    msg = f"{stamp}  poll {polls}: {len(rows)} vehicles, {new} new rows"
                    print(msg)
                    logging.info(msg)
            except Exception as e:  # network blips, bad responses, etc.
                logging.warning(f"Poll failed: {e}")
            elapsed = time.time() - start
            time.sleep(max(0, POLL_SECONDS - elapsed))
    except KeyboardInterrupt:
        print("\nStopped.")
        logging.info("Logger stopped by user")
    finally:
        conn.close()


if __name__ == "__main__":
    main()