"""Load and strictly validate the committed synthetic corpus fixture.

A single invalid record fails the whole load. Nothing is skipped silently, and
error messages name the failing field without repeating its value.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.domain.models import SyntheticCorpus


class CorpusValidationError(ValueError):
    pass


def _describe(error: ValidationError) -> str:
    issues = [
        f"{'.'.join(str(part) for part in issue['loc']) or 'corpus'} ({issue['type']})"
        for issue in error.errors()
    ]
    return "; ".join(issues)


def parse_corpus(payload: object) -> SyntheticCorpus:
    try:
        return SyntheticCorpus.model_validate(payload)
    except ValidationError as exc:
        raise CorpusValidationError(
            f"Synthetic corpus failed validation: {_describe(exc)}"
        ) from None


def load_corpus(path: Path) -> SyntheticCorpus:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CorpusValidationError(f"Synthetic corpus file not found: {path.name}") from None
    except json.JSONDecodeError:
        raise CorpusValidationError(f"Synthetic corpus is not valid JSON: {path.name}") from None
    return parse_corpus(payload)
