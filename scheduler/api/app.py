import pytz

from fastapi import FastAPI
from croniter import croniter
from api.models import SchedulerEntity, NextTimeToRun

from datetime import datetime

app = FastAPI()


@app.post("/render_next_run")
def render_next_run(scheduler_entity: SchedulerEntity) -> NextTimeToRun:
    tz = pytz.timezone(scheduler_entity.timezone)
    now = datetime.now(tz=tz)
    c = croniter(scheduler_entity.cron_expression, now)
    rendered_next_time = datetime.fromtimestamp(c.get_next(), tz=tz)
    return NextTimeToRun(timestamp=rendered_next_time)
