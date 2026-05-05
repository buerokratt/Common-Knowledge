from pathlib import Path

from pydantic import DirectoryPath, PostgresDsn
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    db_uri: PostgresDsn
    dsl_path: DirectoryPath = Path("/DSL")
    export_path: DirectoryPath = Path("/exported-data")


settings = Settings()  # type: ignore[call-arg]
