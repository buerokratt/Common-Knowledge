from pathlib import Path

from pydantic import DirectoryPath
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    source_path: DirectoryPath = Path("/source")
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = "us-east-1"
    s3_bucket_name: str
    s3_endpoint_url: str
    s3_presigned_url_expiration: int = 3600

    class Config:
        env_file = ".env"


settings = Settings()  # type: ignore[call-arg]
