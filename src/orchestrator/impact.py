"""Read the shortener source tree and describe real modules, routes, and symbols."""

from __future__ import annotations

import ast
from pathlib import Path

REQUIRED_SYMBOLS = {
    "src/shortener/models.py": ["ShortLink", "Click"],
    "src/shortener/service.py": [
        "create_link",
        "resolve_redirect",
        "link_status",
        "record_click",
        "deactivate_link",
        "link_stats",
    ],
    "src/shortener/api.py": ["create_url", "redirect_code", "get_metadata", "get_stats", "delete_url"],
    "src/shortener/validation.py": ["validate_target_url", "validate_alias"],
    "src/shortener/codes.py": ["generate_code", "RESERVED_ALIASES"],
}

REQUIRED_COLUMNS = ["code", "target_url", "expires_at", "disabled_at", "idempotency_key"]

REQUIRED_ROUTES = [
    ("POST", "/api/v1/urls"),
    ("GET", "/{code}"),
    ("GET", "/api/v1/urls/{code}"),
    ("GET", "/api/v1/urls/{code}/stats"),
    ("DELETE", "/api/v1/urls/{code}"),
]


def scan_repository(root: Path) -> dict:
    package = root / "src" / "shortener"
    modules = []
    routes = []
    symbols: list[str] = []
    if not package.is_dir():
        return {"modules": [], "routes": [], "symbols": [], "missing_package": True}
    for path in sorted(package.glob("*.py")):
        info = _scan_file(path, root)
        modules.append(info)
        routes.extend(info["routes"])
        symbols.extend(info["symbols"])
    return {"modules": modules, "routes": routes, "symbols": symbols, "missing_package": False}


def analyze_alias_and_expiry(code_map: dict) -> dict:
    by_path = {module["path"]: module for module in code_map.get("modules", [])}
    missing: list[str] = []
    impacted = []
    for rel, expected in REQUIRED_SYMBOLS.items():
        module = by_path.get(rel)
        present = set(module["symbols"]) if module else set()
        found = [name for name in expected if name in present]
        absent = [name for name in expected if name not in present]
        if module is None:
            missing.append(f"{rel} (file missing)")
        for name in absent:
            missing.append(f"{rel}:{name}")
        if module is not None:
            impacted.append(
                {
                    "path": rel,
                    "symbols": found,
                    "reason": _reason(rel),
                }
            )
    columns = []
    model = by_path.get("src/shortener/models.py")
    if model:
        for cls in model["classes"]:
            if cls["name"] == "ShortLink":
                columns = cls["columns"]
    for column in REQUIRED_COLUMNS:
        if column not in columns:
            missing.append(f"ShortLink.{column}")
    route_set = {(route["method"], route["path"]) for route in code_map.get("routes", [])}
    impacted_apis = []
    for method, path in REQUIRED_ROUTES:
        if (method, path) not in route_set:
            missing.append(f"{method} {path}")
        else:
            impacted_apis.append({"method": method, "path": path, "change": _api_change(method, path)})
    data_flow = _data_flow(set(code_map.get("symbols", [])))
    return {
        "change": "optional custom aliases and optional link expiration",
        "impacted_modules": impacted,
        "impacted_apis": impacted_apis,
        "data_model_columns": columns,
        "data_flow": data_flow,
        "risks": [
            "Custom aliases are low entropy. Stats and delete are unauthenticated, so a guessable alias is a capability.",
            "Expiry uses the application clock. A skewed clock expires links early or late.",
            "Redirect responses send Cache-Control: no-store so a cached 302 does not outlive expiry.",
            "Links created without an alias or expiry are unchanged. This change does not backfill a TTL.",
        ],
        "verified_symbols": not missing,
        "missing_symbols": missing,
    }


def _scan_file(path: Path, root: Path) -> dict:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions: list[str] = []
    classes: list[dict] = []
    routes: list[dict] = []
    assigned: list[str] = []
    rel = str(path.relative_to(root))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            functions.append(node.name)
            for decorator in node.decorator_list:
                route = _route_from_decorator(decorator)
                if route:
                    method, route_path = route
                    routes.append(
                        {"method": method, "path": route_path, "function": node.name, "file": rel}
                    )
        elif isinstance(node, ast.ClassDef):
            columns = []
            methods = []
            for child in node.body:
                if isinstance(child, ast.FunctionDef):
                    methods.append(child.name)
                elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    columns.append(child.target.id)
            classes.append({"name": node.name, "methods": methods, "columns": columns})
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.append(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            assigned.append(node.target.id)
    symbols = functions + [cls["name"] for cls in classes] + assigned
    return {
        "path": rel,
        "functions": functions,
        "classes": classes,
        "routes": routes,
        "assigns": assigned,
        "symbols": symbols,
    }


def _route_from_decorator(decorator: ast.AST) -> tuple[str, str] | None:
    if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
        return None
    method = decorator.func.attr.upper()
    if method not in {"GET", "POST", "DELETE", "PUT", "PATCH"} or not decorator.args:
        return None
    arg = decorator.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return method, arg.value
    return None


def _reason(path: str) -> str:
    return {
        "src/shortener/models.py": "ShortLink stores the public code, target, expiry, and soft-delete timestamp.",
        "src/shortener/service.py": "Create, redirect policy, click recording, and stats live in the service.",
        "src/shortener/api.py": "HTTP contract for create, redirect, metadata, stats, and delete.",
        "src/shortener/validation.py": "Scheme checks and alias rules run before a row is written.",
        "src/shortener/codes.py": "Reserved aliases and the non-sequential code generator.",
    }.get(path, "Touched by alias or expiry behavior.")


def _api_change(method: str, path: str) -> str:
    key = (method, path)
    return {
        ("POST", "/api/v1/urls"): "Accepts optional alias, ttl_seconds, and expires_at.",
        ("GET", "/{code}"): "Returns 410 when the link is expired or disabled, otherwise 302 and a click.",
        ("GET", "/api/v1/urls/{code}"): "Metadata includes expiry, disabled flag, and status.",
        ("GET", "/api/v1/urls/{code}/stats"): "Stats remain available after expiry or soft-delete.",
        ("DELETE", "/api/v1/urls/{code}"): "Soft-disables the link. A second delete is idempotent.",
    }.get(key, "Contract touched by the enhancement.")


def _data_flow(symbols: set[str]) -> list[str]:
    steps = []
    if "validate_target_url" in symbols and "validate_alias" in symbols and "create_link" in symbols:
        steps.append(
            "Client POST body enters validate_target_url and validate_alias, then create_link "
            "persists a ShortLink with code, target_url, expires_at, and optional idempotency_key."
        )
    if "resolve_redirect" in symbols and "link_status" in symbols and "record_click" in symbols:
        steps.append(
            "GET /{code} calls resolve_redirect. link_status returns disabled or expired as 410, "
            "and an active link continues to record_click before a 302."
        )
    if "link_stats" in symbols:
        steps.append(
            "GET /api/v1/urls/{code}/stats calls link_stats, which aggregates clicks for that link "
            "even when the link is later expired or disabled."
        )
    if "deactivate_link" in symbols:
        steps.append(
            "DELETE /api/v1/urls/{code} calls deactivate_link, which sets disabled_at and leaves the row in place."
        )
    return steps
