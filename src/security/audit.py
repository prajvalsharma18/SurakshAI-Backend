"""Development-only append-only audit event storage."""

from datetime import datetime, timezone
import re
from uuid import uuid4


RESULT_SUCCESS = 'SUCCESS'
RESULT_FAILURE = 'FAILURE'
RESULT_DENIED = 'DENIED'
AUDIT_RESULTS = {RESULT_SUCCESS, RESULT_FAILURE, RESULT_DENIED}
_SENSITIVE_METADATA_MARKERS = (
    'password', 'token', 'secret', 'api_key', 'authorization', 'credential',
    'payload', 'response', 'prompt', 'shap', 'wellness', 'mongo', 'uri',
    'header', 'feature_vector', 'embedding',
)
_SENSITIVE_VALUE_PATTERNS = re.compile(r'(?i)(mongodb(?:\+srv)?://|bearer\s+|-----BEGIN [A-Z ]*PRIVATE KEY-----)')


def _safe_extra_metadata(metadata):
    safe = {}
    for key, value in metadata.items():
        normalized_key = str(key).lower()
        if any(marker in normalized_key for marker in _SENSITIVE_METADATA_MARKERS):
            continue
        if not isinstance(value, (str, int, float, bool)) and value is not None:
            continue
        if isinstance(value, str) and _SENSITIVE_VALUE_PATTERNS.search(value):
            continue
        safe[key] = value
    return safe


class AuditService:
    """Keep safe traceability metadata in an append-only in-memory store."""

    def __init__(self):
        self._events = []

    def record_event(
        self,
        *,
        actor_user_id=None,
        actor_role=None,
        action,
        resource_type=None,
        resource_id=None,
        result,
        source=None,
        purpose=None,
        attempted_username=None,
        consent_type=None,
        granted=None,
        **extra_metadata,
    ):
        if result not in AUDIT_RESULTS:
            raise ValueError('Invalid audit result')
        event = {
            'event_id': str(uuid4()),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'actor_user_id': actor_user_id,
            'actor_role': actor_role,
            'action': action,
            'resource_type': resource_type,
            'resource_id': resource_id,
            'result': result,
            'source': source,
            'purpose': purpose,
        }
        if (
            isinstance(attempted_username, str)
            and len(attempted_username) <= 64
            and not _SENSITIVE_VALUE_PATTERNS.search(attempted_username)
        ):
            event['attempted_username'] = attempted_username
        if consent_type is not None:
            event['consent_type'] = consent_type
        if granted is not None:
            event['granted'] = granted
        event.update(_safe_extra_metadata(extra_metadata))
        self._events.append(event)
        return event.copy()

    def list_events(self):
        return [event.copy() for event in self._events]


_audit_service = AuditService()


def get_audit_service():
    return _audit_service
