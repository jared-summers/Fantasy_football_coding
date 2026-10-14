"""Central parsers for Yahoo's irregular JSON response shape."""
from __future__ import annotations

from collections.abc import Iterator
import re
from typing import Any

import pandas as pd

IDENTITY_COLUMNS = ["season", "week", "game_id", "league_id", "league_key"]


def snake_case(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()


def normalize_numbered(value: Any) -> Any:
    """Turn Yahoo's ``{0: ..., 1: ..., count: n}`` pseudo-lists into lists."""
    if isinstance(value, list):
        items = [normalize_numbered(item) for item in value]
        # Yahoo represents an XML object as a list of one-key dictionaries.
        # Merge only when keys do not collide; repeated keys denote a real list.
        dictionaries = [item for item in items if isinstance(item, dict)]
        other_items = [item for item in items if item not in ([], {}) and not isinstance(item, dict)]
        all_keys = [key for item in dictionaries for key in item]
        if dictionaries and not other_items and len(all_keys) == len(set(all_keys)):
            merged: dict[str, Any] = {}
            for item in dictionaries:
                merged.update(item)
            return merged
        return items
    if not isinstance(value, dict):
        return value
    normalized = {str(key): normalize_numbered(item) for key, item in value.items()}
    numeric = sorted((key for key in normalized if key.isdigit()), key=int)
    non_numeric = [key for key in normalized if not key.isdigit() and key != "count"]
    if numeric and not non_numeric:
        return [normalized[key] for key in numeric]
    # ``count`` is metadata only on numbered pseudo-lists. On a normal object
    # it can be a meaningful roster-slot value and must be retained.
    return normalized


def walk(value: Any) -> Iterator[dict[str, Any]]:
    value = normalize_numbered(value)
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def first_value(value: Any, key: str, default: Any = None) -> Any:
    for item in walk(value):
        if key in item and not isinstance(item[key], (dict, list)):
            return item[key]
    return default


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten scalar leaves with predictable snake_case keys."""
    value = normalize_numbered(value)
    result: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}_{snake_case(key)}" if prefix else snake_case(key)
            if isinstance(child, (dict, list)):
                result.update(flatten(child, name))
            else:
                result[name] = child
    elif isinstance(value, list):
        for index, child in enumerate(value):
            result.update(flatten(child, f"{prefix}_{index}"))
    elif prefix:
        result[prefix] = value
    return result


def entity_objects(payload: Any, entity_key: str) -> list[dict[str, Any]]:
    """Return the largest objects that identify Yahoo entities, once each."""
    candidates = [item for item in walk(payload) if entity_key in item]
    # A player may be nested in several wrappers. De-duplicate by its raw key.
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        identifier = str(item[entity_key])
        if identifier not in seen:
            seen.add(identifier)
            result.append(item)
    return result


def stat_columns(value: Any, prefix: str = "") -> dict[str, Any]:
    """Preserve Yahoo stat ids/names as stable, useful columns where present."""
    output: dict[str, Any] = {}
    for item in walk(value):
        stat_id = item.get("stat_id")
        stat_name = item.get("name") or item.get("display_name")
        stat_value = item.get("value")
        if stat_id is not None and stat_value is not None:
            label = snake_case(str(stat_name or f"stat_{stat_id}"))
            output[f"{prefix}{label}" if prefix else label] = stat_value
    return output


def stable_frame(records: list[dict[str, Any]], sort_by: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame.from_records(records)
    for column in IDENTITY_COLUMNS:
        if column not in frame:
            frame[column] = None
    ordered = [column for column in IDENTITY_COLUMNS if column in frame]
    ordered += sorted(column for column in frame.columns if column not in ordered)
    frame = frame[ordered]
    present = [column for column in sort_by if column in frame]
    return frame.sort_values(present, kind="stable").reset_index(drop=True) if present else frame
