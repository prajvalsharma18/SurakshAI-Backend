"""Privacy-minimized aggregate response shapes for the welfare dashboard."""

from typing import TypedDict


class DashboardPersonnelSummary(TypedDict):
    total_authorized: int


class DashboardRiskSummary(TypedDict):
    LOW: int
    ELEVATED: int
    HIGH: int


class DashboardAlertSummary(TypedDict):
    open: int
    attention: int
    priority: int


class DashboardInterventionSummary(TypedDict):
    active: int
    follow_up: int


class WelfareDashboardSummary(TypedDict):
    personnel: DashboardPersonnelSummary
    risk: DashboardRiskSummary
    alerts: DashboardAlertSummary
    interventions: DashboardInterventionSummary
    generated_at: str
