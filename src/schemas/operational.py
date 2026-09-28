"""Validation for raw operational personnel records."""

from datetime import date, datetime, timezone
from uuid import uuid4

from src.schemas.personnel import validate_personnel_identifier
from src.schemas.seed import validate_seed_batch_id


INTENSITIES = {'LOW', 'MODERATE', 'HIGH', 'VERY_HIGH'}
SHIFT_TYPES = {'DAY', 'NIGHT', 'ROTATING'}
POSTING_CATEGORIES = {'FIELD', 'TRAINING', 'ADMIN', 'SUPPORT', 'OTHER'}

OPERATIONAL_SCHEMAS = {
    'duty_records': {
        'required': {'personnel_id', 'date', 'duty_hours', 'night_duty', 'shift_type', 'operational_intensity'},
        'date_field': 'date',
    },
    'leave_records': {
        'required': {'personnel_id', 'leave_type', 'start_date', 'end_date', 'duration_days'},
        'date_field': 'start_date',
    },
    'deployment_records': {
        'required': {'personnel_id', 'deployment_type', 'start_date', 'end_date', 'operational_intensity', 'location_category'},
        'date_field': 'start_date',
    },
    'transfer_records': {
        'required': {'personnel_id', 'transfer_date', 'previous_posting', 'new_posting', 'reason_category'},
        'date_field': 'transfer_date',
    },
    'training_records': {
        'required': {'personnel_id', 'training_type', 'start_date', 'end_date', 'training_hours', 'intensity'},
        'date_field': 'start_date',
    },
    'workload_records': {
        'required': {'personnel_id', 'date', 'workload_score', 'duty_hours', 'task_count', 'night_duty', 'consecutive_duty_days'},
        'date_field': 'date',
    },
}

_COMMON_FIELDS = {'record_id', 'created_at', 'seed_batch_id'}
_ENUMS = {
    'shift_type': SHIFT_TYPES,
    'operational_intensity': INTENSITIES,
    'intensity': INTENSITIES,
    'leave_type': {'ANNUAL', 'CASUAL', 'COMPENSATORY', 'UNPAID', 'OTHER'},
    'deployment_type': {'FIELD', 'TRAINING', 'SUPPORT', 'PEACEKEEPING', 'OTHER'},
    'location_category': {'DOMESTIC', 'BORDER', 'REMOTE', 'URBAN', 'TRAINING_AREA', 'OTHER'},
    'previous_posting': POSTING_CATEGORIES,
    'new_posting': POSTING_CATEGORIES,
    'reason_category': {'ROTATION', 'CAREER_PROGRESSION', 'UNIT_REORGANIZATION', 'PERSONAL_REQUEST', 'OPERATIONAL_REQUIREMENT', 'OTHER'},
    'training_type': {'FIELD_EXERCISE', 'SAFETY', 'LEADERSHIP', 'TECHNICAL', 'FITNESS', 'OTHER'},
}
_NUMERIC_BOUNDS = {
    'duty_hours': (0, 24),
    'training_hours': (0, 1000),
    'workload_score': (0, 100),
    'duration_days': (1, 366),
    'task_count': (0, 500),
    'consecutive_duty_days': (0, 90),
}
_BOOLEAN_FIELDS = {'night_duty'}


def _parse_date(value, field):
    if not isinstance(value, str):
        raise ValueError(f'{field} must be an ISO date string')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f'{field} must be an ISO date string') from error
    if parsed.isoformat() != value:
        raise ValueError(f'{field} must use YYYY-MM-DD format')
    return parsed


def normalize_operational_record(domain, payload, *, record_id=None, created_at=None):
    if domain not in OPERATIONAL_SCHEMAS:
        raise ValueError('Unknown operational record domain')
    if not isinstance(payload, dict):
        raise ValueError('Operational record must be an object')

    schema = OPERATIONAL_SCHEMAS[domain]
    required = schema['required']
    allowed = required | schema.get('optional', set()) | _COMMON_FIELDS
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f'Unsupported operational field(s): {", ".join(sorted(unknown))}')
    missing = required - set(payload)
    if missing:
        raise ValueError(f'Missing required field(s): {", ".join(sorted(missing))}')

    record = dict(payload)
    if 'seed_batch_id' in record:
        record['seed_batch_id'] = validate_seed_batch_id(record['seed_batch_id'])
    record['personnel_id'] = validate_personnel_identifier(record['personnel_id'])
    date_fields = {'date', 'start_date', 'end_date', 'transfer_date'}
    parsed_dates = {}
    for field in date_fields & set(record):
        value = record[field]
        if value is None and field == 'end_date' and domain == 'deployment_records':
            continue
        parsed_dates[field] = _parse_date(value, field)

    for field, choices in _ENUMS.items():
        if field in record and record[field] not in choices:
            raise ValueError(f'{field} must be one of {sorted(choices)}')
    for field, (minimum, maximum) in _NUMERIC_BOUNDS.items():
        if field not in record:
            continue
        value = record[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
            raise ValueError(f'{field} must be between {minimum} and {maximum}')
        if field in {'duration_days', 'task_count', 'consecutive_duty_days'} and not isinstance(value, int):
            raise ValueError(f'{field} must be an integer')
    for field in _BOOLEAN_FIELDS & set(record):
        if not isinstance(record[field], bool):
            raise ValueError(f'{field} must be a boolean')

    if domain == 'leave_records':
        if parsed_dates['end_date'] < parsed_dates['start_date']:
            raise ValueError('end_date must not precede start_date')
        expected_days = (parsed_dates['end_date'] - parsed_dates['start_date']).days + 1
        if record['duration_days'] != expected_days:
            raise ValueError('duration_days must match the inclusive leave date range')
    if domain == 'training_records' and parsed_dates['end_date'] < parsed_dates['start_date']:
        raise ValueError('end_date must not precede start_date')
    if domain == 'deployment_records' and record.get('end_date') is not None and parsed_dates['end_date'] < parsed_dates['start_date']:
        raise ValueError('end_date must not precede start_date')
    if domain == 'duty_records' and record['night_duty'] and record['shift_type'] == 'DAY':
        raise ValueError('night_duty cannot be true for a DAY shift')

    record['record_id'] = record_id or record.get('record_id') or str(uuid4())
    record['created_at'] = created_at or record.get('created_at') or datetime.now(timezone.utc).isoformat()
    return record