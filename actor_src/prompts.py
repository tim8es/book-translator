from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def translation_contract() -> str:
    return (ROOT / "docs" / "TRANSLATION.md").read_text(encoding="utf-8")


def translator_messages(
    *,
    source: str,
    target_language: str,
    glossary: str,
    style_guide: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Translator role in Book Translator. Follow the literary contract below. "
                "Return only the complete translated chapter, with no commentary.\n\n"
                + translation_contract()
            ),
        },
        {
            "role": "user",
            "content": (
                f"TARGET LANGUAGE: {target_language}\n\n"
                f"CURRENT GLOSSARY:\n{glossary}\n\n"
                f"CURRENT STYLE GUIDE:\n{style_guide}\n\n"
                f"SOURCE CHAPTER:\n{source}"
            ),
        },
    ]


def reviewer_messages(
    *,
    source: str,
    translation: str,
    target_language: str,
    glossary: str,
    style_guide: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the independent Reviewer role in Book Translator. Follow the contract below. "
                "Compare source against translation. Return ONLY JSON with this exact shape: "
                '{"outcome":"PASS|CORRECTIONS_REQUIRED","issues":[{"source":"...","problem":"...","required_fix":"..."}]}. '
                "Use PASS only when the current artifact truly satisfies the contract.\n\n"
                + translation_contract()
            ),
        },
        {
            "role": "user",
            "content": (
                f"TARGET LANGUAGE: {target_language}\n\n"
                f"GLOSSARY:\n{glossary}\n\n"
                f"STYLE GUIDE:\n{style_guide}\n\n"
                f"SOURCE:\n{source}\n\n"
                f"CURRENT TRANSLATION:\n{translation}"
            ),
        },
    ]


def correction_messages(
    *,
    source: str,
    current_translation: str,
    issues: list[dict],
    target_language: str,
    glossary: str,
    style_guide: str,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are the Translator role revising a chapter after independent review. "
                "Apply every valid review finding without introducing unrelated rewrites. "
                "Return only the complete corrected translation.\n\n"
                + translation_contract()
            ),
        },
        {
            "role": "user",
            "content": (
                f"TARGET LANGUAGE: {target_language}\n\n"
                f"GLOSSARY:\n{glossary}\n\n"
                f"STYLE GUIDE:\n{style_guide}\n\n"
                f"SOURCE:\n{source}\n\n"
                f"CURRENT TRANSLATION:\n{current_translation}\n\n"
                f"REVIEW ISSUES:\n{issues}"
            ),
        },
    ]
