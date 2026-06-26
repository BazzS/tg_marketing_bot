from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    ABTest,
    ABTestStatus,
    ActionType,
    Ad,
    AdStatus,
    Campaign,
    CampaignStatus,
    MetricSnapshot,
    OptimizationAction,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    daily_budget REAL NOT NULL,
    bid REAL NOT NULL,
    targeting TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ads (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    text TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    ad_id TEXT,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    conversions INTEGER NOT NULL DEFAULT 0,
    spend REAL NOT NULL DEFAULT 0.0
);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    campaign_id TEXT,
    action_type TEXT NOT NULL,
    old_value TEXT NOT NULL DEFAULT '',
    new_value TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ab_tests (
    id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    variant_a_id TEXT NOT NULL,
    variant_b_id TEXT NOT NULL,
    metric TEXT NOT NULL DEFAULT 'ctr',
    status TEXT NOT NULL DEFAULT 'running',
    winner_id TEXT,
    confidence REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_campaign ON metrics(campaign_id);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions(timestamp);
"""


class Database:
    def __init__(self, db_path: str = "data/marketing.db"):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def close(self):
        self._conn.close()

    # ── campaigns ──

    def save_campaign(self, c: Campaign) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO campaigns VALUES (?,?,?,?,?,?,?)",
            (c.id, c.name, c.status.value, c.daily_budget, c.bid,
             json.dumps(c.targeting), c.created_at.isoformat()),
        )
        self._conn.commit()

    def get_campaign(self, campaign_id: str) -> Campaign | None:
        row = self._conn.execute(
            "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_campaign(row)

    def list_campaigns(self, status: CampaignStatus | None = None) -> list[Campaign]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM campaigns WHERE status=?", (status.value,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM campaigns").fetchall()
        return [self._row_to_campaign(r) for r in rows]

    def _row_to_campaign(self, row: sqlite3.Row) -> Campaign:
        return Campaign(
            id=row["id"], name=row["name"],
            status=CampaignStatus(row["status"]),
            daily_budget=row["daily_budget"], bid=row["bid"],
            targeting=json.loads(row["targeting"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # ── ads ──

    def save_ad(self, ad: Ad) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO ads VALUES (?,?,?,?,?,?)",
            (ad.id, ad.campaign_id, ad.text, ad.url,
             ad.status.value, ad.created_at.isoformat()),
        )
        self._conn.commit()

    def list_ads(self, campaign_id: str, status: AdStatus | None = None) -> list[Ad]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM ads WHERE campaign_id=? AND status=?",
                (campaign_id, status.value),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM ads WHERE campaign_id=?", (campaign_id,),
            ).fetchall()
        return [
            Ad(id=r["id"], campaign_id=r["campaign_id"], text=r["text"],
               url=r["url"], status=AdStatus(r["status"]),
               created_at=datetime.fromisoformat(r["created_at"]))
            for r in rows
        ]

    # ── metrics ──

    def save_metric(self, m: MetricSnapshot) -> None:
        self._conn.execute(
            "INSERT INTO metrics (timestamp,campaign_id,ad_id,impressions,clicks,conversions,spend) "
            "VALUES (?,?,?,?,?,?,?)",
            (m.timestamp.isoformat(), m.campaign_id, m.ad_id,
             m.impressions, m.clicks, m.conversions, m.spend),
        )
        self._conn.commit()

    def get_metrics(
        self, campaign_id: str, last_n: int | None = None
    ) -> list[MetricSnapshot]:
        query = "SELECT * FROM metrics WHERE campaign_id=? ORDER BY timestamp DESC"
        params: list = [campaign_id]
        if last_n:
            query += " LIMIT ?"
            params.append(last_n)
        rows = self._conn.execute(query, params).fetchall()
        return [
            MetricSnapshot(
                timestamp=datetime.fromisoformat(r["timestamp"]),
                campaign_id=r["campaign_id"], ad_id=r["ad_id"],
                impressions=r["impressions"], clicks=r["clicks"],
                conversions=r["conversions"], spend=r["spend"],
            )
            for r in reversed(rows)
        ]

    def get_total_metrics(self, campaign_id: str) -> MetricSnapshot:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(impressions),0) as impressions, "
            "COALESCE(SUM(clicks),0) as clicks, "
            "COALESCE(SUM(conversions),0) as conversions, "
            "COALESCE(SUM(spend),0) as spend "
            "FROM metrics WHERE campaign_id=?",
            (campaign_id,),
        ).fetchone()
        return MetricSnapshot(
            campaign_id=campaign_id,
            impressions=row["impressions"], clicks=row["clicks"],
            conversions=row["conversions"], spend=row["spend"],
        )

    def get_ad_metrics(self, ad_id: str) -> MetricSnapshot:
        row = self._conn.execute(
            "SELECT COALESCE(SUM(impressions),0) as impressions, "
            "COALESCE(SUM(clicks),0) as clicks, "
            "COALESCE(SUM(conversions),0) as conversions, "
            "COALESCE(SUM(spend),0) as spend "
            "FROM metrics WHERE ad_id=?",
            (ad_id,),
        ).fetchone()
        return MetricSnapshot(
            campaign_id="", ad_id=ad_id,
            impressions=row["impressions"], clicks=row["clicks"],
            conversions=row["conversions"], spend=row["spend"],
        )

    # ── actions ──

    def save_action(self, a: OptimizationAction) -> None:
        self._conn.execute(
            "INSERT INTO actions VALUES (?,?,?,?,?,?,?)",
            (a.id, a.timestamp.isoformat(), a.campaign_id,
             a.action_type.value, a.old_value, a.new_value, a.reason),
        )
        self._conn.commit()

    def list_actions(self, last_n: int = 50) -> list[OptimizationAction]:
        rows = self._conn.execute(
            "SELECT * FROM actions ORDER BY timestamp DESC LIMIT ?", (last_n,)
        ).fetchall()
        return [
            OptimizationAction(
                id=r["id"],
                timestamp=datetime.fromisoformat(r["timestamp"]),
                campaign_id=r["campaign_id"],
                action_type=ActionType(r["action_type"]),
                old_value=r["old_value"], new_value=r["new_value"],
                reason=r["reason"],
            )
            for r in reversed(rows)
        ]

    # ── ab tests ──

    def save_ab_test(self, t: ABTest) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO ab_tests VALUES (?,?,?,?,?,?,?,?,?)",
            (t.id, t.campaign_id, t.variant_a_id, t.variant_b_id,
             t.metric, t.status.value, t.winner_id, t.confidence,
             t.created_at.isoformat()),
        )
        self._conn.commit()

    def list_ab_tests(
        self, status: ABTestStatus | None = None
    ) -> list[ABTest]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM ab_tests WHERE status=?", (status.value,)
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM ab_tests").fetchall()
        return [
            ABTest(
                id=r["id"], campaign_id=r["campaign_id"],
                variant_a_id=r["variant_a_id"], variant_b_id=r["variant_b_id"],
                metric=r["metric"], status=ABTestStatus(r["status"]),
                winner_id=r["winner_id"], confidence=r["confidence"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]
