# Bulk link creation

| Task | Stage | Depends on | Status |
| --- | --- | --- | --- |
| `PLAN-1` Publish the task subgraph for this brief | planning | - | succeeded |
| `FR-1-design` Design FR-1: Create many links in one call with POST /api/v1/urls/bulk. | architecture | - | succeeded |
| `FR-1-implement` Implement FR-1 | implementation | FR-1-design | succeeded |
| `FR-1-test` Test FR-1 | testing | FR-1-implement | succeeded |
| `FR-1-document` Document FR-1 | documentation | FR-1-implement | succeeded |
| `APPLY-1` Apply the generated patch inside the run workspace | apply_patch | FR-1-implement | succeeded |
| `SEC-1` Security and compliance review | security_review | FR-1-test | succeeded |
| `REL-1` Release readiness | release_readiness | SEC-1, FR-1-document | succeeded |
