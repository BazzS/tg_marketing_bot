"""Self-learning optimizer: Thompson Sampling bids, budget reallocation,
A/B testing with chi-squared, rule-based auto-pause and alerts."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .config import Settings
from .db import Database
from .models import (
    ABTest,
    ABTestStatus,
    ActionType,
    AdStatus,
    Campaign,
    CampaignStatus,
    MetricSnapshot,
    OptimizationAction,
)
from .platform_base import AdPlatformClient

import numpy as np


# ── Thompson Sampling Bid Optimizer ──


@dataclass
class _BetaPosterior:
    alpha: float = 1.0
    beta: float = 1.0


class BidOptimizer:
    """Bayesian bandit for bid optimization using Thompson Sampling."""

    def __init__(self, settings: Settings):
        self._posteriors: dict[str, _BetaPosterior] = {}
        self._min_bid = settings.min_bid
        self._max_bid = settings.max_bid
        self._max_change = 0.25  # max 25% change per cycle

    def update(self, campaign_id: str, clicks: int, conversions: int) -> None:
        if campaign_id not in self._posteriors:
            self._posteriors[campaign_id] = _BetaPosterior()
        p = self._posteriors[campaign_id]
        p.alpha += conversions
        p.beta += max(0, clicks - conversions)

    def suggest_bid(self, campaign: Campaign) -> float | None:
        p = self._posteriors.get(campaign.id)
        if not p or (p.alpha + p.beta) < 5:
            return None  # not enough data

        sample = np.random.beta(p.alpha, p.beta)
        # higher conversion probability → can afford higher bid
        # lower → should reduce bid to cut waste
        mean = p.alpha / (p.alpha + p.beta)
        if sample > mean * 1.1:
            direction = 1.0
        elif sample < mean * 0.9:
            direction = -1.0
        else:
            return None  # no change needed

        change = campaign.bid * self._max_change * abs(sample - mean) / max(mean, 0.01)
        new_bid = campaign.bid + direction * change
        new_bid = max(self._min_bid, min(self._max_bid, new_bid))

        if abs(new_bid - campaign.bid) < 0.01:
            return None
        return round(new_bid, 3)


# ── Budget Allocator ──


class BudgetAllocator:
    """Redistributes budget from underperforming to overperforming campaigns."""

    def __init__(self, realloc_pct: float = 0.15, min_budget: float = 5.0):
        self._realloc_pct = realloc_pct
        self._min_budget = min_budget

    def reallocate(
        self, campaigns: list[Campaign], metrics: dict[str, MetricSnapshot],
    ) -> list[tuple[Campaign, float, float]]:
        """Returns list of (campaign, old_budget, new_budget) for changed ones."""
        scored: list[tuple[Campaign, float]] = []
        for c in campaigns:
            m = metrics.get(c.id)
            if not m or m.spend == 0:
                continue
            efficiency = m.conversions / m.spend if m.spend > 0 else 0
            scored.append((c, efficiency))

        if len(scored) < 2:
            return []

        scored.sort(key=lambda x: x[1])
        n = max(1, len(scored) // 4)
        bottom = scored[:n]
        top = scored[-n:]

        changes: list[tuple[Campaign, float, float]] = []
        total_freed = 0.0

        for c, _ in bottom:
            freed = c.daily_budget * self._realloc_pct
            new_budget = max(self._min_budget, c.daily_budget - freed)
            actual_freed = c.daily_budget - new_budget
            if actual_freed > 0.01:
                total_freed += actual_freed
                changes.append((c, c.daily_budget, new_budget))

        if total_freed < 0.01:
            return []

        bonus_each = total_freed / len(top)
        for c, _ in top:
            old = c.daily_budget
            new = old + bonus_each
            changes.append((c, old, new))

        return changes


# ── A/B Tester ──


class ABTester:
    """Chi-squared test for comparing two ad variants."""

    def __init__(self, settings: Settings):
        self._p_threshold = settings.ab_test_confidence
        self._min_samples = 100

    def evaluate(
        self, test: ABTest, metrics_a: MetricSnapshot, metrics_b: MetricSnapshot,
    ) -> ABTest:
        if test.status != ABTestStatus.RUNNING:
            return test

        if test.metric == "ctr":
            hits_a, total_a = metrics_a.clicks, metrics_a.impressions
            hits_b, total_b = metrics_b.clicks, metrics_b.impressions
        else:  # cr
            hits_a, total_a = metrics_a.conversions, metrics_a.clicks
            hits_b, total_b = metrics_b.conversions, metrics_b.clicks

        if total_a < self._min_samples or total_b < self._min_samples:
            return test

        p_value = self._chi_squared_2x2(hits_a, total_a, hits_b, total_b)
        if p_value > self._p_threshold:
            return test

        rate_a = hits_a / total_a if total_a else 0
        rate_b = hits_b / total_b if total_b else 0
        winner = test.variant_a_id if rate_a >= rate_b else test.variant_b_id

        test.status = ABTestStatus.COMPLETED
        test.winner_id = winner
        test.confidence = round(1.0 - p_value, 4)
        return test

    @staticmethod
    def _chi_squared_2x2(
        hits_a: int, total_a: int, hits_b: int, total_b: int,
    ) -> float:
        miss_a = total_a - hits_a
        miss_b = total_b - hits_b
        table = [[hits_a, miss_a], [hits_b, miss_b]]
        grand = total_a + total_b
        if grand == 0:
            return 1.0

        row_sums = [sum(r) for r in table]
        col_sums = [table[0][j] + table[1][j] for j in range(2)]

        chi2 = 0.0
        for i in range(2):
            for j in range(2):
                expected = row_sums[i] * col_sums[j] / grand
                if expected == 0:
                    continue
                chi2 += (table[i][j] - expected) ** 2 / expected

        # 1 degree of freedom → approximate p-value
        return _chi2_sf(chi2, 1)


def _chi2_sf(x: float, df: int) -> float:
    """Survival function for chi-squared (upper tail p-value), df=1."""
    if x <= 0:
        return 1.0
    if df == 1:
        return math.erfc(math.sqrt(x / 2) / math.sqrt(1))
    return math.exp(-x / 2)


# ── Rule Engine ──


@dataclass
class Alert:
    campaign_id: str
    message: str
    severity: str = "warning"  # warning | critical


class RuleEngine:
    def __init__(self, settings: Settings):
        self._max_cpc = settings.max_cpc

    def check(
        self,
        campaign: Campaign,
        current: MetricSnapshot,
        previous: MetricSnapshot | None,
    ) -> list[Alert | OptimizationAction]:
        results: list[Alert | OptimizationAction] = []

        if current.clicks > 0 and current.cpc > self._max_cpc:
            results.append(OptimizationAction(
                campaign_id=campaign.id,
                action_type=ActionType.PAUSE_CAMPAIGN,
                old_value=f"cpc={current.cpc:.2f}",
                new_value="paused",
                reason=f"CPC {current.cpc:.2f} exceeds max {self._max_cpc:.2f}",
            ))

        if (
            previous
            and previous.ctr > 0
            and current.ctr > 0
            and current.ctr < previous.ctr * 0.5
        ):
            results.append(Alert(
                campaign_id=campaign.id,
                message=f"CTR dropped >50%: {previous.ctr:.4f} → {current.ctr:.4f}",
                severity="critical",
            ))

        if current.impressions == 0 and campaign.status == CampaignStatus.ACTIVE:
            results.append(Alert(
                campaign_id=campaign.id,
                message="No impressions in this cycle",
                severity="warning",
            ))

        return results


# ── Orchestrator ──


class Optimizer:
    """Combines all optimization components."""

    def __init__(self, settings: Settings, db: Database, platform: AdPlatformClient):
        self.bid_optimizer = BidOptimizer(settings)
        self.budget_allocator = BudgetAllocator()
        self.ab_tester = ABTester(settings)
        self.rule_engine = RuleEngine(settings)
        self._db = db
        self._platform = platform
        self._prev_metrics: dict[str, MetricSnapshot] = {}

    def optimize_cycle(self) -> list[OptimizationAction]:
        actions: list[OptimizationAction] = []
        campaigns = self._db.list_campaigns(CampaignStatus.ACTIVE)
        if not campaigns:
            return actions

        current_metrics: dict[str, MetricSnapshot] = {}
        for c in campaigns:
            m = self._platform.fetch_metrics(c.id)
            self._db.save_metric(m)
            current_metrics[c.id] = m

            # update Thompson Sampling posteriors
            self.bid_optimizer.update(c.id, m.clicks, m.conversions)

        # 1. Rule checks
        for c in campaigns:
            m = current_metrics[c.id]
            results = self.rule_engine.check(c, m, self._prev_metrics.get(c.id))
            for r in results:
                if isinstance(r, OptimizationAction):
                    if r.action_type == ActionType.PAUSE_CAMPAIGN:
                        self._platform.pause_campaign(c.id)
                        c.status = CampaignStatus.PAUSED
                        self._db.save_campaign(c)
                    self._db.save_action(r)
                    actions.append(r)
                elif isinstance(r, Alert):
                    a = OptimizationAction(
                        campaign_id=r.campaign_id,
                        action_type=ActionType.ALERT,
                        reason=r.message,
                    )
                    self._db.save_action(a)
                    actions.append(a)

        # refresh active campaigns after rule engine may have paused some
        campaigns = self._db.list_campaigns(CampaignStatus.ACTIVE)

        # 2. Bid optimization
        for c in campaigns:
            new_bid = self.bid_optimizer.suggest_bid(c)
            if new_bid is not None:
                old_bid = c.bid
                c.bid = new_bid
                self._platform.update_campaign(c)
                self._db.save_campaign(c)
                a = OptimizationAction(
                    campaign_id=c.id,
                    action_type=ActionType.BID_CHANGE,
                    old_value=f"{old_bid:.3f}",
                    new_value=f"{new_bid:.3f}",
                    reason=f"Thompson Sampling adjustment",
                )
                self._db.save_action(a)
                actions.append(a)

        # 3. Budget reallocation
        total_metrics = {c.id: self._db.get_total_metrics(c.id) for c in campaigns}
        changes = self.budget_allocator.reallocate(campaigns, total_metrics)
        for c, old_budget, new_budget in changes:
            c.daily_budget = new_budget
            self._platform.update_campaign(c)
            self._db.save_campaign(c)
            a = OptimizationAction(
                campaign_id=c.id,
                action_type=ActionType.BUDGET_REALLOC,
                old_value=f"{old_budget:.2f}",
                new_value=f"{new_budget:.2f}",
                reason="Efficiency-based reallocation",
            )
            self._db.save_action(a)
            actions.append(a)

        # 4. A/B tests
        running_tests = self._db.list_ab_tests(ABTestStatus.RUNNING)
        for test in running_tests:
            m_a = self._db.get_ad_metrics(test.variant_a_id)
            m_b = self._db.get_ad_metrics(test.variant_b_id)
            updated = self.ab_tester.evaluate(test, m_a, m_b)
            if updated.status == ABTestStatus.COMPLETED:
                self._db.save_ab_test(updated)
                loser = (
                    test.variant_b_id
                    if updated.winner_id == test.variant_a_id
                    else test.variant_a_id
                )
                self._platform.pause_ad(loser)
                a = OptimizationAction(
                    campaign_id=test.campaign_id,
                    action_type=ActionType.AB_TEST_WINNER,
                    old_value=f"a={test.variant_a_id}, b={test.variant_b_id}",
                    new_value=f"winner={updated.winner_id}",
                    reason=f"A/B test completed, confidence={updated.confidence:.2%}",
                )
                self._db.save_action(a)
                actions.append(a)

        self._prev_metrics = current_metrics
        return actions
