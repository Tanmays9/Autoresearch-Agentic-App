from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Atlas Research"
    database_url: str = "sqlite:///./data/research.db"
    brave_search_api_key: str = ""
    local_token_file: str = ".local/token"
    enable_embeddings: bool = True
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    cors_origins: str = "http://127.0.0.1:3000,http://localhost:3000"
    task_lease_seconds: int = 900
    runner_stale_seconds: int = 90
    max_source_bytes: int = 10 * 1024 * 1024
    max_execution_log_bytes: int = 4 * 1024 * 1024
    crawl_deadline_minutes: int = 30
    crawl_domain_page_limit: int = 20
    crawl_max_depth: int = 2
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    azure_openai_api_version: str = "2025-04-01-preview"
    # Single-model policy: every local LangGraph role uses gpt-5.6-sol.
    azure_research_deployment: str = "gpt-5.6-sol"
    azure_reasoning_deployment: str = "gpt-5.6-sol"
    langgraph_checkpoint_path: str = "/app/data/langgraph-checkpoints.db"
    inhouse_agent_concurrency: int = 5
    inhouse_tool_rounds: int = 6
    inhouse_run_deadline_minutes: int = 90
    documentation_experiment_budget: int = 12
    azure_token_budget: int = 1_000_000
    azure_cost_budget_usd: float = 50.0
    azure_research_cost_per_million_tokens: float = 10.0
    azure_reasoning_cost_per_million_tokens: float = 10.0

    @property
    def azure_ready(self) -> bool:
        return bool(self.azure_openai_endpoint and self.azure_openai_api_key)

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]

    def local_token(self) -> str:
        path = Path(self.local_token_file)
        if not path.exists():
            return "development-token-change-me"
        return path.read_text(encoding="utf-8").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
