from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KBAGENT_", env_file=".env", extra="ignore")
    data_dir: Path = Path("data")
    api_key: str = ""
    model: str = ""
    base_url: str = "https://api.openai.com/v1"
    max_upload_bytes: int = 10 * 1024 * 1024
    agent_timeout: int = 120

    @property
    def agent_enabled(self) -> bool:
        return bool(self.api_key and self.model)
