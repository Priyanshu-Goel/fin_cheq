"""
Central place for all environment configuration.
Import `settings` anywhere you need a config value instead of calling
os.environ directly, so there is exactly one source of truth.
"""
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_db_password: str = ""

    cometapi_key: str = ""
    cometapi_base_url: str = "https://api.cometapi.com/v1"
    cometapi_model: str = "claude-haiku-4-5-20251001"

    indian_api_key: str = ""
    indian_api_base_url: str = "https://stock.indianapi.in"

    hf_api_token: str = ""

    backtest_years_ago: int = 3
    cors_allow_origin: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_whitespace(cls, value):
        """
        A leading/trailing space or newline pasted into a hosting provider's
        environment-variable UI is invisible in the dashboard but breaks an
        API key comparison outright - the key "looks" identical everywhere
        you'd check it, yet gets rejected. Stripping every string field
        here means that class of mistake can't silently break auth again,
        regardless of which secret it happens to.
        """
        return value.strip() if isinstance(value, str) else value


settings = Settings()
