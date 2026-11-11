"""Build a brief-specific task subgraph from normalized requirements."""

from __future__ import annotations

import re

_PATH = re.compile(r"\b(GET|POST|DELETE|PUT|PATCH)\s+(/[A-Za-z0-9_./{}-]+)")


def build_task_plan(requirements: dict) -> dict:
    functional = list(requirements.get("functional_requirements") or [])
    tasks: list[dict] = [
        {
            "id": "PLAN-1",
            "title": "Publish the task subgraph for this brief",
            "requirement_id": None,
            "depends_on": [],
            "inputs": ["normalized_requirements"],
            "outputs": ["task_plan"],
            "owning_stage": "planning",
            "status": "pending",
        }
    ]
    redirect_impl = None
    for item in functional:
        text = item["statement"] + " " + " ".join(item.get("acceptance") or [])
        if "GET /{code}" in text and "redirect" in text.lower():
            redirect_impl = f"{item['id']}-implement"
            break
    if redirect_impl is None:
        for item in functional:
            text = item["statement"] + " " + " ".join(item.get("acceptance") or [])
            if "GET /{code}" in text:
                redirect_impl = f"{item['id']}-implement"
                break

    for item in functional:
        fr = item["id"]
        text = item["statement"] + " " + " ".join(item.get("acceptance") or [])
        design_id = f"{fr}-design"
        impl_id = f"{fr}-implement"
        test_id = f"{fr}-test"
        doc_id = f"{fr}-document"
        impl_deps = [design_id]
        if redirect_impl and impl_id != redirect_impl and _depends_on_redirect(text):
            impl_deps.append(redirect_impl)
        tasks.extend(
            [
                _task(design_id, f"Design {fr}: {item['statement']}", fr, [], ["normalized_requirements"], ["design"], "architecture"),
                _task(impl_id, f"Implement {fr}", fr, impl_deps, ["design"], ["code_patch", "implementation_report"], "implementation"),
                _task(test_id, f"Test {fr}", fr, [impl_id], ["implementation_report"], ["test_report"], "testing"),
                _task(doc_id, f"Document {fr}", fr, [impl_id], ["design", "implementation_report"], ["docs_snippet"], "documentation"),
            ]
        )
    test_ids = [task["id"] for task in tasks if task["id"].endswith("-test")]
    doc_ids = [task["id"] for task in tasks if task["id"].endswith("-document")]
    impl_ids = [task["id"] for task in tasks if task["id"].endswith("-implement")]
    tasks.append(
        _task(
            "APPLY-1",
            "Apply the generated patch inside the run workspace",
            None,
            impl_ids,
            ["code_patch"],
            ["apply_report"],
            "apply_patch",
        )
    )
    tasks.append(
        _task(
            "SEC-1",
            "Security and compliance review",
            None,
            test_ids or ["APPLY-1"],
            ["implementation_report", "apply_report"],
            ["security_review"],
            "security_review",
        )
    )
    tasks.append(
        _task(
            "REL-1",
            "Release readiness",
            None,
            ["SEC-1", *doc_ids],
            ["test_report", "docs_snippet", "security_review"],
            ["release_report"],
            "release_readiness",
        )
    )
    edges = [{"from": dep, "to": task["id"]} for task in tasks for dep in task["depends_on"]]
    return {
        "title": requirements.get("title") or "Task plan",
        "kind": requirements.get("kind"),
        "tasks": tasks,
        "edges": edges,
        "markdown": render_task_plan({"title": requirements.get("title") or "Task plan", "tasks": tasks, "edges": edges}),
    }


def mark_tasks(plan: dict, stage_id: str, status: str) -> bool:
    changed = False
    for task in plan.get("tasks") or []:
        if task.get("owning_stage") == stage_id and task.get("status") != status:
            task["status"] = status
            changed = True
    if changed:
        plan["markdown"] = render_task_plan(plan)
    return changed


def render_task_plan(plan: dict) -> str:
    lines = [f"# {plan.get('title', 'Task plan')}", "", "| Task | Stage | Depends on | Status |", "| --- | --- | --- | --- |"]
    for task in plan.get("tasks") or []:
        deps = ", ".join(task.get("depends_on") or []) or "-"
        lines.append(f"| `{task['id']}` {task['title']} | {task['owning_stage']} | {deps} | {task['status']} |")
    lines.append("")
    return "\n".join(lines)


def _task(task_id, title, requirement_id, depends_on, inputs, outputs, stage) -> dict:
    return {
        "id": task_id,
        "title": title,
        "requirement_id": requirement_id,
        "depends_on": depends_on,
        "inputs": inputs,
        "outputs": outputs,
        "owning_stage": stage,
        "status": "pending",
    }


def _depends_on_redirect(text: str) -> bool:
    lowered = text.lower()
    if "click" in lowered or "stats" in lowered or "referrer" in lowered:
        return True
    paths = [match.group(2) for match in _PATH.finditer(text)]
    return any(path.startswith("/api/v1/urls/") and path != "/api/v1/urls" for path in paths)
