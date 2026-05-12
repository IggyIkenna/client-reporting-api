"""Minimal pyarrow.parquet type stubs for basedpyright in client-reporting-api."""
from __future__ import annotations

from pathlib import Path
from typing import IO

import pyarrow


def read_table(
    source: str | Path | IO[bytes],
    *,
    columns: list[str] | None = ...,
    use_threads: bool = ...,
    metadata: object = ...,
    schema: pyarrow.Schema | None = ...,
    use_pandas_metadata: bool = ...,
    read_dictionary: list[str] | None = ...,
    memory_map: bool = ...,
    buffer_size: int = ...,
    partitioning: object = ...,
    filesystem: object = ...,
    filters: object = ...,
    use_legacy_dataset: bool = ...,
    ignore_prefixes: list[str] | None = ...,
    pre_buffer: bool = ...,
    coerce_int96_timestamp_unit: str | None = ...,
    thrift_string_size_limit: int | None = ...,
    thrift_container_size_limit: int | None = ...,
    page_checksum_verification: bool = ...,
    encryption_properties: object = ...,
    decryption_properties: object = ...,
    dataset_kwargs: dict[str, object] | None = ...,
) -> pyarrow.Table: ...


def write_table(
    table: pyarrow.Table,
    where: str | Path,
    row_group_size: int | None = ...,
    version: str = ...,
    use_dictionary: bool | list[str] = ...,
    compression: str | dict[str, str] = ...,
    write_statistics: bool | list[str] = ...,
    use_deprecated_int96_timestamps: bool = ...,
    coerce_timestamps: str | None = ...,
    allow_truncated_timestamps: bool = ...,
    data_page_size: int | None = ...,
    flavor: str | None = ...,
    filesystem: object = ...,
    compression_level: int | dict[str, int] | None = ...,
    use_byte_stream_split: bool | list[str] = ...,
    column_encoding: str | dict[str, str] | None = ...,
    data_page_version: str = ...,
    use_compliant_nested_type: bool = ...,
    encryption_properties: object = ...,
    write_batch_size: int | None = ...,
    dictionary_pagesize_limit: int | None = ...,
    store_schema: bool = ...,
    write_page_index: bool = ...,
    write_page_checksum: bool = ...,
    sorting_columns: object = ...,
) -> None: ...
