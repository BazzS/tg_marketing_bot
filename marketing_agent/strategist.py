"""LLM-based strategy advisor. Analyzes trends and suggests high-level changes.
Gracefully skips if no API key is configured."""

from __future__ import annotations

import json
import logging

import httpx

from .config import Settings
from .db import Database

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Ты — опытный performance-маркетолог, управляющий рекламными кампаниями в Telegram.
Анализируй метрики и историю оптимизаций, давай конкретные рекомендации.

Отвечай строго JSON:
{
  "assessment": "краткая оценка текущей ситуации (2-3 предложения)",
  "recommendations": [
    {"action": "описание действия", "priority": "high|medium|low", "reason": "почему"}
  ],
  "trend": "improving|stable|declining",
  "risk_factors": ["фактор1", "фактор2"]
}
"""


class Strategist:
    def __init__(self, settings: Settings):
        self._api_key = settings.openai_api_key
        self._model = settings.strategist_model
        self._enabled = bool(self._api_key)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def analyze(self, db: Database) -> dict | None:
        if not self._enabled:
            log.info("Strategist disabled (no API key)")
            return None

        summary = self._build_summary(db)
        try:
            response = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "max_tokens": 1024,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": summary},
                    ],
                },
                timeout=30,
            )
            response.raise_for_status()
            text = response.json()["choices"][0]["message"]["content"]
            return json.loads(text)
        except Exception as e:
            log.error("Strategist error: %s", e)
            return None

    def _build_summary(self, db: Database) -> str:
        campaigns = db.list_campaigns()
        lines = ["# Текущие кампании\n"]

        for c in campaigns:
            total = db.get_total_metrics(c.id)
            recent = db.get_metrics(c.id, last_n=5)
            lines.append(f"## {c.name} [{c.status.value}]")
            lines.append(f"  bid={c.bid:.3f}, budget={c.daily_budget:.2f}")
            lines.append(
                f"  TOTAL: impr={total.impressions}, clicks={total.clicks}, "
                f"conv={total.conversions}, spend={total.spend:.2f}"
            )
            if total.clicks:
                lines.append(
                    f"  CTR={total.ctr:.4f}, CR={total.cr:.4f}, "
                    f"CPC={total.cpc:.2f}, CPL={total.cpl:.2f}"
                )
            if recent:
                last = recent[-1]
                lines.append(
                    f"  LAST CYCLE: impr={last.impressions}, clicks={last.clicks}, "
                    f"conv={last.conversions}, spend={last.spend:.2f}"
                )
            lines.append("")

        actions = db.list_actions(last_n=20)
        if actions:
            lines.append("# Последние действия оптимизатора\n")
            for a in actions[-10:]:
                lines.append(
                    f"  [{a.action_type.value}] campaign={a.campaign_id}: "
                    f"{a.reason} ({a.old_value} → {a.new_value})"
                )

        return "\n".join(lines)
