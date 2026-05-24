"""Minimal pyarrow type stubs for basedpyright in client-reporting-api."""
from __future__ import annotations

class Schema: ...


class Table:
    schema: Schema

    def to_pylist(self) -> list[dict[str, object]]: ...

    @staticmethod
    def from_pylist(
        mapping: list[dict[str, object]],
        schema: Schema | None = ...,
    ) -> Table: ...
