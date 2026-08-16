"""
Central place for all environment configuration.
Import `settings` anywhere you need a config value instead of calling
os.environ directly, so there is exactly one source of truth.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_db_password: str = ""

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"

    indian_api_key: str = ""
    indian_api_base_url: str = "https://stock.indianapi.in"

    backtest_years_ago: int = 3
    cors_allow_origin: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
