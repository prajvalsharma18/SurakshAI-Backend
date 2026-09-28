"""Adapter from Phase 3C/3D data to the canonical 31-feature SURAKSHAI model contract."""

from __future__ import annotations

import math
from typing import Any

import numpy as np

from src.ml.feature_schema import FEATURE_SOURCE_MAP, MODEL_FEATURES


CANONICAL_FIELD_ALIASES = {
    'duty_hours_7d_avg': ('duty_hours_7d_avg',),
    'duty_hours_30d_avg': ('duty_hours_30d_avg',),
    'night_duty_7d_count': ('night_duty_days_7d',),
    'night_duty_30d_count': ('night_duty_days_30d',),
    'night_duty_frequency_7d': ('night_duty_frequency_7d',),
    'night_duty_frequency_30d': ('night_duty_frequency_30d',),
    'workload_7d_avg': ('workload_7d_avg',),
    'workload_30d_avg': ('workload_30d_avg',),
    'workload_trend': ('workload_trend',),
    'current_consecutive_duty_days': ('current_consecutive_duty_days',),
    'max_consecutive_duty_days_30d': ('max_consecutive_duty_days_30d',),
    'days_since_most_recent_leave': ('days_since_most_recent_completed_leave',),
    'leave_days_30d': ('leave_days_30d',),
    'leave_episodes_30d': ('leave_episodes_30d',),
    'current_deployment_days': ('current_deployment_duration_days',),
    'deployment_days_30d': ('deployment_days_30d',),
    'deployment_count_30d': ('deployments_30d',),
    'training_hours_30d': ('training_hours_30d',),
    'training_session_count_30d': ('training_sessions_30d',),
    'transfer_count_30d': ('transfers_30d',),
    'duty_hours_trend': ('duty_hours_trend',),
    'night_duty_trend': ('night_duty_trend',),
    'average_tasks_30d': ('task_count_30d_avg',),
    'high_workload_days_30d': ('high_workload_days_30d',),
    'workload_duty_hours_mean_30d': ('workload_duty_hours_30d_avg',),
    'deployment_intensity_current': ('deployment_intensity_current', 'current_deployment_intensity'),
    'sleep_quality': ('sleep_quality',),
    'fatigue_level': ('fatigue_level',),
    'perceived_stress': ('perceived_stress',),
    'mood_wellbeing': ('mood_wellbeing',),
    'wellness_available': ('wellness_available',),
}


def _as_float(value):
    if value is None:
        return np.nan
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isnan(value):
            return np.nan
        return float(value)
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) else np.nan
    except (TypeError, ValueError):
        return np.nan


def _as_optional_float(value):
    if value is None:
        return np.nan
    return _as_float(value)


def _deployment_intensity_value(value):
    if value is None:
        return np.nan
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if math.isnan(numeric):
            return np.nan
        return numeric
    normalized = str(value).strip().upper()
    mapping = {'LOW': 1.0, 'MODERATE': 2.0, 'HIGH': 3.0, 'VERY_HIGH': 4.0}
    return mapping.get(normalized, np.nan)


def build_canonical_feature_vector(feature_payload, wellness_record=None):
    """Map a Phase 3C feature dictionary and optional wellness assessment into the exact canonical order."""
    if not isinstance(feature_payload, dict):
        raise ValueError('Feature payload must be a dictionary')

    features = {}
    for name in MODEL_FEATURES:
        features[name] = np.nan

    for canonical_name in MODEL_FEATURES:
        value = None
        for candidate_name in CANONICAL_FIELD_ALIASES.get(canonical_name, (canonical_name,)):
            if candidate_name in feature_payload and feature_payload.get(candidate_name) is not None:
                value = feature_payload.get(candidate_name)
                break
        if canonical_name == 'deployment_intensity_current':
            features[canonical_name] = _deployment_intensity_value(value)
        elif value is None:
            features[canonical_name] = np.nan
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            features[canonical_name] = float(value)
        else:
            features[canonical_name] = _as_float(value)

    if wellness_record:
        wellness_values = {
            'sleep_quality': wellness_record.get('sleep_quality'),
            'fatigue_level': wellness_record.get('fatigue_level'),
            'perceived_stress': wellness_record.get('perceived_stress'),
            'mood_wellbeing': wellness_record.get('mood_wellbeing'),
        }
        for key, value in wellness_values.items():
            if key in features:
                features[key] = _as_optional_float(value)
        features['wellness_available'] = 1
    else:
        features['wellness_available'] = 0
        for key in ('sleep_quality', 'fatigue_level', 'perceived_stress', 'mood_wellbeing'):
            features[key] = np.nan

    required = set(MODEL_FEATURES)
    missing = sorted(required - set(features))
    if missing:
        raise ValueError(f'Missing required feature fields: {missing}')

    ordered = {name: features[name] for name in MODEL_FEATURES}
    for field, metadata in FEATURE_SOURCE_MAP.items():
        if field not in ordered:
            raise ValueError(f'Canonical feature {field} missing from adapter output')
        if metadata is None:
            raise ValueError(f'Feature metadata missing for {field}')
    return ordered


def validate_feature_vector(feature_vector):
    if not isinstance(feature_vector, dict):
        raise ValueError('Canonical risk feature vector must be an object')
    missing = [field for field in MODEL_FEATURES if field not in feature_vector]
    if missing:
        raise ValueError(f'Missing canonical fields: {missing}')
    if list(feature_vector.keys()) != MODEL_FEATURES:
        raise ValueError('Canonical feature vector order does not match the model contract')
    return dict(feature_vector)
