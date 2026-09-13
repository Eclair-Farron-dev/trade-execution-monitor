"""DuckDB ETL and bitemporal metrics: event cutoff differs from arrival cutoff."""
from datetime import timedelta

import duckdb
import pandas as pd

from monitor.config import DEFAULTS


class Store:
    def __init__(self, orders, events, path=":memory:"):
        self.db = duckdb.connect(str(path))
        self.db.register("input_orders", orders)
        self.db.register("input_events", events)
        self.db.execute("CREATE OR REPLACE TABLE source_orders AS SELECT * FROM input_orders")
        self.db.execute("CREATE OR REPLACE TABLE source_events AS SELECT * FROM input_events")
        self.as_of = None

    def close(self):
        self.db.close()

    def ingest(self, as_of):
        """Rebuild visible tables deterministically; seeking backwards is safe."""
        self.as_of = as_of
        self.db.execute("CREATE OR REPLACE TABLE raw_orders AS SELECT * FROM source_orders WHERE submitted_at <= ?", [as_of])
        self.db.execute("CREATE OR REPLACE TABLE raw_events AS SELECT * FROM source_events WHERE arrived_at <= ?", [as_of])
        self.db.execute("""
            CREATE OR REPLACE TABLE clean_orders AS
            SELECT * FROM raw_orders WHERE order_id IS NOT NULL AND quantity > 0
            QUALIFY count(*) OVER (PARTITION BY order_id) = 1
        """)
        # Conflicting payloads under one event ID are all quarantined, not silently picked.
        self.db.execute("""
            CREATE OR REPLACE TABLE checked_events AS
            WITH conflicts AS (
                SELECT event_id FROM raw_events GROUP BY event_id
                HAVING count(DISTINCT (order_id,status,quantity,event_at)) > 1
            )
            SELECT e.*, CASE
                WHEN e.event_id IS NULL THEN '事件编号缺失'
                WHEN c.event_id IS NOT NULL THEN '同一事件编号内容冲突'
                WHEN o.order_id IS NULL THEN '订单不存在或订单无效'
                WHEN e.status IS NULL OR e.status NOT IN ('FILLED','REJECTED','PARTIAL') THEN '状态无效'
                WHEN e.quantity IS NULL OR e.quantity < 0 OR e.quantity > o.quantity THEN '数量无效'
                WHEN e.status = 'FILLED' AND e.quantity != o.quantity THEN '完全成交数量不一致'
                WHEN e.status = 'REJECTED' AND e.quantity != 0 THEN '拒单数量不为零'
                WHEN e.event_at IS NULL OR e.event_at < o.submitted_at OR e.arrived_at < e.event_at THEN '时间顺序无效'
                ELSE NULL END AS issue
            FROM raw_events e LEFT JOIN clean_orders o USING (order_id)
            LEFT JOIN conflicts c USING (event_id)
        """)
        self.db.execute("CREATE OR REPLACE TABLE quarantine AS SELECT * FROM checked_events WHERE issue IS NOT NULL")
        self.db.execute("""
            CREATE OR REPLACE TABLE clean_events AS
            SELECT * EXCLUDE(issue) FROM checked_events WHERE issue IS NULL
            QUALIFY row_number() OVER (PARTITION BY event_id ORDER BY arrived_at) = 1
        """)

    def cohort(self, window_end, channel="全部", settings=DEFAULTS):
        if self.as_of is None or window_end > self.as_of:
            raise ValueError("观察窗口不能晚于当前回放时间")
        # Window is [end-5min, end-60s]. Both boundaries are explicitly included.
        start = window_end - timedelta(seconds=settings.window_seconds)
        mature = window_end - timedelta(seconds=settings.maturity_seconds)
        sql = """
            WITH latest AS (
                SELECT * FROM clean_events WHERE event_at <= ?
                QUALIFY row_number() OVER (PARTITION BY order_id ORDER BY event_at DESC,event_id DESC) = 1
            )
            SELECT o.*, e.event_id, e.status, e.event_at, e.arrived_at,
                   e.quantity AS reported_quantity,
                   epoch(e.arrived_at)-epoch(e.event_at) AS delay_seconds
            FROM clean_orders o LEFT JOIN latest e USING(order_id)
            WHERE o.submitted_at >= ? AND o.submitted_at <= ?
              AND (? = '全部' OR o.channel = ?)
            ORDER BY o.submitted_at,o.order_id
        """
        return self.db.execute(sql, [window_end, start, mature, channel, channel]).df()

    def metrics(self, window_end, channel="全部", settings=DEFAULTS):
        frame = self.cohort(window_end, channel, settings)
        self.db.register("cohort_frame", frame)
        row = self.db.execute("""
            SELECT count(*) AS orders,
                count(*) FILTER(WHERE status='FILLED') AS filled,
                count(*) FILTER(WHERE status='REJECTED') AS rejected,
                count(*) FILTER(WHERE status IS NULL) AS missing,
                count(*) FILTER(WHERE status='PARTIAL') AS partial,
                quantile_cont(delay_seconds, 0.95) AS delay_p95
            FROM cohort_frame
        """).fetchone()
        n, filled, rejected, missing, partial, p95 = row
        return {"channel": channel, "window_end": window_end.isoformat(), "as_of": self.as_of.isoformat(),
                "orders": n, "filled": filled, "rejected": rejected, "missing": missing, "partial": partial,
                "fill_rate": filled/n if n else None, "reject_rate": rejected/n if n else None,
                "missing_rate": missing/n if n else None, "delay_p95": p95,
                "provisional": missing > 0, "sufficient": n >= settings.min_orders}

    def quality(self, channel="全部"):
        predicate = "(?='全部' OR o.channel=?)"
        counts = {}
        for table in ("raw_events", "clean_events", "quarantine"):
            counts[table] = self.db.execute(
                f"SELECT count(*) FROM {table} e LEFT JOIN clean_orders o USING(order_id) WHERE {predicate}",
                [channel, channel]).fetchone()[0]
        valid = self.db.execute(
            f"SELECT count(*) FROM checked_events e LEFT JOIN clean_orders o USING(order_id) WHERE issue IS NULL AND {predicate}",
            [channel, channel]).fetchone()[0]
        counts["duplicates_removed"] = valid-counts["clean_events"]
        counts["invalid_orders"] = self.db.execute("SELECT (SELECT count(*) FROM raw_orders)-(SELECT count(*) FROM clean_orders)").fetchone()[0]
        counts["unassigned_quarantine"] = self.db.execute("SELECT count(*) FROM quarantine q LEFT JOIN clean_orders o USING(order_id) WHERE o.order_id IS NULL").fetchone()[0]
        return {"as_of": self.as_of.isoformat(), "channel": channel,
                "scope": "截至回放时刻累计到达的记录；无归属隔离记录和无效订单为全局计数", **counts}

    def evidence_rows(self, table, channel="全部", limit=100):
        if table not in {"raw_orders", "clean_orders", "raw_events", "clean_events", "quarantine"}:
            raise ValueError("Table not allowed")
        if "orders" in table:
            return self.db.execute(f"SELECT * FROM {table} WHERE (?='全部' OR channel=?) ORDER BY submitted_at DESC LIMIT ?", [channel, channel, limit]).df()
        return self.db.execute(f"""SELECT e.*,o.channel FROM {table} e LEFT JOIN clean_orders o USING(order_id)
            WHERE (?='全部' OR o.channel=?) ORDER BY arrived_at DESC LIMIT ?""", [channel, channel, limit]).df()


def serializable_records(frame):
    return __import__("json").loads(frame.to_json(orient="records", date_format="iso", force_ascii=False))
