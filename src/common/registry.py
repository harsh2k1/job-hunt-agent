from collections.abc import Callable
from typing import Generic, TypeVar

RegisteredT = TypeVar("RegisteredT")


class UnknownRegistryEntryError(LookupError):
    pass


class Registry(Generic[RegisteredT]):
    """Name -> implementation lookup behind every factory in the project.

    Implementations add themselves with the `register` decorator, so a new
    database backend, cache backend or job source is one new class and no
    edits anywhere else.
    """

    def __init__(self, kind: str):
        # what is being registered, e.g. "database backend"; only used in errors
        self._kind = kind
        self._entries: dict[str, RegisteredT] = {}

    def register(self, name: str) -> Callable[[RegisteredT], RegisteredT]:
        def add_entry(entry: RegisteredT) -> RegisteredT:
            if name in self._entries:
                raise ValueError(f"{self._kind} {name!r} is already registered")
            self._entries[name] = entry
            return entry

        return add_entry

    def get(self, name: str) -> RegisteredT:
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownRegistryEntryError(
                f"unknown {self._kind} {name!r}; registered: {', '.join(self.names()) or 'none'}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._entries)
