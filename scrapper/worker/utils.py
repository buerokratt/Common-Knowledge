import functools
from typing import Type

from pydantic import BaseModel


def un_json(class_object: Type[BaseModel]):
    def decorator(f):
        @functools.wraps(f)
        def wrapper(task):
            task_model = class_object.model_validate(task)
            return f(task_model)
        return wrapper
    return decorator
