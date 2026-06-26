from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class CampaignStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class AdStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class ABTestStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"


class Campaign(BaseModel):
    id: str = Field(default_factory=_new_id)
    name: str
    status: CampaignStatus = CampaignStatus.ACTIVE
    daily_budget: float
    bid: float
    targeting: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utcnow)


class Ad(BaseModel):
    id: str = Field(default_factory=_new_id)
    campaign_id: str
    text: str
    url: str = ""
    status: AdStatus = AdStatus.ACTIVE
    created_at: datetime = Field(default_factory=_utcnow)


class MetricSnapshot(BaseModel):
    timestamp: datetime = Field(default_factory=_utcnow)
    campaign_id: str
    ad_id: str | None = None
    impressions: int = 0
    clicks: int = 0
    conversions: int = 0
    spend: float = 0.0

    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def cr(self) -> float:
        return self.conversions / self.clicks if self.clicks else 0.0

    @property
    def cpc(self) -> float:
        return self.spend / self.clicks if self.clicks else 0.0

    @property
    def cpm(self) -> float:
        return (self.spend / self.impressions * 1000) if self.impressions else 0.0

    @property
    def cpl(self) -> float:
        return self.spend / self.conversions if self.conversions else 0.0


class ActionType(str, Enum):
    BID_CHANGE = "bid_change"
    BUDGET_REALLOC = "budget_realloc"
    PAUSE_CAMPAIGN = "pause_campaign"
    RESUME_CAMPAIGN = "resume_campaign"
    PAUSE_AD = "pause_ad"
    AB_TEST_WINNER = "ab_test_winner"
    STRATEGY_NOTE = "strategy_note"
    ALERT = "alert"


class OptimizationAction(BaseModel):
    id: str = Field(default_factory=_new_id)
    timestamp: datetime = Field(default_factory=_utcnow)
    campaign_id: str | None = None
    action_type: ActionType
    old_value: str = ""
    new_value: str = ""
    reason: str = ""


class ABTest(BaseModel):
    id: str = Field(default_factory=_new_id)
    campaign_id: str
    variant_a_id: str
    variant_b_id: str
    metric: str = "ctr"
    status: ABTestStatus = ABTestStatus.RUNNING
    winner_id: str | None = None
    confidence: float | None = None
    created_at: datetime = Field(default_factory=_utcnow)
