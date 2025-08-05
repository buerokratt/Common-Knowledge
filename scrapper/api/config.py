from pydantic_settings import BaseSettings
from pydantic import AnyUrl


class Settings(BaseSettings):
    broker_url: AnyUrl


settings = Settings()
