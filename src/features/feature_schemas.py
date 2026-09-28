"""Validation rules for the Phase 3C longitudinal feature vector."""

import math
from datetime import date

from src.schemas.operational import INTENSITIES
from src.schemas.personnel import validate_personnel_identifier


HIGH_WORKLOAD_SCORE_THRESHOLD = 70.0

FEATURE_NUMERIC_FIELDS = {
    'duty_hours_7d_total': (0, 10000, True),
    'duty_hours_7d_avg': (0, 10000, True),
    'duty_hours_7d_max': (0, 10000, True),
    'night_duty_frequency_7d': (0, 1, True),
    'operational_intensity_7d_avg': (1, 4, True),
    'duty_hours_30d_total': (0, 10000, True),
    'duty_hours_30d_avg': (0, 10000, True),
    'duty_hours_30d_max': (0, 10000, True),
    'night_duty_frequency_30d': (0, 1, True),
    'operational_intensity_30d_avg': (1, 4, True),
    'workload_7d_avg': (0, 100, True),
    'workload_7d_max': (0, 100, True),
    'task_count_7d_avg': (0, 500, True),
    'task_count_7d_total': (0, 100000, True),
    'workload_duty_hours_7d_avg': (0, 24, True),
    'workload_30d_avg': (0, 100, True),
    'workload_30d_max': (0, 100, True),
    'task_count_30d_avg': (0, 500, True),
    'task_count_30d_total': (0, 100000, True),
    'workload_duty_hours_30d_avg': (0, 24, True),
    'training_hours_7d': (0, 100000, False),
    'training_hours_30d': (0, 100000, False),
    'training_intensity_30d_avg': (1, 4, True),
    'duty_hours_trend': (-10000, 10000, True),
    'workload_trend': (-100, 100, True),
    'night_duty_trend': (-1, 1, True),
}

FEATURE_INTEGER_FIELDS = {
    'duty_days_7d',
    'night_duty_days_7d',
    'duty_days_30d',
    'night_duty_days_30d',
    'current_consecutive_duty_days',
    'max_consecutive_duty_days_7d',
    'max_consecutive_duty_days_30d',
    'leave_episodes_30d',
    'leave_days_30d',
    'current_deployment_duration_days',
    'deployment_days_30d',
    'deployments_30d',
    'training_sessions_30d',
    'transfers_30d',
}
FEATURE_OPTIONAL_INTEGER_FIELDS = {
    'days_since_most_recent_completed_leave': (0, 100000),
    'days_since_most_recent_transfer': (0, 100000),
    'high_workload_days_7d': (0, 7),
    'high_workload_days_30d': (0, 30),
}
FEATURE_BOOLEAN_FIELDS = {
    'duty_data_available_7d',
    'duty_data_available_30d',
    'workload_data_available_7d',
    'workload_data_available_30d',
    'leave_data_available_30d',
    'recent_leave_period_30d',
    'deployment_data_available_30d',
    'training_data_available_7d',
    'training_data_available_30d',
    'transfer_data_available_30d',
}
FEATURE_VECTOR_FIELDS = {
    'personnel_id',
    'reference_date',
    'high_workload_score_threshold',
    'current_deployment_intensity',
} | set(FEATURE_NUMERIC_FIELDS) | FEATURE_INTEGER_FIELDS | set(FEATURE_OPTIONAL_INTEGER_FIELDS) | FEATURE_BOOLEAN_FIELDS


def validate_feature_vector(payload):
    if not isinstance(payload, dict):
        raise ValueError('Feature vector must be an object')
    missing = FEATURE_VECTOR_FIELDS - set(payload)
    unknown = set(payload) - FEATURE_VECTOR_FIELDS
    if missing or unknown:
        raise ValueError('Feature vector fields do not match the Phase 3C schema')

    result = dict(payload)
    result['personnel_id'] = validate_personnel_identifier(result['personnel_id'])
    reference_date = result['reference_date']
    if not isinstance(reference_date, str):
        raise ValueError('reference_date must use YYYY-MM-DD format')
    try:
        parsed_reference = date.fromisoformat(reference_date)
    except ValueError as error:
        raise ValueError('reference_date must use YYYY-MM-DD format') from error
    if parsed_reference.isoformat() != reference_date:
        raise ValueError('reference_date must use YYYY-MM-DD format')

    for field, (minimum, maximum, nullable) in FEATURE_NUMERIC_FIELDS.items():
        value = result[field]
        if value is None and nullable:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{field} must be a finite number')
        if not minimum <= value <= maximum:
            raise ValueError(f'{field} must be between {minimum} and {maximum}')
    for field in FEATURE_INTEGER_FIELDS:
        if isinstance(result[field], bool) or not isinstance(result[field], int) or result[field] < 0:
            raise ValueError(f'{field} must be a non-negative integer')
    for field, (minimum, maximum) in FEATURE_OPTIONAL_INTEGER_FIELDS.items():
        value = result[field]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum
        ):
            raise ValueError(f'{field} must be an integer between {minimum} and {maximum}, or None')
    for field in FEATURE_BOOLEAN_FIELDS:
        if not isinstance(result[field], bool):
            raise ValueError(f'{field} must be a boolean')
    if result['high_workload_score_threshold'] != HIGH_WORKLOAD_SCORE_THRESHOLD:
        raise ValueError('high_workload_score_threshold does not match the configured prototype threshold')
    intensity = result['current_deployment_intensity']
    if intensity is not None and (not isinstance(intensity, str) or intensity not in INTENSITIES):
        raise ValueError('current_deployment_intensity is invalid')
    return result