"""Validation for voluntary, structured wellness self-assessments."""

from datetime import date, datetime, timezone
from uuid import uuid4

from src.schemas.personnel import validate_personnel_identifier


WELLNESS_SCALE_FIELDS = (
    'sleep_quality',
    'fatigue_level',
    'perceived_stress',
    'mood_wellbeing',
)
WELLNESS_FIELDS = set(WELLNESS_SCALE_FIELDS)
WELLNESS_SUBMISSION_FIELDS = WELLNESS_FIELDS | {'assessment_date'}


def normalize_wellness_assessment(
    payload,
    personnel_id,
    *,
    assessment_id=None,
    submitted_at=None,
    today=None,
):
    if not isinstance(payload, dict):
        raise ValueError('Wellness assessment must be a JSON object')
    unknown = set(payload) - WELLNESS_SUBMISSION_FIELDS
    if unknown:
        raise ValueError(f'Unsupported wellness field(s): {", ".join(sorted(unknown))}')
    missing = WELLNESS_SUBMISSION_FIELDS - set(payload)
    if missing:
        raise ValueError(f'Missing required wellness field(s): {", ".join(sorted(missing))}')

    personnel_id = validate_personnel_identifier(personnel_id)
    assessment_date = payload['assessment_date']
    if not isinstance(assessment_date, str):
        raise ValueError('assessment_date must use YYYY-MM-DD format')
    try:
        parsed_date = date.fromisoformat(assessment_date)
    except ValueError as error:
        raise ValueError('assessment_date must use YYYY-MM-DD format') from error
    if parsed_date.isoformat() != assessment_date:
        raise ValueError('assessment_date must use YYYY-MM-DD format')
    if parsed_date > (today or date.today()):
        raise ValueError('assessment_date cannot be in the future')

    values = {}
    for field in WELLNESS_SCALE_FIELDS:
        value = payload[field]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ValueError(f'{field} must be an integer from 1 through 5')
        values[field] = value

    return {
        'assessment_id': assessment_id or str(uuid4()),
        'personnel_id': personnel_id,
        'assessment_date': parsed_date.isoformat(),
        'submitted_at': submitted_at or datetime.now(timezone.utc).isoformat(),
        **values,
    }