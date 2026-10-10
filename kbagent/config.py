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
    agent_timeout: int = Field(default=120, ge=10, le=3600)
    wiki_max_steps: int = Field(default=80, ge=8, le=256)
    wiki_max_files: int = Field(default=12, ge=1, le=100)
    wiki_source_chars: int = Field(default=24000, ge=4000, le=100000)

    gitlab_url: str = ""
    gitlab_username: str = "oauth2"
    gitlab_token: SecretStr = SecretStr("")
    gitlab_ssl_ca: Path | None = None
    repository_local_roots: list[Path] = []
    repository_git_timeout: int = Field(default=120, ge=1, le=600)
    repository_max_files: int = Field(default=500, ge=1, le=2000)
    repository_max_file_bytes: int = Field(default=200000, ge=1, le=1000000)
    repository_max_total_bytes: int = Field(default=10000000, ge=1, le=50000000)

    @field_validator("repository_local_roots")
    @classmethod
    def valid_roots(cls, values):
        if any(not p.is_absolute() for p in values):
            raise ValueError("Repository local roots must be absolute paths")
        return values

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
