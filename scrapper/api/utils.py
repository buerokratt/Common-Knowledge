import os

from api.models import BaseObject


def get_path_for_task(task: BaseObject):
    agency_str = task.agency_id
    source_str = task.source_id

    return os.path.join(agency_str, source_str)
