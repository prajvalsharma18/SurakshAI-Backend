"""Authoritative, aggregate-only welfare dashboard metrics."""

from datetime import datetime, timezone

from src.schemas.welfare_dashboard import WelfareDashboardSummary
from src.security.audit import RESULT_SUCCESS, get_audit_service


class WelfareDashboardService:
    """Compose repository aggregates without fetching per-person case data."""

    def __init__(self, personnel_repository, risk_repository, alert_repository, intervention_repository, audit_service=None):
        self.personnel_repository = personnel_repository
        self.risk_repository = risk_repository
        self.alert_repository = alert_repository
        self.intervention_repository = intervention_repository
        self.audit_service = audit_service or get_audit_service()

    def get_summary(self, identity) -> WelfareDashboardSummary:
        identity = identity or {}
        if identity.get('role') not in {'WELFARE_OFFICER', 'ADMIN'}:
            raise PermissionError('Forbidden')

        risk = self.risk_repository.get_summary()
        alerts = self.alert_repository.dashboard_summary()
        interventions = self.intervention_repository.dashboard_summary()
        generated_at = datetime.now(timezone.utc).isoformat()
        response = {
            'personnel': {'total_authorized': int(self.personnel_repository.count_authorized())},
            'risk': {category: int(risk.get('risk_distribution', {}).get(category, 0)) for category in ('LOW', 'ELEVATED', 'HIGH')},
            'alerts': {
                'open': int(alerts.get('open', 0)),
                'attention': int(alerts.get('attention', 0)),
                'priority': int(alerts.get('priority', 0)),
            },
            'interventions': {
                'active': int(interventions.get('active', 0)),
                'follow_up': int(interventions.get('follow_up', 0)),
            },
            'generated_at': generated_at,
        }
        self.audit_service.record_event(
            actor_user_id=identity.get('user_id'),
            actor_role=identity.get('role'),
            action='WELFARE_DASHBOARD_SUMMARY_VIEW',
            resource_type='welfare_dashboard_summary',
            result=RESULT_SUCCESS,
            source='welfare_dashboard_service.get_summary',
            purpose='View aggregate welfare dashboard metrics',
        )
        return response
