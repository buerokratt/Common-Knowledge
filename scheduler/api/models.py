from datetime import datetime
from pydantic import BaseModel


class SchedulerEntity(BaseModel):
    cron_expression: str
    timezone: str = 'Europe/Tallinn'


class NextTimeToRun(BaseModel):
    timestamp: datetime