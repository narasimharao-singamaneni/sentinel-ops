import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://localhost:5432/person_api"
    MAX_CONCURRENT: int = 10
    SEMAPHORE_TIMEOUT: float = 3.0
    DB_QUERY_TIMEOUT: float = 1.0
    SENTINEL_URL: str | None = None
    CACHE_TTL: int = 600
    DEFAULT_PAGE_SIZE: int = 20
    MAX_PAGE_SIZE: int = 100

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", case_sensitive=False)


settings = Settings()