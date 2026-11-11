# Release checklist

Recommendation: `accept_for_prototype_with_known_limits`
Ready for this prototype scope: `true`
Approval mode: `demo`

| Item | Status | Evidence |
| --- | --- | --- |
| Requirements normalized | pass | normalized_requirements |
| Ambiguity resolved with a recorded decision | pass | dec_4892f0451b |
| Task plan executed for this brief | pass | task_plan |
| Architecture and design written | pass | design |
| Implementation mapped onto the repository | pass | implementation_report |
| Behavioral test suite passed | pass | test_report |
| Shortener contract tokens present in the test suite | pass |  |
| Documentation snippet produced | pass | docs_snippet |
| Security policy clear of blocking findings | pass | security_review |
| Compliance controls: retention, purge, and referrer sanitizing | pass | security_review |
| Breaking-change control | pass |  |
| Release checkpoint approved | pass | dec_cf3df72cd5 |

## Notes

- **Ambiguity resolved with a recorded decision.** Selected 'analytics' with score 1.0. Rule: highest rubric score, then lexical id. Ranking: analytics=1.0, aliases=0.9, auto_expire_all=0.25. Non-breaking, testable, low-risk, in-codebase, and narrow scope score higher. Demo mode records this choice so the run can continue. A supervised run pauses until a person picks an interpretation.
- **Task plan executed for this brief.** 8 tasks
- **Breaking-change control.** No breaking changes were declared.
- **Release checkpoint approved.** Approved by demo_policy after the pre-approval checkpoint. Supervised mode would wait for a person. Demo mode records the pre-approval checkpoint and then auto-approves so a CLI run can finish. This is not a human release sign-off.

## Production gaps kept visible

- SQLite and the in-memory rate limiter are process-local.
- Create, stats, and delete are unauthenticated. A custom alias is a guessable capability.
- The orchestrator runs in-process. It is not an isolated worker fleet.
- Generated patches are applied only inside a run workspace. They are not merged to the main checkout.
- Demo approval is recorded in the audit log. It is not a production change-management control.
- Referrer retention is a fixed day window in one SQLite file, not a per-tenant policy.
