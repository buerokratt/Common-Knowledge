import functools
from collections.abc import Callable

from pydantic import BaseModel


def un_json(class_object: type[BaseModel]) -> Callable:
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def wrapper(task: dict) -> object:
            task_model = class_object.model_validate(task)
            return f(task_model)

        return wrapper

    return decorator
