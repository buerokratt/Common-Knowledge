from pydantic_settings import BaseSettings
from pydantic import DirectoryPath


class Settings(BaseSettings):
    source_path: DirectoryPath = '/source'
    aws_access_key_id: str
    aws_secret_access_key: str
    aws_region: str = 'us-east-1'
    s3_bucket_name: str
    s3_presigned_url_expiration: int = 3600

    class Config:
        env_file = ".env"


settings = Settings() 