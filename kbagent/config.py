from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KBAGENT_", env_file=".env", extra="ignore")
    mysql_host: str = "127.0.0.1"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "kbagent"
    mysql_user: str = "kbagent"
    mysql_password: SecretStr = SecretStr("")
    mysql_ssl_ca: Path | None = None
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: SecretStr = SecretStr("")
    minio_secret_key: SecretStr = SecretStr("")
    minio_bucket: str = "kbagent"
    minio_secure: bool = False
    minio_region: str = "us-east-1"
    minio_create_bucket: bool = True
    port: int = Field(default=8000, ge=1, le=65535)
    api_key: str = ""
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    max_upload_bytes: int = 10 * 1024 * 1024
    agent_timeout: int = 120

    @field_validator("mysql_database")
    @classmethod
    def valid_database(cls, value):
        import re

        if not re.fullmatch(r"[A-Za-z0-9_]{1,64}", value):
            raise ValueError("MySQL database must contain 1–64 letters, digits or underscores")
        return value

    @field_validator("minio_endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        if "://" in value or "/" in value:
            raise ValueError("MinIO endpoint must be host:port without scheme or path")
        return value

    @property
    def agent_enabled(self) -> bool:
        return bool(self.api_key and self.model)
