# core/config.py
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    """Настройки приложения."""

    # PostgreSQL
    postgres_host: str = os.getenv("DB_HOST", "localhost")
    postgres_port: str = os.getenv("DB_PORT", "5432")
    postgres_db: str = os.getenv("DB_NAME", "movies_database")
    postgres_user: str = os.getenv("DB_USER", "app")
    postgres_password: str = os.getenv("DB_PASSWORD", "123qwe")
    sleep_time_seconds: int = os.getenv('SLEEP_TIME', 60)

    @property
    def dsn(self) -> str:
        """DSN для подключения к PostgreSQL."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # Elasticsearch
    elastic_host: str = os.getenv("ELASTIC_HOST", "http://localhost:9200")

    # ETL настройки
    batch_size: int = int(os.getenv("BATCH_SIZE", "100"))
    poll_interval: int = int(os.getenv("POLL_INTERVAL", "10"))
    sleep_time: int = int(os.getenv("SLEEP_TIME", "1"))
    state_file: str = os.getenv("STATE_FILE", str(BASE_DIR / "state" / "state.json"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    # Сколько лет после выхода фильм доступен только по подписке (см. access.py)
    subscription_period_years: int = int(os.getenv("SUBSCRIPTION_PERIOD_YEARS", "3"))


@dataclass
class ETLConfig:
    """Класс описывающий параметры ETL для отдельной сущности."""
    query: str
    index_schema: dict
    state_key: str
    elastic_index_name: str
    related_model: Callable
    batch_size: int = 100
    limit_size: int = 5000


settings = Settings()
