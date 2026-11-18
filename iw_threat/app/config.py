from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_base_url: str = "http://localhost:8000/v1"
    llm_served_name: str = "gpt-oss"
    llm_timeout_s: float = 180.0

    embed_model: str = "intfloat/multilingual-e5-large-instruct"
    embed_dim: int = 1024

    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "chunks"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change_me_neo4j"

    pg_dsn: str = "postgresql://iw:change_me@localhost:5432/iw"

    indicators_path: str = "config/indicators.yaml"
    grading_path: str = "config/source_grading.yaml"

    # 변화 분석 창
    window_days: int = 7
    baseline_days: int = 30
    # 텍스트 첩보 검색 범위
    report_lookback_days: int = 30
    report_top_k: int = 20
    doctrine_top_k: int = 6


@lru_cache
def get_settings() -> Settings:
    return Settings()
