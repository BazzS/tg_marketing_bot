#!/usr/bin/env python3
"""Demo: creates campaigns with different creatives and runs the optimization loop."""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from marketing_agent.agent import MarketingAgent
from marketing_agent.config import Settings


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings()
    agent = MarketingAgent(settings)

    print("Кампании:")
    c1 = agent.create_campaign(
        "IT-курсы Python",
        daily_budget=50.0, bid=2.0,
        audience="developers", geo="RU,UA,BY", age="18-35",
    )
    ad1a = agent.add_ad(c1.id,
        "Стань Python-разработчиком за 3 месяца! Первый урок бесплатно →",
        url="https://example.com/python-course",
    )
    ad1b = agent.add_ad(c1.id,
        "Python с нуля до Middle — 12 недель. Гарантия трудоустройства.",
        url="https://example.com/python-course",
    )
    agent.start_ab_test(c1.id, ad1a.id, ad1b.id, metric="ctr")

    c2 = agent.create_campaign(
        "CRM для малого бизнеса",
        daily_budget=100.0, bid=4.0,
        audience="business_owners", geo="RU", company_size="1-50",
    )
    agent.add_ad(c2.id,
        "CRM, которая сама напомнит о follow-up. 14 дней бесплатно.",
        url="https://example.com/crm",
    )

    c3 = agent.create_campaign(
        "Гайд по инвестициям",
        daily_budget=20.0, bid=0.8,
        audience="finance_interested", geo="RU,KZ", age="25-45",
    )
    agent.add_ad(c3.id,
        "Как начать инвестировать с 10 000 руб. Скачай PDF бесплатно.",
        url="https://example.com/invest-guide",
    )

    llm_status = "включён" if agent.strategist.enabled else "выключен (нет API ключа)"
    print(f"\nЗапуск: {settings.demo_cycles} циклов  |  LLM-стратег: {llm_status}")
    print("─" * 50)

    report = agent.run(settings.demo_cycles)

    print()
    print(report)

    agent.close()


if __name__ == "__main__":
    main()
