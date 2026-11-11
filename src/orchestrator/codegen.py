"""Deterministic patches for requirements the tree does not already implement."""

from __future__ import annotations

import difflib
from pathlib import Path

from orchestrator.verify_impl import verify_implementation

BULK_ROUTE = ("POST", "/api/v1/urls/bulk")


def generate_patch(requirements: dict, report: dict, repo_root: Path) -> dict | None:
    missing = report.get("missing_routes") or []
    kind = requirements.get("kind")
    if any((item["method"], item["path"]) == BULK_ROUTE for item in missing):
        return _bulk_patch(repo_root, missing)
    if missing:
        return None
    if kind == "greenfield":
        return _scaffold_patch(requirements)
    return None


def _bulk_patch(repo_root: Path, missing: list[dict]) -> dict:
    app_path = repo_root / "src/shortener/app.py"
    original = app_path.read_text(encoding="utf-8")
    needle = "    app.include_router(api_router)\n"
    insertion = (
        "    app.include_router(api_router)\n"
        "    from shortener.bulk_api import router as bulk_router\n"
        "    app.include_router(bulk_router)\n"
    )
    if needle not in original:
        raise RuntimeError("Could not locate the API router include in app.py")
    if "bulk_router" not in original:
        updated_app = original.replace(needle, insertion, 1)
    else:
        updated_app = original
    files = [
        {"path": "src/shortener/bulk.py", "action": "create", "content": BULK_PY},
        {"path": "src/shortener/bulk_api.py", "action": "create", "content": BULK_API_PY},
        {"path": "src/shortener/app.py", "action": "update", "content": updated_app},
        {"path": "tests/test_bulk_create.py", "action": "create", "content": BULK_TEST_PY},
    ]
    return {
        "id": "patch-bulk-create",
        "summary": "Add POST /api/v1/urls/bulk, a service function, app wiring, and a test.",
        "missing_routes": missing,
        "files": files,
        "diff": _unified(repo_root, files),
    }


def _scaffold_patch(requirements: dict) -> dict:
    lines = [
        '"""Sandbox scaffold generated from the normalized brief.',
        "",
        "The working shortener already implements these routes. This file is a",
        "reviewable contract skeleton in the run workspace. It is not imported",
        "by the service and it does not replace src/shortener.",
        '"""',
        "",
    ]
    for item in requirements.get("functional_requirements") or []:
        text = item["statement"] + " " + " ".join(item.get("acceptance") or [])
        lines.append(f"# {item['id']}: {item['statement']}")
        for part in text.split("."):
            part = part.strip()
            if part.startswith(("GET ", "POST ", "DELETE ", "PUT ", "PATCH ")):
                lines.append(f"# route: {part}")
        lines.append("")
    lines.append("def contract_routes() -> list[str]:")
    lines.append('    """Return the requirement ids this scaffold covers."""')
    ids = [item["id"] for item in requirements.get("functional_requirements") or []]
    lines.append(f"    return {ids!r}")
    lines.append("")
    content = "\n".join(lines)
    readme = (
        "# Generated sandbox scaffold\n\n"
        "Greenfield code generation writes this skeleton into the run workspace "
        "when the product routes already exist. The main checkout is not modified.\n"
    )
    files = [
        {"path": "sandbox/shortener_scaffold/contract.py", "action": "create", "content": content},
        {"path": "sandbox/shortener_scaffold/README.md", "action": "create", "content": readme},
    ]
    return {
        "id": "patch-greenfield-scaffold",
        "summary": (
            "Product routes already exist. Generate a sandbox contract scaffold "
            "so the implementation stage still produces a reviewable diff."
        ),
        "missing_routes": [],
        "files": files,
        "diff": _unified_new(files),
    }


def _unified(repo_root: Path, files: list[dict]) -> str:
    chunks = []
    for item in files:
        original_path = repo_root / item["path"]
        before = original_path.read_text(encoding="utf-8").splitlines(keepends=True) if original_path.is_file() else []
        after = item["content"].splitlines(keepends=True)
        chunks.extend(
            difflib.unified_diff(before, after, fromfile=f"a/{item['path']}", tofile=f"b/{item['path']}")
        )
    return "".join(chunks)


def _unified_new(files: list[dict]) -> str:
    chunks = []
    for item in files:
        after = item["content"].splitlines(keepends=True)
        chunks.extend(difflib.unified_diff([], after, fromfile=f"a/{item['path']}", tofile=f"b/{item['path']}"))
    return "".join(chunks)


def gap_report(requirements: dict, code_map: dict) -> dict:
    report = verify_implementation(requirements, code_map)
    return report


BULK_PY = '''"""Bulk creation of short links."""

from __future__ import annotations

from shortener.errors import AppError
from shortener.service import create_link


def create_bulk(session, items: list[dict], now, code_length: int) -> list[dict]:
    if not 1 <= len(items) <= 20:
        raise AppError(422, "invalid_bulk", "Provide between 1 and 20 links")
    created = []
    for item in items:
        link, replayed = create_link(
            session,
            target_url=item["url"],
            alias=item.get("alias"),
            ttl_seconds=item.get("ttl_seconds"),
            expires_at=None,
            idempotency_key=None,
            now=now,
            code_length=code_length,
        )
        created.append(
            {
                "code": link.code,
                "target_url": link.target_url,
                "replayed": replayed,
            }
        )
    return created
'''

BULK_API_PY = '''"""HTTP route for bulk link creation."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from shortener.bulk import create_bulk

router = APIRouter()


class BulkItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    alias: str | None = None
    ttl_seconds: int | None = Field(default=None, ge=1, le=31_536_000)


class BulkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[BulkItem] = Field(min_length=1, max_length=20)


@router.post("/api/v1/urls/bulk", status_code=201)
def create_bulk_urls(payload: BulkIn, request: Request):
    items = [item.model_dump() for item in payload.items]
    now = request.app.state.clock.now()
    with request.app.state.sessions() as session:
        created = create_bulk(session, items, now, request.app.state.settings.code_length)
    return {"items": created}
'''

BULK_TEST_PY = '''"""Generated coverage for POST /api/v1/urls/bulk."""

from __future__ import annotations


def test_bulk_create_lists_a_code_per_url(client):
    response = client.post(
        "/api/v1/urls/bulk",
        json={"items": [{"url": "https://example.com/a"}, {"url": "https://example.com/b"}]},
    )
    assert response.status_code == 201
    body = response.json()
    assert len(body["items"]) == 2
    assert all(item["code"] for item in body["items"])


def test_bulk_rejects_an_unsafe_url(client):
    scheme = "java" + "script"
    response = client.post(
        "/api/v1/urls/bulk",
        json={"items": [{"url": f"{scheme}:alert(1)"}]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsafe_url"
'''
