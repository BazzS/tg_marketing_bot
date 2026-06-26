"""Mock ad platform with realistic simulation.

Simulates: bid→impressions curve, CTR variance per creative,
fatigue effect, time-of-day multiplier, budget exhaustion.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .models import Ad, AdStatus, Campaign, CampaignStatus, MetricSnapshot
from .platform_base import AdPlatformClient

# hour → multiplier (peak at 10-13 and 19-21)
_TOD_CURVE = {
    0: 0.3, 1: 0.2, 2: 0.15, 3: 0.1, 4: 0.1, 5: 0.15,
    6: 0.3, 7: 0.5, 8: 0.7, 9: 0.85, 10: 1.0, 11: 1.0,
    12: 0.95, 13: 0.9, 14: 0.8, 15: 0.75, 16: 0.8, 17: 0.85,
    18: 0.95, 19: 1.0, 20: 1.0, 21: 0.9, 22: 0.7, 23: 0.5,
}


@dataclass
class _AdState:
    ad: Ad
    base_ctr: float
    base_cr: float
    cycles_active: int = 0


@dataclass
class _CampaignState:
    campaign: Campaign
    ads: dict[str, _AdState] = field(default_factory=dict)
    cycles_active: int = 0
    total_spend: float = 0.0


class MockAdPlatform(AdPlatformClient):
    def __init__(self, seed: int | None = None):
        self._campaigns: dict[str, _CampaignState] = {}
        self._rng = random.Random(seed)
        self._cycle: int = 0

    def advance_cycle(self) -> None:
        self._cycle += 1

    @property
    def current_hour(self) -> int:
        return self._cycle % 24

    def create_campaign(self, campaign: Campaign) -> Campaign:
        self._campaigns[campaign.id] = _CampaignState(campaign=campaign)
        return campaign

    def update_campaign(self, campaign: Campaign) -> Campaign:
        if campaign.id in self._campaigns:
            self._campaigns[campaign.id].campaign = campaign
        return campaign

    def pause_campaign(self, campaign_id: str) -> None:
        if campaign_id in self._campaigns:
            self._campaigns[campaign_id].campaign.status = CampaignStatus.PAUSED

    def resume_campaign(self, campaign_id: str) -> None:
        if campaign_id in self._campaigns:
            self._campaigns[campaign_id].campaign.status = CampaignStatus.ACTIVE

    def create_ad(self, ad: Ad) -> Ad:
        cid = ad.campaign_id
        if cid not in self._campaigns:
            raise ValueError(f"Campaign {cid} not found")
        base_ctr = self._rng.uniform(0.008, 0.05)
        base_cr = self._rng.uniform(0.02, 0.12)
        self._campaigns[cid].ads[ad.id] = _AdState(
            ad=ad, base_ctr=base_ctr, base_cr=base_cr,
        )
        return ad

    def pause_ad(self, ad_id: str) -> None:
        for state in self._campaigns.values():
            if ad_id in state.ads:
                state.ads[ad_id].ad.status = AdStatus.PAUSED
                return

    def fetch_metrics(self, campaign_id: str) -> MetricSnapshot:
        state = self._campaigns.get(campaign_id)
        if not state or state.campaign.status != CampaignStatus.ACTIVE:
            return MetricSnapshot(campaign_id=campaign_id)

        state.cycles_active += 1
        total_imp = total_clicks = total_conv = 0
        total_spend = 0.0

        active_ads = [
            a for a in state.ads.values() if a.ad.status == AdStatus.ACTIVE
        ]
        if not active_ads:
            return MetricSnapshot(campaign_id=campaign_id)

        budget_per_ad = state.campaign.daily_budget / len(active_ads)

        for ad_state in active_ads:
            m = self._simulate_ad(state.campaign, ad_state, budget_per_ad)
            total_imp += m.impressions
            total_clicks += m.clicks
            total_conv += m.conversions
            total_spend += m.spend

        state.total_spend += total_spend
        return MetricSnapshot(
            campaign_id=campaign_id,
            impressions=total_imp, clicks=total_clicks,
            conversions=total_conv, spend=round(total_spend, 4),
        )

    def fetch_ad_metrics(self, ad_id: str, campaign_id: str) -> MetricSnapshot:
        state = self._campaigns.get(campaign_id)
        if not state:
            return MetricSnapshot(campaign_id=campaign_id, ad_id=ad_id)
        ad_state = state.ads.get(ad_id)
        if not ad_state or ad_state.ad.status != AdStatus.ACTIVE:
            return MetricSnapshot(campaign_id=campaign_id, ad_id=ad_id)

        active_count = sum(
            1 for a in state.ads.values() if a.ad.status == AdStatus.ACTIVE
        )
        budget = state.campaign.daily_budget / max(active_count, 1)
        return self._simulate_ad(state.campaign, ad_state, budget)

    def _simulate_ad(
        self, campaign: Campaign, ad_state: _AdState, budget: float,
    ) -> MetricSnapshot:
        ad_state.cycles_active += 1
        bid = campaign.bid
        tod_mult = _TOD_CURVE.get(self.current_hour, 0.5)

        # impressions: logarithmic curve on bid, scaled by budget and tod
        base_impressions = 500 * math.log1p(bid * 2) * tod_mult
        noise = self._rng.gauss(1.0, 0.15)
        impressions = max(0, int(base_impressions * noise))

        # fatigue: CTR decays ~5% per 10 cycles
        fatigue = max(0.5, 1.0 - 0.005 * ad_state.cycles_active)
        ctr = ad_state.base_ctr * fatigue * self._rng.gauss(1.0, 0.1)
        ctr = max(0.001, min(ctr, 0.15))

        clicks = 0
        for _ in range(impressions):
            if self._rng.random() < ctr:
                clicks += 1

        cr = ad_state.base_cr * self._rng.gauss(1.0, 0.15)
        cr = max(0.005, min(cr, 0.3))
        conversions = 0
        for _ in range(clicks):
            if self._rng.random() < cr:
                conversions += 1

        # spend: actual CPC varies around bid with auction dynamics
        actual_cpc = bid * self._rng.uniform(0.6, 1.0)
        spend = clicks * actual_cpc
        if spend > budget:
            ratio = budget / spend
            clicks = int(clicks * ratio)
            conversions = min(conversions, clicks)
            spend = budget

        return MetricSnapshot(
            campaign_id=campaign.id, ad_id=ad_state.ad.id,
            impressions=impressions, clicks=clicks,
            conversions=conversions, spend=round(spend, 4),
        )
