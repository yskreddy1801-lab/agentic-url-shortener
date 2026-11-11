"""Map normalized requirements onto routes and symbols that actually exist."""

from __future__ import annotations

import re

_PATH = re.compile(r"\b(GET|POST|DELETE|PUT|PATCH)\s+(/[A-Za-z0-9_./{}-]+)")
_SYMBOL = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")


def verify_implementation(requirements: dict, code_map: dict) -> dict:
    routes = {(route["method"], route["path"]) for route in code_map.get("routes", [])}
    symbols = set(code_map.get("symbols", []))
    rows = []
    gaps: list[str] = []
    for requirement in requirements.get("functional_requirements") or []:
        text = requirement["statement"] + " " + " ".join(requirement.get("acceptance") or [])
        paths = [(match.group(1).upper(), match.group(2).rstrip(".")) for match in _PATH.finditer(text)]
        named = _SYMBOL.findall(text)
        evidence = []
        if paths:
            for method, path in paths:
                if (method, path) in routes:
                    evidence.append({"type": "route", "method": method, "path": path})
                else:
                    gaps.append(f"{requirement['id']} expects {method} {path}, which is not defined")
            status = "satisfied" if all((method, path) in routes for method, path in paths) else "gap"
        elif named:
            found = [name for name in named if name in symbols]
            if found:
                evidence.append({"type": "symbol", "names": found})
                status = "satisfied"
            else:
                gaps.append(f"{requirement['id']} names {named}, which are not in the source tree")
                status = "gap"
        else:
            gaps.append(f"{requirement['id']} has no API path or `symbol` to verify against the repository")
            status = "gap"
        rows.append(
            {
                "id": requirement["id"],
                "status": status,
                "statement": requirement["statement"],
                "evidence": evidence,
            }
        )
    return {
        "verified": not gaps and bool(rows),
        "requirements": rows,
        "gaps": gaps,
        "route_count": len(routes),
        "note": (
            "This stage checks that each requirement names a route or symbol present in the tree. "
            "Behavioral proof is the testing stage, which executes the shortener suite."
        ),
    }
