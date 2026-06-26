import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from marketing_agent.config import Settings
from marketing_agent.db import Database
from marketing_agent.models import (
    ABTest,
    ABTestStatus,
    Ad,
    Campaign,
    CampaignStatus,
    MetricSnapshot,
)
from marketing_agent.optimizer import ABTester, Alert, BidOptimizer, BudgetAllocator, RuleEngine
from marketing_agent.platform_mock import MockAdPlatform


def _settings(**kw) -> Settings:
    return Settings(**{"db_path": ":memory:", **kw})


class TestBidOptimizer:
    def test_no_suggestion_without_data(self):
        opt = BidOptimizer(_settings())
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        assert opt.suggest_bid(c) is None

    def test_suggest_after_updates(self):
        opt = BidOptimizer(_settings())
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        for _ in range(20):
            opt.update(c.id, clicks=100, conversions=10)
        bid = opt.suggest_bid(c)
        assert bid is None or 0.1 <= bid <= 10.0

    def test_respects_bounds(self):
        opt = BidOptimizer(_settings(min_bid=1.0, max_bid=3.0))
        c = Campaign(name="test", daily_budget=50, bid=2.5)
        for _ in range(50):
            opt.update(c.id, clicks=100, conversions=1)
        bid = opt.suggest_bid(c)
        if bid is not None:
            assert 1.0 <= bid <= 3.0


class TestBudgetAllocator:
    def test_no_changes_with_one_campaign(self):
        alloc = BudgetAllocator()
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        m = {c.id: MetricSnapshot(campaign_id=c.id, spend=10, conversions=5)}
        assert alloc.reallocate([c], m) == []

    def test_redistributes_from_bad_to_good(self):
        alloc = BudgetAllocator(realloc_pct=0.2, min_budget=5.0)
        campaigns = [
            Campaign(id=f"c{i}", name=f"Campaign {i}", daily_budget=50, bid=2.0)
            for i in range(4)
        ]
        metrics = {
            "c0": MetricSnapshot(campaign_id="c0", spend=50, conversions=1),
            "c1": MetricSnapshot(campaign_id="c1", spend=50, conversions=2),
            "c2": MetricSnapshot(campaign_id="c2", spend=50, conversions=8),
            "c3": MetricSnapshot(campaign_id="c3", spend=50, conversions=15),
        }
        changes = alloc.reallocate(campaigns, metrics)
        assert len(changes) > 0
        ids_changed = {c.id for c, _, _ in changes}
        assert "c0" in ids_changed  # worst should lose budget
        assert "c3" in ids_changed  # best should gain budget


class TestABTester:
    def test_needs_min_samples(self):
        tester = ABTester(_settings())
        test = ABTest(
            campaign_id="c1", variant_a_id="a1", variant_b_id="a2",
        )
        m_a = MetricSnapshot(campaign_id="c1", ad_id="a1", impressions=50, clicks=5)
        m_b = MetricSnapshot(campaign_id="c1", ad_id="a2", impressions=50, clicks=2)
        result = tester.evaluate(test, m_a, m_b)
        assert result.status == ABTestStatus.RUNNING

    def test_detects_winner_with_large_difference(self):
        tester = ABTester(_settings(ab_test_confidence=0.05))
        test = ABTest(
            campaign_id="c1", variant_a_id="a1", variant_b_id="a2",
        )
        m_a = MetricSnapshot(
            campaign_id="c1", ad_id="a1", impressions=1000, clicks=100,
        )
        m_b = MetricSnapshot(
            campaign_id="c1", ad_id="a2", impressions=1000, clicks=30,
        )
        result = tester.evaluate(test, m_a, m_b)
        assert result.status == ABTestStatus.COMPLETED
        assert result.winner_id == "a1"
        assert result.confidence is not None and result.confidence > 0.95


class TestRuleEngine:
    def test_pauses_on_high_cpc(self):
        engine = RuleEngine(_settings(max_cpc=3.0))
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        m = MetricSnapshot(campaign_id=c.id, clicks=10, spend=50)  # CPC=5.0
        results = engine.check(c, m, None)
        assert any(
            hasattr(r, "action_type") and r.action_type.value == "pause_campaign"
            for r in results
        )

    def test_alerts_on_ctr_drop(self):
        engine = RuleEngine(_settings())
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        prev = MetricSnapshot(campaign_id=c.id, impressions=1000, clicks=50)
        curr = MetricSnapshot(campaign_id=c.id, impressions=1000, clicks=20)
        results = engine.check(c, curr, prev)
        assert any(isinstance(r, Alert) for r in results)


class TestMockPlatform:
    def test_generates_metrics(self):
        platform = MockAdPlatform(seed=42)
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        platform.create_campaign(c)
        ad = Ad(campaign_id=c.id, text="Test ad")
        platform.create_ad(ad)
        platform.advance_cycle()
        m = platform.fetch_metrics(c.id)
        assert m.impressions > 0
        assert m.spend >= 0

    def test_paused_campaign_no_metrics(self):
        platform = MockAdPlatform(seed=42)
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        platform.create_campaign(c)
        ad = Ad(campaign_id=c.id, text="Test ad")
        platform.create_ad(ad)
        platform.pause_campaign(c.id)
        platform.advance_cycle()
        m = platform.fetch_metrics(c.id)
        assert m.impressions == 0


class TestDatabase:
    def test_campaign_crud(self):
        db = Database(":memory:")
        c = Campaign(name="test", daily_budget=50, bid=2.0)
        db.save_campaign(c)
        loaded = db.get_campaign(c.id)
        assert loaded is not None
        assert loaded.name == "test"
        db.close()

    def test_metrics_storage(self):
        db = Database(":memory:")
        m = MetricSnapshot(
            campaign_id="c1", impressions=100, clicks=10, conversions=2, spend=5.0,
        )
        db.save_metric(m)
        loaded = db.get_metrics("c1")
        assert len(loaded) == 1
        assert loaded[0].impressions == 100
        db.close()
