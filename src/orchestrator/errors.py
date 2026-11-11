"""Control-flow exceptions for the workflow engine."""

from __future__ import annotations


class OrchestratorError(Exception):
    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class WorkflowPaused(Exception):
    def __init__(self, kind: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.kind = kind
        self.details = details or {}


class StageFailed(Exception):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class PolicyViolation(StageFailed):
    def __init__(self, message: str, findings: list | None = None):
        super().__init__(message, retryable=False)
        self.findings = findings or []


class GateFailure(Exception):
    def __init__(self, gate: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.gate = gate
        self.retryable = retryable
