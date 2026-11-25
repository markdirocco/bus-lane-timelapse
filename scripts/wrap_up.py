"""
Wrap-up for the week of MBTA recording.

1. Converts mbta_positions.db (SQLite) into Parquet files:
     data/positions_raw.parquet   every vehicle, every ping
     data/bus_positions.parquet   regular city buses only, with local time
2. Backs both Parquet files up to OneDrive (if it can find your OneDrive folder)
3. Health check: lists every gap in the recording longer than 5 minutes,
   and saves a chart of vehicles per hour for each day (health_check.png)

Setup (one time, in the buslane environment):
    pip install matplotlib

Run from the bus-lane-timelapse folder, AFTER stopping the logger:
    python wrap_up.py
"""

import os
import shutil
import sys

import duckdb

DB = "data/mbta_positions.db"
RAW = "data/positions_raw.parquet"
BUSES = "data/bus_positions.parquet"
ROUTES = "data/gtfs/routes.txt"
KEEP = ("Local Bus", "Frequent Bus", "Coverage Bus", "Supplemental Bus")
GAP_MINUTES = 5


def check_logger_stopped():
    if os.path.exists(DB + "-wal") and os.path.getsize(DB + "-wal") > 0:
        print("The -wal file is still there. Make sure the logger is stopped (Ctrl+C),")
        print("then run this again. Nothing was changed.")
        sys.exit(1)


def convert(con):
    print("1/4  Converting SQLite to Parquet (a few minutes)...")
    con.sql(f"ATTACH '{DB}' AS mbta (TYPE sqlite, READ_ONLY)")
    con.sql(f"""
        COPY (SELECT * FROM mbta.positions ORDER BY vehicle_ts)
        TO '{RAW}' (FORMAT parquet, COMPRESSION zstd)
    """)
    keep = ", ".join(f"'{k}'" for k in KEEP)
    con.sql(f"""
        COPY (
            SELECT p.*,
                   r.route_desc,
                   timezone('America/New_York', to_timestamp(p.vehicle_ts)) AS ts_local
            FROM '{RAW}' p
            JOIN read_csv('{ROUTES}') r USING (route_id)
            WHERE r.route_desc IN ({keep})
            ORDER BY vehicle_id, vehicle_ts
        ) TO '{BUSES}' (FORMAT parquet, COMPRESSION zstd)
    """)
    raw_n = con.sql(f"SELECT count(*) FROM '{RAW}'").fetchall()[0][0]
    bus_n = con.sql(f"SELECT count(*) FROM '{BUSES}'").fetchall()[0][0]
    print(f"     All vehicles: {raw_n:,} rows  ({os.path.getsize(RAW) / 1e6:,.0f} MB)")
    print(f"     Buses only:   {bus_n:,} rows  ({os.path.getsize(BUSES) / 1e6:,.0f} MB)")
    print(f"     (SQLite file was {os.path.getsize(DB) / 1e6:,.0f} MB)")


def backup():
    print("2/4  Backing up to OneDrive...")
    onedrive = os.environ.get("OneDriveCommercial") or os.environ.get("OneDrive")
    if not onedrive or not os.path.isdir(onedrive):
        print("     Couldn't find your OneDrive folder. Copy the two Parquet files")
        print("     from the data folder into OneDrive by hand.")
        return
    dest = os.path.join(onedrive, "bus-lane-timelapse-backup")
    os.makedirs(dest, exist_ok=True)
    for f in (RAW, BUSES):
        shutil.copy2(f, dest)
    print(f"     Copied to {dest}")


def gaps(con):
    print(f"3/4  Gaps in the recording longer than {GAP_MINUTES} minutes:")
    rows = con.sql(f"""
        WITH polls AS (SELECT DISTINCT fetched_ts FROM '{RAW}'),
        diffs AS (
            SELECT lag(fetched_ts) OVER (ORDER BY fetched_ts) AS prev, fetched_ts
            FROM polls
        )
        SELECT strftime(timezone('America/New_York', to_timestamp(prev)), '%a %b %d %H:%M') AS gap_start,
               strftime(timezone('America/New_York', to_timestamp(fetched_ts)), '%a %b %d %H:%M') AS gap_end,
               round((fetched_ts - prev) / 60.0) AS minutes
        FROM diffs
        WHERE fetched_ts - prev > {GAP_MINUTES * 60}
        ORDER BY prev
    """).fetchall()
    if not rows:
        print("     None!")
    for start, end, mins in rows:
        print(f"     {start}  to  {end}   ({mins:.0f} min)")


def chart(con):
    print("4/4  Drawing health_check.png...")
    import matplotlib.pyplot as plt

    df = con.sql(f"""
        SELECT strftime(ts, '%a %m/%d') AS day,
               hour(ts) AS hr,
               count(DISTINCT vehicle_id) AS vehicles
        FROM (SELECT vehicle_id,
                     timezone('America/New_York', to_timestamp(fetched_ts)) AS ts
              FROM '{RAW}')
        GROUP BY ALL
        ORDER BY min(ts)
    """).df()

    fig, ax = plt.subplots(figsize=(11, 5))
    for day, d in df.groupby("day", sort=False):
        ax.plot(d["hr"], d["vehicles"], marker="o", markersize=3, label=day)
    ax.set_xticks(range(0, 24, 2))
    ax.set_xlabel("Hour of day")
    ax.set_ylabel("Vehicles seen in that hour")
    ax.set_title("MBTA vehicles recorded per hour, by day")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig("health_check.png", dpi=150)
    print("     Saved health_check.png")


def main():
    check_logger_stopped()
    os.makedirs("data", exist_ok=True)
    con = duckdb.connect()
    convert(con)
    backup()
    gaps(con)
    chart(con)
    print("\nDone.")


if __name__ == "__main__":
    main()