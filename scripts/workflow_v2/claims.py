"""Domain primitives for Workflow v2 durable claims."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


SELECTOR_RE = re.compile(r"^([1-9][0-9]*)(?:-([1-9][0-9]*))?$")
MAX_CHAPTER_NUMBER = 999_999


class ClaimError(RuntimeError):
    """Base error for durable claim coordination."""


class InvalidClaimSelector(ClaimError):
    """A chapter selector cannot be mapped to canonical workflow units."""


def canonical_unit_id(number: int) -> str:
    """Return the stable Workflow v2 unit ID for one chapter number."""

    if type(number) is not int or not 1 <= number <= MAX_CHAPTER_NUMBER:
        raise InvalidClaimSelector(
            f"chapter number must be an integer from 1 to {MAX_CHAPTER_NUMBER}"
        )
    return f"chapter-{number:06d}"


def resolve_selector(progress: Mapping[str, Any], selector: str) -> list[str]:
    """Resolve an inclusive numeric selector against durable progress state."""

    if not isinstance(progress, Mapping):
        raise InvalidClaimSelector("progress state must be an object")
    chapters = progress.get("chapters")
    if not isinstance(chapters, list):
        raise InvalidClaimSelector("progress state must contain a chapters array")

    available: set[int] = set()
    for index, chapter in enumerate(chapters):
        if not isinstance(chapter, Mapping):
            raise InvalidClaimSelector(f"progress chapter {index + 1} must be an object")
        number = chapter.get("number")
        if type(number) is not int or not 1 <= number <= MAX_CHAPTER_NUMBER:
            raise InvalidClaimSelector(
                f"progress chapter {index + 1} has invalid chapter number {number!r}"
            )
        if number in available:
            raise InvalidClaimSelector(f"progress contains duplicate chapter number {number}")
        available.add(number)

    if not isinstance(selector, str):
        raise InvalidClaimSelector("selector must be a string")
    match = SELECTOR_RE.fullmatch(selector)
    if match is None:
        raise InvalidClaimSelector("selector must be a positive chapter number or inclusive range N-M")

    start = int(match.group(1))
    end = int(match.group(2)) if match.group(2) is not None else start
    if start > MAX_CHAPTER_NUMBER or end > MAX_CHAPTER_NUMBER:
        raise InvalidClaimSelector(
            f"chapter number must not exceed {MAX_CHAPTER_NUMBER}"
        )
    if end < start:
        raise InvalidClaimSelector("range end must not precede range start")

    requested = list(range(start, end + 1))
    missing = [number for number in requested if number not in available]
    if missing:
        joined = ", ".join(str(number) for number in missing)
        raise InvalidClaimSelector(f"selector references missing chapter(s): {joined}")

    return [canonical_unit_id(number) for number in requested]
