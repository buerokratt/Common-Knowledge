import functools
from collections.abc import Callable
from typing import Type

from pydantic import BaseModel


def un_json(class_object: Type[BaseModel]) -> Callable[..., Callable[..., None]]:
    def decorator(f: Callable[..., None]) -> Callable[..., None]:
        @functools.wraps(f)
        def wrapper(task: object) -> None:
            task_model = class_object.model_validate(task)
            return f(task_model)

        return wrapper

    return decorator
