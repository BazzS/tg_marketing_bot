"""Text-based reports with sparklines."""

from __future__ import annotations

from .db import Database
from .models import ActionType, CampaignStatus, OptimizationAction

_SPARK_CHARS = "▁▂▃▄▅▆▇█"

_ACTION_LABELS = {
    ActionType.BID_CHANGE:      ("💰", "ставка"),
    ActionType.BUDGET_REALLOC:  ("📊", "бюджет"),
    ActionType.PAUSE_CAMPAIGN:  ("⏸ ", "пауза"),
    ActionType.RESUME_CAMPAIGN: ("▶ ", "возобновлена"),
    ActionType.PAUSE_AD:        ("⏸ ", "объявление на паузе"),
    ActionType.AB_TEST_WINNER:  ("🏆", "A/B"),
    ActionType.STRATEGY_NOTE:   ("🧠", "стратегия"),
    ActionType.ALERT:           ("⚠️ ", ""),
}


def sparkline(values: list[float]) -> str:
    if not values:
        return ""
    lo, hi = min(values), max(values)
    rng = hi - lo if hi != lo else 1.0
    return "".join(
        _SPARK_CHARS[min(len(_SPARK_CHARS) - 1, int((v - lo) / rng * (len(_SPARK_CHARS) - 1)))]
        for v in values
    )


def campaign_report(db: Database, campaign_id: str) -> str:
    c = db.get_campaign(campaign_id)
    if not c:
        return f"Campaign {campaign_id} not found"

    total = db.get_total_metrics(campaign_id)
    recent = db.get_metrics(campaign_id, last_n=20)
    status_label = "активна" if c.status == CampaignStatus.ACTIVE else "на паузе"
    lines = [
        f"  {c.name}  [{status_label}]  ставка ${c.bid:.2f}  бюджет ${c.daily_budget:.0f}/день",
        f"    показы {total.impressions:>8,}   клики {total.clicks:>6,}  CTR {total.ctr:.2%}",
        f"    конверсии {total.conversions:>5,}                  CR  {total.cr:.2%}",
        f"    потрачено ${total.spend:>8,.2f}   CPC ${total.cpc:.2f}   CPL ${total.cpl:.2f}",
    ]

    if recent:
        impr_vals = [float(m.impressions) for m in recent]
        click_vals = [float(m.clicks) for m in recent]
        spend_vals = [m.spend for m in recent]
        lines.extend([
            f"    тренд показов:  {sparkline(impr_vals)}",
            f"    тренд кликов:   {sparkline(click_vals)}",
            f"    тренд расходов: {sparkline(spend_vals)}",
        ])

    return "\n".join(lines)


def format_action(a: OptimizationAction, name_map: dict[str, str] | None = None) -> str:
    icon, label = _ACTION_LABELS.get(a.action_type, ("•", ""))
    name = (name_map or {}).get(a.campaign_id or "", a.campaign_id or "—")
    name_col = f"{name:<28}"

    t = a.action_type
    old, new = a.old_value, a.new_value

    if t == ActionType.BID_CHANGE:
        try:
            return f"  {icon} {name_col} {label} ${float(old):.2f} → ${float(new):.2f}"
        except ValueError:
            pass

    if t == ActionType.BUDGET_REALLOC:
        try:
            return f"  {icon} {name_col} {label} ${float(old):.0f} → ${float(new):.0f}"
        except ValueError:
            pass

    if t in (ActionType.PAUSE_CAMPAIGN, ActionType.ALERT):
        return f"  {icon} {name_col} {a.reason}"

    if t == ActionType.STRATEGY_NOTE:
        return f"  {icon} {a.reason}"

    return f"  {icon} {name_col} {label} {a.reason}"


def full_report(db: Database) -> str:
    campaigns = db.list_campaigns()
    if not campaigns:
        return "Кампании не найдены."

    name_map = {c.id: c.name for c in campaigns}

    total_spend = 0.0
    total_conv = 0
    total_clicks = 0
    total_impr = 0

    parts = ["─── Итоги по кампаниям ───────────────────────────", ""]
    for c in campaigns:
        parts.append(campaign_report(db, c.id))
        parts.append("")
        m = db.get_total_metrics(c.id)
        total_spend += m.spend
        total_conv += m.conversions
        total_clicks += m.clicks
        total_impr += m.impressions

    avg_cpc = total_spend / total_clicks if total_clicks else 0
    avg_cpl = total_spend / total_conv if total_conv else 0
    active = sum(1 for c in campaigns if c.status == CampaignStatus.ACTIVE)

    parts.extend([
        "─── Сводка ───────────────────────────────────────",
        f"  Кампании: {len(campaigns)}  (активных: {active})",
        f"  Показы:    {total_impr:>10,}",
        f"  Клики:     {total_clicks:>10,}",
        f"  Конверсии: {total_conv:>10,}",
        f"  Потрачено: {total_spend:>10,.2f}",
        f"  CPC ср.:   ${avg_cpc:.2f}",
        f"  CPL ср.:   ${avg_cpl:.2f}",
    ])

    actions = db.list_actions(last_n=10)
    if actions:
        parts.extend(["", "─── Последние действия ───────────────────────────"])
        for a in actions:
            parts.append(format_action(a, name_map))

    return "\n".join(parts)
