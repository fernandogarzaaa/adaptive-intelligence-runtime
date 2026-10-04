"""Finding and report schemas for AIR-SELF-AUDIT.

Every finding carries EXACTLY these keys:
  category, finding, evidence, confidence, reproduction, severity,
  recommended_change.

``recommended_change`` is text only. The harness never applies it.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

CATEGORIES = [
    "Architecture",
    "Persistence",
    "Security",
    "Epistemics",
    "Evaluation",
    "Assurance",
    "Learning",
    "Concurrency",
    "Recovery",
    "Prompt injection",
    "Capability escalation",
    "Evidence contamination",
    "Reward hacking",
]

SEVERITIES = ("critical", "high", "medium", "low")
CONFIDENCE_LEVELS = ("high", "medium", "low")


class Confidence(BaseModel):
    level: str
    justification: str  # one line: why this level

    @field_validator("level")
    @classmethod
    def _level(cls, v: str) -> str:
        if v not in CONFIDENCE_LEVELS:
            raise ValueError(f"confidence level must be one of {CONFIDENCE_LEVELS}")
        return v


class Finding(BaseModel):
    category: str
    finding: str
    evidence: list[str] = Field(default_factory=list)
    confidence: Confidence
    reproduction: str
    severity: str
    recommended_change: str  # text only; the audit never applies it

    @field_validator("category")
    @classmethod
    def _category(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"unknown audit category: {v!r}")
        return v

    @field_validator("severity")
    @classmethod
    def _severity(cls, v: str) -> str:
        if v not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}")
        return v


class ProbeRecord(BaseModel):
    """One probe execution: what ran, what it covered, what it found."""

    name: str
    category: str
    kind: str  # "test_file" | "live"
    passed: bool
    detail: str = ""
    tests_run: list[str] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


class CategoryReport(BaseModel):
    name: str
    status: str  # "clean" | "findings"
    probes: list[ProbeRecord] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)


class AuditReport(BaseModel):
    audit_id: str
    air_version: str
    started_at: str
    completed_at: str | None = None
    categories: list[CategoryReport] = Field(default_factory=list)
    blind_spots: str = ""
    summary: str = ""

    def severity_counts(self) -> dict[str, int]:
        counts = {s: 0 for s in SEVERITIES}
        for cat in self.categories:
            for f in cat.findings:
                counts[f.severity] += 1
        return counts
