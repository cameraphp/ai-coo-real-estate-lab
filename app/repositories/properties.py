from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from app.domain.models import SyntheticProperty


class PropertyRepository(Protocol):
    def list_all(self) -> list[SyntheticProperty]: ...


class SyntheticJsonPropertyRepository:
    def __init__(self, catalog_path: Path | None = None) -> None:
        if catalog_path is None:
            catalog_path = (
                Path(__file__).resolve().parents[2] / "data" / "properties.synthetic.json"
            )
        self._catalog_path = catalog_path
        self._cache: list[SyntheticProperty] | None = None

    def _load(self) -> list[SyntheticProperty]:
        if self._cache is not None:
            return self._cache
        raw = self._catalog_path.read_text(encoding="utf-8")
        items = json.loads(raw)
        parsed = [SyntheticProperty.model_validate(item) for item in items]
        self._cache = parsed
        return parsed

    def list_all(self) -> list[SyntheticProperty]:
        return list(self._load())
