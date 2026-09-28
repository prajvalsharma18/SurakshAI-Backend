"""Authorization-aware assembly of privacy-minimized welfare report DTOs."""

from datetime import date, timedelta, datetime, timezone
from uuid import uuid4

from src.security.audit import RESULT_FAILURE, RESULT_SUCCESS, get_audit_service
from src.security.consent import CONSENT_WELLNESS_DATA_PROCESSING
from src.security.pseudonymization import Pseudonymizer
from src.services.wellness_service import WellnessAuthorizationError

from .schemas import (
    AlertSummary,
    ContributingFactor,
    InterventionSummary,
    PersonnelReference,
    RecommendationSummary,
    ReportMetadata,
    RiskObservation,
    RiskSummary,
    RiskTrend,
    WelfareReportDTO,
    WellnessSummary,
)


class WelfareReportAuthorizationError(PermissionError):
    """Raised when a caller cannot receive an individual welfare report."""


class WelfareReportValidationError(ValueError):
    """Raised when a report request is invalid."""


class WelfareReportGenerationError(RuntimeError):
    """Raised when a current SURAKSHAI source cannot provide report data."""


class WelfareReportService:
    """Collect current service outputs and construct a minimized report DTO."""

    ALLOWED_ROLES = {'PERSONNEL', 'WELFARE_OFFICER', 'ADMIN'}
    OPERATIONAL_FIELDS = (
        'duty_hours_7d_avg', 'duty_hours_30d_avg', 'night_duty_frequency_7d',
        'night_duty_frequency_30d', 'workload_7d_avg', 'workload_30d_avg',
        'workload_trend', 'current_consecutive_duty_days',
        'max_consecutive_duty_days_30d', 'leave_days_30d', 'leave_episodes_30d',
        'current_deployment_duration_days', 'deployment_days_30d',
        'deployments_30d', 'training_hours_30d', 'training_sessions_30d',
        'transfers_30d', 'duty_hours_trend', 'night_duty_trend',
        'task_count_30d_avg', 'high_workload_days_30d',
        'workload_duty_hours_30d_avg', 'current_deployment_intensity',
    )
    WELLNESS_FIELDS = ('sleep_quality', 'fatigue_level', 'perceived_stress', 'mood_wellbeing')

    def __init__(self, *, feature_service, risk_service, wellness_service,
                 recommendation_service, workflow_service, consent_service,
                 pseudonymizer=None, audit_service=None):
        self.feature_service = feature_service
        self.risk_service = risk_service
        self.wellness_service = wellness_service
        self.recommendation_service = recommendation_service
        self.workflow_service = workflow_service
        self.consent_service = consent_service
        self.pseudonymizer = pseudonymizer or Pseudonymizer()
        self.audit_service = audit_service or get_audit_service()

    @staticmethod
    def _reference_date(value):
        if value is None:
            return date.today().isoformat()
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise WelfareReportValidationError('reference_date must use YYYY-MM-DD format') from error
        if parsed.isoformat() != value or parsed > date.today():
            raise WelfareReportValidationError('reference_date must be a valid non-future YYYY-MM-DD date')
        return parsed.isoformat()

    @staticmethod
    def _authorize(personnel_id, identity):
        identity = identity or {}
        role = identity.get('role')
        if role not in WelfareReportService.ALLOWED_ROLES:
            raise WelfareReportAuthorizationError('Forbidden')
        if role == 'PERSONNEL' and identity.get('personnel_id') != personnel_id:
            raise WelfareReportAuthorizationError('Forbidden')
        return identity

    def generate(self, personnel_id, *, reference_date=None, identity=None):
        identity = self._authorize(personnel_id, identity)
        reference_date = self._reference_date(reference_date)
        try:
            risk = self.risk_service.explain_for_personnel(
                personnel_id, reference_date=reference_date, identity=identity,
            )
            features = self.feature_service.compute(
                personnel_id, reference_date=reference_date, identity=identity,
            )
            recommendations = self.recommendation_service.recommend_for_personnel(
                personnel_id, reference_date=reference_date, identity=identity,
            )
            alerts = self.workflow_service.list_alerts(personnel_id)
            interventions = self._interventions(alerts)
            wellness = self._wellness(personnel_id, identity, reference_date)
            report = self._build_dto(
                personnel_id, reference_date, risk, features, recommendations,
                alerts, interventions, wellness,
            )
            self.audit_service.record_event(
                actor_user_id=identity.get('user_id'),
                actor_role=identity.get('role'),
                action='WELFARE_REPORT_GENERATED',
                resource_type='welfare_report',
                resource_id=report.metadata.report_id,
                result=RESULT_SUCCESS,
                source='welfare_report_service',
                purpose='Generate privacy-preserving welfare report',
                pseudonymous_subject=report.personnel.pseudonymous_reference,
                reference_date=reference_date,
                model_version=report.metadata.model_version,
            )
            return report
        except (WelfareReportAuthorizationError, WelfareReportValidationError):
            raise
        except Exception as error:
            self.audit_service.record_event(
                actor_user_id=identity.get('user_id'),
                actor_role=identity.get('role'),
                action='WELFARE_REPORT_GENERATED',
                resource_type='welfare_report',
                result=RESULT_FAILURE,
                source='welfare_report_service',
                purpose='Generate privacy-preserving welfare report',
            )
            raise WelfareReportGenerationError('Welfare report data is currently unavailable') from error

    def _wellness(self, personnel_id, identity, reference_date):
        try:
            if identity.get('role') == 'PERSONNEL':
                self.consent_service.link_personnel_identity(identity.get('user_id'), personnel_id)
            if not self.consent_service.has_personnel_consent(personnel_id, CONSENT_WELLNESS_DATA_PROCESSING):
                return WellnessSummary(False, 'Voluntary wellness data unavailable under current consent.')
            if identity.get('role') == 'PERSONNEL':
                assessments = self.wellness_service.list_for_self(identity, end_date=reference_date)
            else:
                assessments = self.wellness_service.list_for_authorized_personnel(personnel_id, identity, end_date=reference_date)
            latest = assessments[0] if assessments else None
            if latest is None:
                return WellnessSummary(False, 'Voluntary wellness data unavailable under current consent.')
            return WellnessSummary(
                True,
                'Authorized voluntary wellness data.',
                assessment_date=latest.get('assessment_date'),
                values={field: latest.get(field) for field in self.WELLNESS_FIELDS if field in latest},
            )
        except WellnessAuthorizationError:
            return WellnessSummary(False, 'Voluntary wellness data unavailable under current consent.')

    def _interventions(self, alerts):
        repository = getattr(self.workflow_service, 'interventions', None)
        if repository is None:
            return []
        result = []
        for alert in alerts or []:
            for item in repository.list_for_alert(alert.get('alert_id')):
                result.append(item)
        return result

    def _risk_trend(self, personnel_id, reference_date):
        repository = getattr(self.workflow_service, 'predictions', None)
        if repository is None:
            return RiskTrend()
        observations = repository.list_recent(
            personnel_id,
            start_date=(date.fromisoformat(reference_date) - timedelta(days=29)).isoformat(),
            end_date=reference_date,
        )
        rows = tuple(RiskObservation(item.get('reference_date'), item.get('risk_category'), item.get('severity')) for item in observations if item.get('reference_date') and item.get('risk_category'))
        if not rows:
            return RiskTrend()
        latest_7 = [item.risk_category for item in rows if item.reference_date >= (date.fromisoformat(reference_date) - timedelta(days=6)).isoformat()]
        latest_30 = [item.risk_category for item in rows]
        return RiskTrend(
            rows,
            trend_7d=', '.join(latest_7) if latest_7 else 'No observations in the last 7 days.',
            trend_30d=', '.join(latest_30) if latest_30 else 'No observations in the last 30 days.',
            persistence='Repeated observations are available for human review.' if len(set(latest_30)) < len(latest_30) else 'No repeated category pattern established.',
        )

    def _build_dto(self, personnel_id, reference_date, risk, features, recommendations, alerts, interventions, wellness):
        risk_category = risk.get('risk_category')
        if risk_category not in {'LOW', 'ELEVATED', 'HIGH'}:
            raise WelfareReportGenerationError('Risk output is not canonical')
        data_mode = risk.get('data_mode', 'OPERATIONAL_ONLY')
        if data_mode == 'OPERATIONAL_AND_WELLNESS':
            data_mode = 'OPERATIONAL_PLUS_WELLNESS'
        factors = []
        for item in (risk.get('explanation') or {}).get('top_contributors', []):
            feature = item.get('feature')
            if not feature or feature not in features:
                continue
            magnitude = float(item.get('shap_value', 0.0))
            factors.append(ContributingFactor(
                feature=feature,
                label=str(item.get('label', feature)),
                value=features.get(feature),
                direction=str(item.get('direction', 'not specified')),
                magnitude=magnitude,
                explanation='Model contribution for this feature; not causal evidence.',
            ))
        safe_features = {field: features.get(field) for field in self.OPERATIONAL_FIELDS if field in features}
        safe_alerts = tuple(AlertSummary(
            alert_reference=str(item.get('alert_id', 'unavailable')),
            severity=item.get('severity'),
            created_at=item.get('created_at'),
            status=item.get('status'),
            acknowledged=bool(item.get('acknowledged_at')),
            reviewed=bool(item.get('reviewed_at')),
            follow_up=item.get('scheduled_follow_up'),
            resolution=item.get('resolution_type'),
        ) for item in alerts or [])
        safe_interventions = tuple(InterventionSummary(
            intervention_type=item.get('action_type'),
            created_at=item.get('created_at'),
            status=item.get('status'),
            scheduled_follow_up=item.get('scheduled_follow_up'),
            outcome_category=item.get('outcome_category'),
        ) for item in interventions or [])
        safe_recommendations = tuple(RecommendationSummary(
            category=item.get('category', 'GENERAL'),
            action=str(item.get('action', '')),
            priority=item.get('priority'),
            rationale=str(item.get('rationale', '')),
            sources=tuple(str(source) for source in item.get('sources', [])),
        ) for item in (recommendations.get('recommendations') or []))
        return WelfareReportDTO(
            metadata=ReportMetadata(
                report_id=f'rpt-{uuid4()}',
                generated_at=datetime.now(timezone.utc).isoformat(),
                reference_date=reference_date,
                coverage_period=f'Operational feature coverage through {reference_date}; 30-day windows where supported.',
                model_version=risk.get('model_version', 'surakshai-risk-v0.1'),
                data_mode=data_mode,
            ),
            personnel=PersonnelReference(self.pseudonymizer.pseudonymize(personnel_id)),
            risk=RiskSummary(
                risk_category=risk_category,
                model=risk.get('model_version', 'surakshai-risk-v0.1'),
                data_mode=data_mode,
                disclaimer='This risk category is an AI decision-support output and is not a medical diagnosis, disciplinary classification, or fitness determination.',
            ),
            trend=self._risk_trend(personnel_id, reference_date),
            factors=tuple(factors),
            operational_summary=safe_features,
            wellness=wellness,
            recommendations=safe_recommendations,
            alerts=safe_alerts,
            interventions=safe_interventions,
            privacy_notice=(
                'This report contains privacy-sensitive welfare information and is restricted to authorized use.',
                'Risk predictions and explanations are AI decision-support outputs. They are not medical diagnoses, disciplinary decisions, or fitness determinations.',
                'Voluntary wellness information is shown only where valid consent permits its use.',
            ),
        )
