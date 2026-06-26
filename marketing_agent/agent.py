"""MarketingAgent — main orchestrator that ties everything together."""

from __future__ import annotations

import logging

from .config import Settings
from .db import Database
from .models import (
    ABTest,
    ActionType,
    Ad,
    Campaign,
    CampaignStatus,
    OptimizationAction,
)
from .optimizer import Optimizer
from .platform_base import AdPlatformClient
from .platform_mock import MockAdPlatform
from .reports import full_report
from .strategist import Strategist

log = logging.getLogger(__name__)

_ICONS = {
    ActionType.BID_CHANGE:      "💰",
    ActionType.BUDGET_REALLOC:  "📊",
    ActionType.PAUSE_CAMPAIGN:  "⏸ ",
    ActionType.RESUME_CAMPAIGN: "▶ ",
    ActionType.PAUSE_AD:        "⏸ ",
    ActionType.AB_TEST_WINNER:  "🏆",
    ActionType.STRATEGY_NOTE:   "🧠",
    ActionType.ALERT:           "⚠️ ",
}


def _fmt(action: OptimizationAction, name_map: dict[str, str]) -> str:
    icon = _ICONS.get(action.action_type, "•")
    name = name_map.get(action.campaign_id or "", "—")
    name_col = f"{name:<28}"

    t = action.action_type
    old, new = action.old_value, action.new_value

    if t == ActionType.BID_CHANGE:
        try:
            return f"  {icon} {name_col} ставка ${float(old):.2f} → ${float(new):.2f}"
        except ValueError:
            pass

    if t == ActionType.BUDGET_REALLOC:
        try:
            return f"  {icon} {name_col} бюджет ${float(old):.0f} → ${float(new):.0f}"
        except ValueError:
            pass

    if t == ActionType.PAUSE_CAMPAIGN:
        return f"  {icon} {name_col} ПАУЗА  {action.reason}"

    if t == ActionType.ALERT:
        return f"  {icon} {name_col} {action.reason}"

    if t == ActionType.AB_TEST_WINNER:
        return f"  {icon} {name_col} A/B завершён  {action.reason}"

    if t == ActionType.STRATEGY_NOTE:
        return f"  {icon} стратегия: {action.reason}"

    return f"  {icon} {name_col} {action.reason}"


class MarketingAgent:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings()
        self.db = Database(self.settings.db_path)
        self.platform: AdPlatformClient = MockAdPlatform(seed=42)
        self.optimizer = Optimizer(self.settings, self.db, self.platform)
        self.strategist = Strategist(self.settings)
        self._cycle = 0

    def create_campaign(
        self, name: str, daily_budget: float, bid: float, **targeting
    ) -> Campaign:
        c = Campaign(name=name, daily_budget=daily_budget, bid=bid, targeting=targeting)
        self.platform.create_campaign(c)
        self.db.save_campaign(c)
        log.info("  + %-28s  бюджет $%g/день  ставка $%.2f", name, daily_budget, bid)
        return c

    def add_ad(self, campaign_id: str, text: str, url: str = "") -> Ad:
        ad = Ad(campaign_id=campaign_id, text=text, url=url)
        self.platform.create_ad(ad)
        self.db.save_ad(ad)
        return ad

    def start_ab_test(
        self, campaign_id: str, ad_a_id: str, ad_b_id: str, metric: str = "ctr",
    ) -> ABTest:
        test = ABTest(
            campaign_id=campaign_id,
            variant_a_id=ad_a_id,
            variant_b_id=ad_b_id,
            metric=metric,
        )
        self.db.save_ab_test(test)
        c = self.db.get_campaign(campaign_id)
        name = c.name if c else campaign_id
        log.info("  ↔ A/B тест → %s  (по %s)", name, metric.upper())
        return test

    def run_cycle(self) -> list[OptimizationAction]:
        self._cycle += 1
        if isinstance(self.platform, MockAdPlatform):
            self.platform.advance_cycle()

        hour = self.platform.current_hour if isinstance(self.platform, MockAdPlatform) else 0
        log.info("\nЦикл %d  [%02d:00]", self._cycle, hour)

        actions = self.optimizer.optimize_cycle()

        name_map = {c.id: c.name for c in self.db.list_campaigns()}
        for a in actions:
            log.info("%s", _fmt(a, name_map))

        if (
            self._cycle % self.settings.strategy_interval_cycles == 0
            and self.strategist.enabled
        ):
            actions.extend(self._run_strategy(name_map))

        return actions

    def _run_strategy(self, name_map: dict[str, str]) -> list[OptimizationAction]:
        log.info("  🧠 запрос к LLM-стратегу...")
        result = self.strategist.analyze(self.db)
        if not result:
            return []

        actions = []
        trend_label = {"improving": "растёт", "stable": "стабильно", "declining": "падает"}.get(
            result.get("trend", ""), result.get("trend", "")
        )
        log.info("  🧠 тренд: %s — %s", trend_label, result.get("assessment", ""))

        for rec in result.get("recommendations", []):
            priority = {"high": "!", "medium": "~", "low": " "}.get(rec.get("priority", ""), "")
            log.info("     %s %s", priority, rec.get("action", ""))

        a = OptimizationAction(
            action_type=ActionType.STRATEGY_NOTE,
            reason=f"[{result.get('trend')}] {result.get('assessment', '')}",
        )
        self.db.save_action(a)
        actions.append(a)
        return actions

    def run(self, cycles: int | None = None) -> str:
        n = cycles or self.settings.demo_cycles
        for _ in range(n):
            self.run_cycle()
        return full_report(self.db)

    def close(self):
        self.db.close()
