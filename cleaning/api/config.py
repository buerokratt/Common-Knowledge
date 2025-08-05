from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    ruuter_internal: str
    languages: list[str] = ['est', 'rus', 'eng']


settings = Settings()
