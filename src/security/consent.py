"""Durable, default-deny consent management for voluntary processing."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.db.repositories.consent_repository import ConsentRepository
from src.schemas.seed import validate_seed_batch_id
from src.security.audit import RESULT_SUCCESS, get_audit_service


CONSENT_WELLNESS_DATA_PROCESSING = 'WELLNESS_DATA_PROCESSING'
CONSENT_BIOMETRIC_DATA_PROCESSING = 'BIOMETRIC_DATA_PROCESSING'
CONSENT_RECOMMENDATION_PROCESSING = 'RECOMMENDATION_PROCESSING'
CONSENT_DATA_SHARING = 'DATA_SHARING'

CONSENT_TYPES = (
    CONSENT_WELLNESS_DATA_PROCESSING,
    CONSENT_BIOMETRIC_DATA_PROCESSING,
    CONSENT_RECOMMENDATION_PROCESSING,
    CONSENT_DATA_SHARING,
)


class ConsentService:
    """Authoritative consent service backed by immutable MongoDB transitions."""

    def __init__(self, repository=None, audit_service=None):
        self.repository = repository or ConsentRepository()
        self.audit_service = audit_service or get_audit_service()
        # Compatibility maps retain trusted request identity links only; they
        # are never used as the consent source of truth.
        self._personnel_user_ids = {}
        self._user_personnel_ids = {}
        self._records = {}

    def link_personnel_identity(self, user_id, personnel_id):
        if not user_id or not personnel_id:
            return
        linked_user = self._personnel_user_ids.get(personnel_id)
        linked_personnel = self._user_personnel_ids.get(user_id)
        if linked_user not in (None, user_id) or linked_personnel not in (None, personnel_id):
            raise ValueError('Authenticated personnel identity is already linked')
        self._personnel_user_ids[personnel_id] = user_id
        self._user_personnel_ids[user_id] = personnel_id

    def _validate_type(self, consent_type):
        if consent_type not in CONSENT_TYPES:
            raise ValueError('Unknown consent type')

    def _resolve_personnel_id(self, user_id, personnel_id=None):
        return personnel_id or self._user_personnel_ids.get(user_id)

    def _set_state(self, user_id, consent_type, granted, *, personnel_id=None,
                   source='PERSONNEL', seed_batch_id=None):
        self._validate_type(consent_type)
        if not isinstance(granted, bool):
            raise ValueError('granted must be a boolean')
        personnel_id = self._resolve_personnel_id(user_id, personnel_id)
        if not personnel_id:
            raise ValueError('Authenticated personnel identity is required')
        now_value = datetime.now(timezone.utc)
        previous = self.repository.get_current_consent(
            personnel_id=personnel_id,
            consent_type=consent_type,
        )
        if previous and previous.get('updated_at'):
            previous_value = datetime.fromisoformat(previous['updated_at'])
            if now_value <= previous_value:
                now_value = previous_value + timedelta(microseconds=1)
        now = now_value.isoformat()
        record = {
            'consent_id': f'consent-{uuid4()}',
            'personnel_id': personnel_id,
            'consent_type': consent_type,
            'status': 'GRANTED' if granted else 'REVOKED',
            'granted_at': now if granted else None,
            'revoked_at': None if granted else now,
            'updated_at': now,
            'source': source,
            'actor_user_id': user_id,
        }
        if seed_batch_id is not None:
            record['seed_batch_id'] = validate_seed_batch_id(seed_batch_id)
        self.repository.append_transition(record)
        self.audit_service.record_event(
            actor_user_id=user_id,
            action='CONSENT_GRANTED' if granted else 'CONSENT_REVOKED',
            resource_type='consent',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='consent_service',
            purpose='Record personnel consent decision',
            consent_type=consent_type,
            granted=granted,
        )
        return self._response_record(record)

    @staticmethod
    def _response_record(record):
        return {
            'consent_type': record['consent_type'],
            'granted': record.get('status') == 'GRANTED',
            'timestamp': record.get('updated_at'),
        }

    def get_user_consents(self, user_id, personnel_id=None):
        current = self.repository.get_current_consents(
            personnel_id=personnel_id,
            actor_user_id=None if personnel_id else user_id,
        )
        result = []
        for consent_type in CONSENT_TYPES:
            record = current.get(consent_type)
            result.append(self._response_record(record) if record else {
                'consent_type': consent_type,
                'granted': False,
                'timestamp': None,
            })
        self.audit_service.record_event(
            actor_user_id=user_id,
            action='CONSENT_VIEW',
            resource_type='consent',
            result=RESULT_SUCCESS,
            source='consent_service',
            purpose='View current consent state',
        )
        return result

    def grant_consent(self, user_id, consent_type, personnel_id=None, *, seed_batch_id=None):
        return self._set_state(
            user_id, consent_type, True, personnel_id=personnel_id,
            seed_batch_id=seed_batch_id,
        )

    def revoke_consent(self, user_id, consent_type, personnel_id=None, *, seed_batch_id=None):
        return self._set_state(
            user_id, consent_type, False, personnel_id=personnel_id,
            seed_batch_id=seed_batch_id,
        )

    def set_consent(self, user_id, consent_type, granted, personnel_id=None, *, seed_batch_id=None):
        return self._set_state(
            user_id, consent_type, granted, personnel_id=personnel_id,
            seed_batch_id=seed_batch_id,
        )

    def has_consent(self, user_id, consent_type):
        self._validate_type(consent_type)
        record = self.repository.get_current_consent(
            actor_user_id=user_id,
            consent_type=consent_type,
        )
        return bool(record and record.get('status') == 'GRANTED')

    def has_personnel_consent(self, personnel_id, consent_type):
        self._validate_type(consent_type)
        record = self.repository.get_current_consent(
            personnel_id=personnel_id,
            consent_type=consent_type,
        )
        return bool(record and record.get('status') == 'GRANTED')

    def get_history(self, personnel_id, consent_type=None):
        if consent_type is not None:
            self._validate_type(consent_type)
        return self.repository.get_history(personnel_id=personnel_id, consent_type=consent_type)
