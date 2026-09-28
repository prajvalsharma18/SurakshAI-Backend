"""Typed, minimized data-transfer objects for welfare reports."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReportMetadata:
    report_id: str
    generated_at: str
    reference_date: str
    coverage_period: str
    model_version: str
    data_mode: str


@dataclass(frozen=True)
class PersonnelReference:
    pseudonymous_reference: str
    is_pseudonymous: bool = True


@dataclass(frozen=True)
class RiskSummary:
    risk_category: str
    model: str
    data_mode: str
    disclaimer: str


@dataclass(frozen=True)
class RiskObservation:
    reference_date: str
    risk_category: str
    severity: str | None = None


@dataclass(frozen=True)
class RiskTrend:
    observations: tuple[RiskObservation, ...] = ()
    trend_7d: str = 'Historical risk trend unavailable.'
    trend_30d: str = 'Historical risk trend unavailable.'
    persistence: str = 'Historical risk trend unavailable.'


@dataclass(frozen=True)
class ContributingFactor:
    feature: str
    label: str
    value: Any
    direction: str
    magnitude: float
    explanation: str


@dataclass(frozen=True)
class WellnessSummary:
    available: bool
    message: str
    assessment_date: str | None = None
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecommendationSummary:
    category: str
    action: str
    priority: str | None
    rationale: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class AlertSummary:
    alert_reference: str
    severity: str | None
    created_at: str | None
    status: str | None
    acknowledged: bool
    reviewed: bool
    follow_up: str | None
    resolution: str | None


@dataclass(frozen=True)
class InterventionSummary:
    intervention_type: str | None
    created_at: str | None
    status: str | None
    scheduled_follow_up: str | None
    outcome_category: str | None


@dataclass(frozen=True)
class WelfareReportDTO:
    metadata: ReportMetadata
    personnel: PersonnelReference
    risk: RiskSummary
    trend: RiskTrend
    factors: tuple[ContributingFactor, ...]
    operational_summary: dict[str, Any]
    wellness: WellnessSummary
    recommendations: tuple[RecommendationSummary, ...]
    alerts: tuple[AlertSummary, ...]
    interventions: tuple[InterventionSummary, ...]
    privacy_notice: tuple[str, ...]
