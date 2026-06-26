from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    openai_api_key: str = ""
    strategist_model: str = "gpt-4o-mini"

    max_cpc: float = 5.0
    max_bid: float = 10.0
    min_bid: float = 0.1
    ab_test_confidence: float = 0.05
    optimization_interval_cycles: int = 1
    strategy_interval_cycles: int = 5

    demo_cycles: int = 20
    db_path: str = "data/marketing.db"
