"""Turn a brief into a normalized requirements document."""

from __future__ import annotations

import re

from orchestrator.ambiguity import analyze_ambiguity

_FR = re.compile(r"^\s*-\s*FR:\s*(.*)$")
_ACCEPT = re.compile(r"^\s*Acceptance:\s*(.*)$")
_BULLET = re.compile(r"^\s*-\s+(.*)$")


def normalize_brief(brief: str, *, kind: str) -> dict:
    text = (brief or "").strip()
    title = _title(text)
    problem = _section(text, "Problem") or text
    requirements = _parse_requirements(_section(text, "Requirements") or text)
    return {
        "title": title,
        "kind": kind,
        "problem_statement": " ".join(problem.split()),
        "actors": ["client application", "link recipient", "operator"],
        "functional_requirements": requirements,
        "constraints": _bullets(_section(text, "Constraints")),
        "out_of_scope": _bullets(_section(text, "Out of scope")),
        "ambiguity": analyze_ambiguity(text),
        "decision": None,
        "selected_interpretation": None,
        "breaking": False,
    }


def _title(text: str) -> str:
    for line in text.splitlines():
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return "Untitled request"


def _section(text: str, name: str) -> str:
    pattern = re.compile(rf"(?ms)^{re.escape(name)}:\s*\n(.*?)(?=^[A-Z][^:\n]{{0,40}}:\s*$|\Z)")
    match = pattern.search(text)
    return match.group(1).strip() if match else ""


def _parse_requirements(section: str) -> list[dict]:
    items: list[dict] = []
    current: dict | None = None
    for line in section.splitlines():
        found = _FR.match(line)
        if found:
            if current:
                items.append(current)
            current = {"statement": found.group(1).strip(), "acceptance": []}
            continue
        accepted = _ACCEPT.match(line)
        if accepted and current is not None:
            current["acceptance"].append(accepted.group(1).strip())
    if current:
        items.append(current)
    for index, item in enumerate(items, start=1):
        item["id"] = f"FR-{index}"
    return items


def _bullets(section: str) -> list[str]:
    if not section:
        return []
    return [match.group(1).strip() for match in (_BULLET.match(line) for line in section.splitlines()) if match]
