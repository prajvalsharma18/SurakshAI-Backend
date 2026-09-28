"""Consent-gated operations for voluntary personnel self-assessments."""

from datetime import date

from src.schemas.wellness import normalize_wellness_assessment
from src.schemas.seed import validate_seed_batch_id
from src.security.audit import RESULT_DENIED, RESULT_SUCCESS, get_audit_service
from src.security.consent import CONSENT_WELLNESS_DATA_PROCESSING
from src.security.permissions import (
    PERMISSION_SUBMIT_WELLNESS,
    PERMISSION_VIEW_OWN_WELLNESS,
    PERMISSION_VIEW_WELLNESS_RECORDS,
    ROLE_ADMIN,
    ROLE_PERSONNEL,
    ROLE_WELFARE_OFFICER,
)
from src.security.rbac import has_permission


class WellnessAuthorizationError(PermissionError):
    """Raised when a caller lacks wellness access or current consent is absent."""


class WellnessService:
    def __init__(self, repository, consent_service, audit_service=None):
        self.repository = repository
        self.consent_service = consent_service
        self.audit_service = audit_service or get_audit_service()

    def _audit(self, action, identity, *, personnel_id=None, assessment_id=None, result=RESULT_SUCCESS, purpose=None):
        self.audit_service.record_event(
            actor_user_id=(identity or {}).get('user_id'),
            actor_role=(identity or {}).get('role'),
            action=action,
            resource_type='wellness_assessment',
            resource_id=assessment_id or personnel_id,
            result=result,
            source='wellness_service',
            purpose=purpose or 'Voluntary wellness assessment access',
        )

    def _deny(self, identity, personnel_id, reason):
        self._audit(
            'WELLNESS_ACCESS_DENIED',
            identity,
            personnel_id=personnel_id,
            result=RESULT_DENIED,
            purpose=reason,
        )
        raise WellnessAuthorizationError(reason)

    def _require_self(self, identity, permission):
        if (
            not identity
            or identity.get('role') != ROLE_PERSONNEL
            or not identity.get('personnel_id')
            or not has_permission(identity, permission)
        ):
            self._deny(identity, (identity or {}).get('personnel_id'), 'Forbidden')
        return identity['personnel_id']

    def _require_self_consent(self, identity, personnel_id):
        if not self.consent_service.has_consent(identity['user_id'], CONSENT_WELLNESS_DATA_PROCESSING):
            self._deny(identity, personnel_id, 'WELLNESS_DATA_PROCESSING consent is required')

    @staticmethod
    def _validate_date_filter(value, field):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f'{field} must use YYYY-MM-DD format')
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f'{field} must use YYYY-MM-DD format') from error
        if parsed.isoformat() != value:
            raise ValueError(f'{field} must use YYYY-MM-DD format')
        return parsed.isoformat()

    def _validated_range(self, start_date, end_date):
        start_date = self._validate_date_filter(start_date, 'start_date')
        end_date = self._validate_date_filter(end_date, 'end_date')
        if start_date and end_date and start_date > end_date:
            raise ValueError('start_date must not follow end_date')
        return start_date, end_date

    def create_for_self(self, payload, identity, *, seed_batch_id=None):
        personnel_id = self._require_self(identity, PERMISSION_SUBMIT_WELLNESS)
        self._require_self_consent(identity, personnel_id)
        assessment = normalize_wellness_assessment(payload, personnel_id)
        if seed_batch_id is not None:
            assessment['seed_batch_id'] = validate_seed_batch_id(seed_batch_id)
        created = self.repository.create(assessment)
        self._audit('WELLNESS_CREATE', identity, assessment_id=created['assessment_id'])
        return created

    def list_for_self(self, identity, *, start_date=None, end_date=None):
        personnel_id = self._require_self(identity, PERMISSION_VIEW_OWN_WELLNESS)
        self._require_self_consent(identity, personnel_id)
        start_date, end_date = self._validated_range(start_date, end_date)
        assessments = self.repository.list_for_personnel(
            personnel_id,
            start_date=start_date,
            end_date=end_date,
        )
        self._audit('WELLNESS_VIEW', identity, personnel_id=personnel_id)
        return assessments

    def get_for_self(self, assessment_id, identity):
        personnel_id = self._require_self(identity, PERMISSION_VIEW_OWN_WELLNESS)
        self._require_self_consent(identity, personnel_id)
        assessment = self.repository.get_owned(personnel_id, assessment_id)
        if assessment is not None:
            self._audit('WELLNESS_VIEW', identity, assessment_id=assessment_id)
        else:
            self._audit(
                'WELLNESS_ACCESS_DENIED',
                identity,
                assessment_id=assessment_id,
                result=RESULT_DENIED,
                purpose='Assessment not found for authenticated personnel',
            )
        return assessment

    def delete_for_self(self, assessment_id, identity):
        personnel_id = self._require_self(identity, PERMISSION_VIEW_OWN_WELLNESS)
        deleted = self.repository.delete_owned(personnel_id, assessment_id)
        if deleted:
            self._audit(
                'WELLNESS_DELETE',
                identity,
                assessment_id=assessment_id,
                purpose='Personnel withdrawal of self-assessment',
            )
        else:
            self._audit(
                'WELLNESS_ACCESS_DENIED',
                identity,
                assessment_id=assessment_id,
                result=RESULT_DENIED,
                purpose='Assessment not found for authenticated personnel',
            )
        return deleted

    def list_for_authorized_personnel(self, personnel_id, identity, *, start_date=None, end_date=None):
        role = (identity or {}).get('role')
        if (
            role not in {ROLE_WELFARE_OFFICER, ROLE_ADMIN}
            or not has_permission(identity, PERMISSION_VIEW_WELLNESS_RECORDS)
        ):
            self._deny(identity, personnel_id, 'Forbidden')
        if not self.consent_service.has_personnel_consent(
            personnel_id,
            CONSENT_WELLNESS_DATA_PROCESSING,
        ):
            self._deny(identity, personnel_id, 'Personnel consent for wellness processing is required')
        start_date, end_date = self._validated_range(start_date, end_date)
        assessments = self.repository.list_for_personnel(
            personnel_id,
            start_date=start_date,
            end_date=end_date,
        )
        self._audit('WELLNESS_VIEW', identity, personnel_id=personnel_id)
        return assessments