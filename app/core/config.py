from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "mpesa-processor"
    env: str = "local"
    log_level: str = "INFO"

    # AWS (Frankfurt, same as the rest of CelliPay)
    aws_region: str = "eu-central-1"
    statements_bucket: str = ""
    statements_queue_url: str = ""

    # MariaDB. Use the restricted user: SELECT on core tables, full rights on stmt_* only.
    database_url: str = "mysql+pymysql://stmt_processor:change-me@127.0.0.1:3306/cellipay?charset=utf8mb4"
    db_pool_size: int = 5

    # Scoring knobs. Changing these is a config change, not a deploy.
    affordability_ratio: Decimal = Decimal("0.900")
    short_window_days: int = 30
    very_short_window_days: int = 14
    # Which devices a customer is scored against: REGION = every AVAILABLE device in the
    # agent's shop's region, SHOP = only the agent's shop.
    device_pool_scope: str = "REGION"

    # Matching. Generate the key once: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    code_encryption_key: str = ""
    request_ttl_minutes: int = 30
    email_lookback_minutes: int = 10     # emails that landed up to 10 min before the request was opened still count
    max_code_attempts: int = 5
    unclaimed_email_ttl_hours: int = 6

    # Nest -> Python auth. Long random string, same value in Nest's env.
    internal_api_token: str = ""
    statement_inbox: str = "mpesa@statements.cellipay.co.ke"
    reuse_assessment_days: int = 30

    def assessment_config(self):
        from app.scoring.assessment import AssessmentConfig
        return AssessmentConfig(affordability_ratio=self.affordability_ratio,
                                short_window_days=self.short_window_days,
                                very_short_window_days=self.very_short_window_days)


@lru_cache
def get_settings() -> Settings:
    return Settings()