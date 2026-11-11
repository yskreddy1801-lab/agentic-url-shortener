# Release checklist

Recommendation: `accept_for_prototype_with_known_limits`
Ready for this prototype scope: `true`
Approval mode: `demo`

| Item | Status | Evidence |
| --- | --- | --- |
| Requirements normalized | pass | normalized_requirements |
| Task plan executed for this brief | pass | task_plan |
| Architecture and design written | pass | design |
| Brownfield impact analysis verified against the source tree | pass | design.impact_analysis |
| Generated patch applied in the run workspace | pass | code_patch |
| Behavioral test suite passed | pass | test_report |
| Shortener contract tokens present in the test suite | pass |  |
| Documentation snippet produced | pass | docs_snippet |
| Security policy clear of blocking findings | pass | security_review |
| Compliance controls: retention, purge, and referrer sanitizing | pass | security_review |
| Breaking-change control | pass |  |
| Release checkpoint approved | pass | dec_ea7ec3d103 |

## Notes

- **Task plan executed for this brief.** 8 tasks
- **Generated patch applied in the run workspace.** Add POST /api/v1/urls/bulk, a service function, app wiring, and a test.
- **Breaking-change control.** No breaking changes were declared.
- **Release checkpoint approved.** Approved by demo_policy after the pre-approval checkpoint. Supervised mode would wait for a person. Demo mode records the pre-approval checkpoint and then auto-approves so a CLI run can finish. This is not a human release sign-off.

## Production gaps kept visible

- SQLite and the in-memory rate limiter are process-local.
- Create, stats, and delete are unauthenticated. A custom alias is a guessable capability.
- The orchestrator runs in-process. It is not an isolated worker fleet.
- Generated patches are applied only inside a run workspace. They are not merged to the main checkout.
- Demo approval is recorded in the audit log. It is not a production change-management control.
- Referrer retention is a fixed day window in one SQLite file, not a per-tenant policy.
