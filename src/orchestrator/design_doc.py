"""Build a design note from normalized requirements and the scanned source tree."""

from __future__ import annotations

import re

from orchestrator.impact import analyze_alias_and_expiry

_PATH = re.compile(r"\b(GET|POST|DELETE|PUT|PATCH)\s+(/[A-Za-z0-9_./{}-]+)")


def build_design(requirements: dict, code_map: dict) -> dict:
    requirement_ids = [item["id"] for item in requirements.get("functional_requirements") or []]
    apis = []
    seen = set()
    for item in requirements.get("functional_requirements") or []:
        text = item["statement"] + " " + " ".join(item.get("acceptance") or [])
        wanted = {(match.group(1).upper(), match.group(2).rstrip(".")) for match in _PATH.finditer(text)}
        for route in code_map.get("routes", []):
            key = (route["method"], route["path"], item["id"])
            if (route["method"], route["path"]) in wanted and key not in seen:
                seen.add(key)
                apis.append(
                    {
                        "method": route["method"],
                        "path": route["path"],
                        "function": route["function"],
                        "file": route["file"],
                        "requirement_id": item["id"],
                    }
                )
    components = [
        {"name": module["path"], "symbols": module["symbols"][:12]}
        for module in code_map.get("modules", [])
        if module["path"] != "src/shortener/__init__.py"
    ]
    data_model = []
    for module in code_map.get("modules", []):
        if not module["path"].endswith("models.py"):
            continue
        for cls in module["classes"]:
            if cls["columns"]:
                data_model.append({"class": cls["name"], "module": module["path"], "columns": cls["columns"]})
    breaking = []
    if requirements.get("breaking"):
        breaking.append(
            {
                "id": "BC-1",
                "description": "The chosen interpretation changes behavior of links that already exist.",
                "requires_acceptance": True,
            }
        )
    impact = None
    if requirements.get("kind") == "brownfield":
        impact = analyze_alias_and_expiry(code_map)
    selected = requirements.get("selected_interpretation")
    summary = _summary(requirements, selected)
    document = {
        "title": requirements.get("title") or "URL shortener design",
        "summary": summary,
        "requirement_ids": requirement_ids,
        "kind": requirements.get("kind"),
        "selected_interpretation": selected,
        "components": components,
        "apis": apis,
        "data_model": data_model,
        "failure_modes": _failure_modes(),
        "decisions": _decisions(),
        "breaking_changes": breaking,
        "impact_analysis": impact,
        "discovered_routes": [
            {"method": route["method"], "path": route["path"], "function": route["function"]}
            for route in code_map.get("routes", [])
        ],
    }
    document["markdown"] = _markdown(document, requirements)
    return document


def _summary(requirements: dict, selected: str | None) -> str:
    base = requirements.get("problem_statement") or "Shorten http(s) URLs and report how they are used."
    if selected:
        return f"Chosen interpretation '{selected}'. {base}"
    return base


def _failure_modes() -> list[dict]:
    return [
        {"condition": "Unknown code", "response": "404 not_found"},
        {"condition": "Expired or disabled link", "response": "410 expired or disabled"},
        {"condition": "Alias already used", "response": "409 alias_taken"},
        {"condition": "Same Idempotency-Key, different body", "response": "409 idempotency_conflict"},
        {"condition": "Non-http(s) scheme or embedded credentials", "response": "422 unsafe_url"},
        {"condition": "Create rate exceeded", "response": "429 rate_limited"},
        {"condition": "Generated code collides", "response": "Retry with a new code, then 503"},
        {"condition": "Click insert fails", "response": "Log and still redirect"},
    ]


def _decisions() -> list[dict]:
    return [
        {
            "decision": "Redirect with 302 and Cache-Control: no-store",
            "why": "A cached 301 would keep sending people to a target after expiry or disable.",
        },
        {
            "decision": "Non-sequential codes from a CSPRNG alphabet",
            "why": "A counter leaks volume and lets neighbors guess nearby links.",
        },
        {
            "decision": "Soft delete",
            "why": "Metadata and stats stay explainable after a link is turned off.",
        },
        {
            "decision": "Idempotency-Key compares a hash of the request, not of the derived expiry instant",
            "why": "A replay must return the original row. Recomputing ttl from 'now' would mint a second link.",
        },
        {
            "decision": "Generated patches land in the run workspace, not in the main checkout",
            "why": "A missing route becomes a unified diff and new files. Applying them requires approval. The main tree stays clean.",
        },
    ]


def _markdown(document: dict, requirements: dict) -> str:
    lines = [
        f"# {document['title']}",
        "",
        document["summary"],
        "",
        "## Requirements in scope",
        "",
    ]
    for item in requirements.get("functional_requirements") or []:
        lines.append(f"- **{item['id']}**: {item['statement']}")
        for acceptance in item.get("acceptance") or []:
            lines.append(f"  - Acceptance: {acceptance}")
    lines.extend(["", "## API surface grounded in the source tree", ""])
    if document["apis"]:
        for api in document["apis"]:
            lines.append(
                f"- `{api['method']} {api['path']}` in `{api['file']}` (`{api['function']}`) for {api['requirement_id']}"
            )
    else:
        lines.append("- No requirement path matched a scanned route.")
    lines.extend(["", "## Data model", ""])
    for model in document["data_model"]:
        lines.append(f"- `{model['class']}` in `{model['module']}`: {', '.join(model['columns'])}")
    lines.extend(["", "## Decisions", ""])
    for item in document["decisions"]:
        lines.append(f"- **{item['decision']}.** {item['why']}")
    lines.extend(["", "## Failure modes", ""])
    for item in document["failure_modes"]:
        lines.append(f"- {item['condition']}: {item['response']}")
    impact = document.get("impact_analysis")
    if impact:
        lines.extend(["", "## Impact analysis", "", impact["change"], ""])
        lines.append("Modules:")
        for module in impact["impacted_modules"]:
            lines.append(f"- `{module['path']}` ({', '.join(module['symbols'])}): {module['reason']}")
        lines.append("")
        lines.append("APIs:")
        for api in impact["impacted_apis"]:
            lines.append(f"- `{api['method']} {api['path']}`: {api['change']}")
        lines.append("")
        lines.append("Data flow:")
        for step in impact["data_flow"]:
            lines.append(f"- {step}")
        lines.append("")
        lines.append("Risks:")
        for risk in impact["risks"]:
            lines.append(f"- {risk}")
        if impact["missing_symbols"]:
            lines.append("")
            lines.append("Missing symbols:")
            for item in impact["missing_symbols"]:
                lines.append(f"- {item}")
    if document["breaking_changes"]:
        lines.extend(["", "## Breaking changes", ""])
        for change in document["breaking_changes"]:
            lines.append(f"- {change['id']}: {change['description']}")
    lines.append("")
    return "\n".join(lines)
