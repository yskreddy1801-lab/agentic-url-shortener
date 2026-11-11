"""Deterministic ambiguity detector for URL-shortener briefs.

Vague requests are not guessed. The detector proposes a fixed catalog of
interpretations and a published rubric. Demo mode may apply the rubric; a
supervised run waits for a person.
"""

from __future__ import annotations

import re

_VAGUE = re.compile(r"\b(better|nicer|improve|enhance|optimize|faster|modern|cleaner)\b", re.I)
_CONCRETE = re.compile(
    r"(/api/|\bGET\b|\bPOST\b|\bDELETE\b|Acceptance:|\bFR:|\b404\b|\b410\b|\b302\b|\balias\b|\bexpir|\bttl\b|\bclick)",
    re.I,
)
_WORD = re.compile(r"[A-Za-z0-9_:'/-]+")

RULE = "max_rubric_score_then_lexical_id"


def score_interpretation(item: dict) -> float:
    score = 0.0
    if not item["breaking"]:
        score += 0.35
    if item["testable"]:
        score += 0.25
    if item["risk"] == "low":
        score += 0.20
    if item["fits_existing_code"]:
        score += 0.10
    if item["scope"] == "narrow":
        score += 0.10
    return round(score, 2)


def domain_interpretations() -> list[dict]:
    """Three readings of an underspecified 'make links better' request."""
    items = [
        {
            "id": "analytics",
            "title": "Explain how links perform",
            "summary": (
                "Make links better by exposing click analytics: total clicks, "
                "last access, top referrers, and clicks per day."
            ),
            "statement": "Expose click analytics for a short link.",
            "acceptance": [
                "GET /api/v1/urls/{code}/stats returns total clicks, last access, top referrers, and clicks per day."
            ],
            "breaking": False,
            "risk": "low",
            "testable": True,
            "fits_existing_code": True,
            "scope": "narrow",
        },
        {
            "id": "aliases",
            "title": "Let people choose readable aliases",
            "summary": (
                "Make links better by allowing a custom alias on create, "
                "with reserved-word and collision checks."
            ),
            "statement": "Accept an optional custom alias when creating a short link.",
            "acceptance": [
                "POST /api/v1/urls accepts an alias and GET /{code} redirects for that alias."
            ],
            "breaking": False,
            "risk": "low",
            "testable": True,
            "fits_existing_code": True,
            "scope": "medium",
        },
        {
            "id": "auto_expire_all",
            "title": "Expire every existing link",
            "summary": (
                "Make links better by force-expiring every existing link after 7 days, "
                "including links that were created without a TTL."
            ),
            "statement": "Automatically expire every existing link after 7 days.",
            "acceptance": [
                "A retention policy updates links that were created without an expiration."
            ],
            "breaking": True,
            "risk": "high",
            "testable": True,
            "fits_existing_code": False,
            "scope": "wide",
        },
    ]
    for item in items:
        item["score"] = score_interpretation(item)
    return items


def analyze_ambiguity(text: str) -> dict:
    signals: list[str] = []
    vague = _VAGUE.search(text or "")
    if vague:
        signals.append(f"vague_language:{vague.group(1).lower()}")
    has_concrete = bool(_CONCRETE.search(text or ""))
    if not has_concrete:
        signals.append("no_concrete_acceptance_or_contract")
    word_count = len(_WORD.findall(text or ""))
    if word_count < 8:
        signals.append("very_short")
    if "FR:" not in (text or "") and word_count < 30:
        signals.append("no_structured_requirements")
    is_ambiguous = False
    if vague and not has_concrete:
        is_ambiguous = True
    if "very_short" in signals and not has_concrete:
        is_ambiguous = True
    return {
        "is_ambiguous": is_ambiguous,
        "signals": signals,
        "interpretations": domain_interpretations() if is_ambiguous else [],
        "word_count": word_count,
    }


def choose_by_rubric(alternatives: list[dict]) -> dict:
    ranked = sorted(alternatives, key=lambda item: (-float(item["score"]), item["id"]))
    winner = ranked[0]
    ranking = ", ".join(f"{item['id']}={item['score']}" for item in ranked)
    rationale = (
        f"Selected '{winner['id']}' with score {winner['score']}. "
        f"Rule: highest rubric score, then lexical id. Ranking: {ranking}. "
        "Non-breaking, testable, low-risk, in-codebase, and narrow scope score higher. "
        "Demo mode records this choice so the run can continue. "
        "A supervised run pauses until a person picks an interpretation."
    )
    return {"selected": winner["id"], "rationale": rationale, "rule": RULE, "winner": winner}
