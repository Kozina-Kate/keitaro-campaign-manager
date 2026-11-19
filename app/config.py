from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./app.db"
    keitaro_base_url: HttpUrl = "https://your-tracker.example"
    keitaro_api_key: SecretStr = SecretStr("")
    keitaro_timeout_seconds: float = Field(default=20, gt=0, le=120)

    @field_validator("keitaro_base_url")
    @classmethod
    def validate_origin(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("Keitaro должен использовать HTTPS")
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("Укажите адрес без пароля, параметров и фрагмента")
        if value.path not in (None, "", "/"):
            raise ValueError("Укажите корень трекера без /admin")
        return value
