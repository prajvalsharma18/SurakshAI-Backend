"""Authorized, privacy-minimized access to persisted risk predictions."""

from datetime import date

from src.schemas.risk import RiskHistoryResponse, RiskSummaryResponse
from src.security.audit import RESULT_SUCCESS, get_audit_service


class RiskHistoryService:
    def __init__(self, repository, audit_service=None):
        self.repository = repository
        self.audit_service = audit_service or get_audit_service()

    @staticmethod
    def _authorize(identity, personnel_id):
        identity = identity or {}
        role = identity.get('role')
        if role == 'PERSONNEL' and identity.get('personnel_id') != personnel_id:
            raise PermissionError('Forbidden')
        if role == 'COMMANDER' or role not in {'PERSONNEL', 'WELFARE_OFFICER', 'ADMIN'}:
            raise PermissionError('Forbidden')
        return identity

    @staticmethod
    def _validate_date(value, field):
        if value is None:
            return None
        try:
            parsed = date.fromisoformat(value)
        except (TypeError, ValueError) as error:
            raise ValueError(f'{field} must use YYYY-MM-DD format') from error
        if parsed.isoformat() != value:
            raise ValueError(f'{field} must use YYYY-MM-DD format')
        return parsed.isoformat()

    @classmethod
    def _date_range(cls, reference_date_from, reference_date_to):
        start = cls._validate_date(reference_date_from, 'reference_date_from')
        end = cls._validate_date(reference_date_to, 'reference_date_to')
        if start and end and start > end:
            raise ValueError('reference_date_from must not be after reference_date_to')
        return start, end

    @staticmethod
    def _history_item(record):
        allowed = ('reference_date', 'risk_category', 'model_version', 'data_mode', 'probabilities', 'created_at')
        return {field: record[field] for field in allowed if field in record}

    def get_history(self, identity, personnel_id, *, page=1, page_size=25, reference_date_from=None, reference_date_to=None) -> RiskHistoryResponse:
        self._authorize(identity, personnel_id)
        if page < 1:
            raise ValueError('page must be at least 1')
        if page_size < 1 or page_size > 100:
            raise ValueError('page_size must be between 1 and 100')
        start, end = self._date_range(reference_date_from, reference_date_to)
        records, total = self.repository.get_history(
            personnel_id,
            page=page,
            page_size=page_size,
            reference_date_from=start,
            reference_date_to=end,
        )
        self.audit_service.record_event(
            actor_user_id=(identity or {}).get('user_id'),
            actor_role=(identity or {}).get('role'),
            action='RISK_HISTORY_VIEW',
            resource_type='personnel_risk_history',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='risk_history_service.get_history',
            purpose='View persisted personnel risk history',
            page=page,
            page_size=page_size,
        )
        return {'items': [self._history_item(record) for record in records], 'page': page, 'page_size': page_size, 'total': total}

    def get_summary(self, identity, *, reference_date_from=None, reference_date_to=None) -> RiskSummaryResponse:
        identity = identity or {}
        if identity.get('role') not in {'WELFARE_OFFICER', 'ADMIN'}:
            raise PermissionError('Forbidden')
        start, end = self._date_range(reference_date_from, reference_date_to)
        summary = self.repository.get_summary(reference_date_from=start, reference_date_to=end)
        distribution = {category: int(summary.get('risk_distribution', {}).get(category, 0)) for category in ('LOW', 'ELEVATED', 'HIGH')}
        self.audit_service.record_event(
            actor_user_id=identity.get('user_id'),
            actor_role=identity.get('role'),
            action='WELFARE_RISK_SUMMARY_VIEW',
            resource_type='welfare_risk_summary',
            result=RESULT_SUCCESS,
            source='risk_history_service.get_summary',
            purpose='View aggregate persisted welfare risk summary',
        )
        return {
            'total_personnel': int(summary.get('total_personnel', 0)),
            'risk_distribution': distribution,
            'latest_prediction_date': summary.get('latest_prediction_date'),
        }
