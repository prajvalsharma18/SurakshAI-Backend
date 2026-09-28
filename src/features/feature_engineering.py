"""Pure, deterministic calculations for leakage-safe operational features."""

from collections import defaultdict
from datetime import date, datetime, timedelta

from src.features.feature_schemas import HIGH_WORKLOAD_SCORE_THRESHOLD, validate_feature_vector
from src.schemas.operational import OPERATIONAL_SCHEMAS
from src.schemas.personnel import validate_personnel_identifier


_INTENSITY_VALUE = {value: index for index, value in enumerate(('LOW', 'MODERATE', 'HIGH', 'VERY_HIGH'), start=1)}


def _parse_date(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _created_by_reference(record, reference_date):
    created_at = record.get('created_at')
    if created_at is None:
        return True
    if not isinstance(created_at, str):
        return False
    try:
        created_date = datetime.fromisoformat(created_at.replace('Z', '+00:00')).date()
    except ValueError:
        created_date = _parse_date(created_at)
    return created_date is not None and created_date <= reference_date


def _eligible_records(domain, personnel_id, records, reference_date):
    date_field = OPERATIONAL_SCHEMAS[domain]['date_field']
    eligible = []
    for record in records or ():
        if not isinstance(record, dict) or record.get('personnel_id') != personnel_id:
            continue
        event_date = _parse_date(record.get(date_field))
        if event_date is None or event_date > reference_date:
            continue
        if not _created_by_reference(record, reference_date):
            continue
        eligible.append((event_date, record))
    return eligible


def _in_window(event_date, reference_date, days):
    return reference_date - timedelta(days=days - 1) <= event_date <= reference_date


def _average(values):
    return sum(values) / len(values) if values else None


def _round(value):
    return round(value, 2) if value is not None else None


def _max_consecutive_days(days):
    if not days:
        return 0
    ordered = sorted(set(days))
    maximum = current = 1
    for previous, current_date in zip(ordered, ordered[1:]):
        if current_date == previous + timedelta(days=1):
            current += 1
            maximum = max(maximum, current)
        else:
            current = 1
    return maximum


def _overlap(start, end, window_start, window_end):
    clipped_start = max(start, window_start)
    clipped_end = min(end, window_end)
    if clipped_start > clipped_end:
        return None
    return clipped_start, clipped_end


def _dates_in_range(start, end):
    for offset in range((end - start).days + 1):
        yield start + timedelta(days=offset)


def _duty_features(records, reference_date, days):
    selected = [(event_date, record) for event_date, record in records if _in_window(event_date, reference_date, days)]
    daily_hours = defaultdict(float)
    daily_night = defaultdict(bool)
    intensity_values = []
    for event_date, record in selected:
        daily_hours[event_date] += record['duty_hours']
        daily_night[event_date] = daily_night[event_date] or record['night_duty']
        intensity_values.append(_INTENSITY_VALUE[record['operational_intensity']])

    duty_days = len(daily_hours)
    night_days = sum(daily_night.values())
    total_hours = sum(daily_hours.values()) if selected else None
    return {
        f'duty_hours_{days}d_total': _round(total_hours),
        f'duty_hours_{days}d_avg': _round(total_hours / days if total_hours is not None else None),
        f'duty_hours_{days}d_max': _round(max(daily_hours.values()) if daily_hours else None),
        f'duty_days_{days}d': duty_days,
        f'night_duty_days_{days}d': night_days,
        f'night_duty_frequency_{days}d': _round(night_days / duty_days if duty_days else None),
        f'operational_intensity_{days}d_avg': _round(_average(intensity_values)),
        f'duty_data_available_{days}d': bool(selected),
    }


def _workload_features(records, reference_date, days):
    selected = [(event_date, record) for event_date, record in records if _in_window(event_date, reference_date, days)]
    daily_scores = defaultdict(list)
    daily_tasks = defaultdict(int)
    daily_duty_hours = defaultdict(float)
    for event_date, record in selected:
        daily_scores[event_date].append(record['workload_score'])
        daily_tasks[event_date] += record['task_count']
        daily_duty_hours[event_date] += record['duty_hours']

    observed_scores = [sum(values) / len(values) for values in daily_scores.values()]
    return {
        f'workload_{days}d_avg': _round(_average(observed_scores)),
        f'workload_{days}d_max': _round(max(observed_scores) if observed_scores else None),
        f'task_count_{days}d_avg': _round(_average(list(daily_tasks.values()))),
        f'task_count_{days}d_total': sum(daily_tasks.values()) if selected else None,
        f'workload_duty_hours_{days}d_avg': _round(_average(list(daily_duty_hours.values()))),
        f'high_workload_days_{days}d': (
            sum(max(scores) >= HIGH_WORKLOAD_SCORE_THRESHOLD for scores in daily_scores.values())
            if selected else None
        ),
        f'workload_data_available_{days}d': bool(selected),
    }


def build_longitudinal_features(personnel_id, reference_date, records_by_domain):
    """Build a feature vector from one person's records available on reference_date."""
    personnel_id = validate_personnel_identifier(personnel_id)
    reference_date = _parse_date(reference_date)
    if reference_date is None:
        raise ValueError('reference_date must use YYYY-MM-DD format')

    records = {
        domain: _eligible_records(domain, personnel_id, records_by_domain.get(domain, ()), reference_date)
        for domain in OPERATIONAL_SCHEMAS
    }
    vector = {'personnel_id': personnel_id, 'reference_date': reference_date.isoformat()}

    duty = records['duty_records']
    workload = records['workload_records']
    for days in (7, 30):
        vector.update(_duty_features(duty, reference_date, days))
        vector.update(_workload_features(workload, reference_date, days))

    all_duty_days = {event_date for event_date, _ in duty}
    all_duty_days.update(
        event_date for event_date, record in workload
        if record['duty_hours'] > 0
    )
    current_streak = 0
    cursor = reference_date
    while cursor in all_duty_days:
        current_streak += 1
        cursor -= timedelta(days=1)
    vector['current_consecutive_duty_days'] = current_streak
    for days in (7, 30):
        window_days = {day for day in all_duty_days if _in_window(day, reference_date, days)}
        vector[f'max_consecutive_duty_days_{days}d'] = _max_consecutive_days(window_days)

    night_days = {}
    for event_date, record in duty:
        night_days[event_date] = night_days.get(event_date, False) or record['night_duty']
    for event_date, record in workload:
        if event_date not in night_days and record['duty_hours'] > 0:
            night_days[event_date] = record['night_duty']
    for days in (7, 30):
        selected_nights = {
            event_date for event_date, is_night in night_days.items()
            if is_night and _in_window(event_date, reference_date, days)
        }
        duty_days = {
            event_date for event_date in all_duty_days
            if _in_window(event_date, reference_date, days)
        }
        vector[f'night_duty_days_{days}d'] = len(selected_nights)
        vector[f'night_duty_frequency_{days}d'] = _round(len(selected_nights) / len(duty_days) if duty_days else None)

    raw_duty_averages = {}
    raw_workload_averages = {}
    raw_night_frequencies = {}
    for days in (7, 30):
        selected_duty = [
            (event_date, record) for event_date, record in duty
            if _in_window(event_date, reference_date, days)
        ]
        raw_duty_averages[days] = (
            sum(record['duty_hours'] for _, record in selected_duty) / days
            if selected_duty else None
        )
        daily_scores = defaultdict(list)
        for event_date, record in workload:
            if _in_window(event_date, reference_date, days):
                daily_scores[event_date].append(record['workload_score'])
        raw_workload_averages[days] = _average([
            sum(scores) / len(scores) for scores in daily_scores.values()
        ])
        selected_duty_days = {day for day in all_duty_days if _in_window(day, reference_date, days)}
        selected_night_days = {
            day for day, is_night in night_days.items()
            if is_night and _in_window(day, reference_date, days)
        }
        raw_night_frequencies[days] = (
            len(selected_night_days) / len(selected_duty_days) if selected_duty_days else None
        )

    vector.update(_leave_features(records['leave_records'], reference_date))
    vector.update(_deployment_features(records['deployment_records'], reference_date))
    vector.update(_training_features(records['training_records'], reference_date))
    vector.update(_transfer_features(records['transfer_records'], reference_date))

    vector['duty_hours_trend'] = _round(
        raw_duty_averages[7] - raw_duty_averages[30]
        if raw_duty_averages[7] is not None and raw_duty_averages[30] is not None else None
    )
    vector['workload_trend'] = _round(
        raw_workload_averages[7] - raw_workload_averages[30]
        if raw_workload_averages[7] is not None and raw_workload_averages[30] is not None else None
    )
    vector['night_duty_trend'] = _round(
        raw_night_frequencies[7] - raw_night_frequencies[30]
        if raw_night_frequencies[7] is not None and raw_night_frequencies[30] is not None else None
    )
    vector['high_workload_score_threshold'] = HIGH_WORKLOAD_SCORE_THRESHOLD
    return validate_feature_vector(vector)


def _leave_features(records, reference_date):
    start_30d = reference_date - timedelta(days=29)
    leave_dates = set()
    overlap_records = []
    completed_end_dates = []
    for event_date, record in records:
        end_date = _parse_date(record.get('end_date'))
        if end_date is None:
            continue
        if end_date <= reference_date:
            completed_end_dates.append(end_date)
        clipped_end = min(end_date, reference_date)
        overlap = _overlap(event_date, clipped_end, start_30d, reference_date)
        if overlap:
            overlap_records.append(record)
            leave_dates.update(_dates_in_range(*overlap))
    most_recent_end = max(completed_end_dates) if completed_end_dates else None
    return {
        'days_since_most_recent_completed_leave': (reference_date - most_recent_end).days if most_recent_end else None,
        'leave_episodes_30d': len(overlap_records),
        'leave_days_30d': len(leave_dates),
        'recent_leave_period_30d': bool(leave_dates),
        'leave_data_available_30d': bool(overlap_records),
    }


def _deployment_features(records, reference_date):
    start_30d = reference_date - timedelta(days=29)
    overlaps = []
    deployment_dates = set()
    active = []
    for start_date, record in records:
        end_date = _parse_date(record.get('end_date')) or reference_date
        clipped_end = min(end_date, reference_date)
        overlap = _overlap(start_date, clipped_end, start_30d, reference_date)
        if overlap:
            overlaps.append(record)
            deployment_dates.update(_dates_in_range(*overlap))
        raw_end = _parse_date(record.get('end_date'))
        if raw_end is None or raw_end >= reference_date:
            active.append((start_date, record))
    current = max(active, key=lambda item: item[0]) if active else None
    return {
        'current_deployment_duration_days': (reference_date - current[0]).days + 1 if current else 0,
        'deployment_days_30d': len(deployment_dates),
        'deployments_30d': len(overlaps),
        'current_deployment_intensity': current[1]['operational_intensity'] if current else None,
        'deployment_data_available_30d': bool(overlaps),
    }


def _training_features(records, reference_date):
    start_7d = reference_date - timedelta(days=6)
    start_30d = reference_date - timedelta(days=29)
    completed = []
    for start_date, record in records:
        end_date = _parse_date(record.get('end_date'))
        if end_date is not None and end_date <= reference_date:
            completed.append((start_date, end_date, record))

    def total_hours(window_start):
        hours = 0.0
        for start_date, end_date, record in completed:
            overlap = _overlap(start_date, end_date, window_start, reference_date)
            if overlap:
                session_days = (end_date - start_date).days + 1
                overlap_days = (overlap[1] - overlap[0]).days + 1
                hours += record['training_hours'] * overlap_days / session_days
        return _round(hours)

    in_30d = [
        record for start_date, end_date, record in completed
        if _overlap(start_date, end_date, start_30d, reference_date)
    ]
    in_7d = [
        record for start_date, end_date, record in completed
        if _overlap(start_date, end_date, start_7d, reference_date)
    ]
    return {
        'training_hours_7d': total_hours(start_7d),
        'training_hours_30d': total_hours(start_30d),
        'training_sessions_30d': len(in_30d),
        'training_intensity_30d_avg': _round(_average([_INTENSITY_VALUE[item['intensity']] for item in in_30d])),
        'training_data_available_7d': bool(in_7d),
        'training_data_available_30d': bool(in_30d),
    }


def _transfer_features(records, reference_date):
    start_30d = reference_date - timedelta(days=29)
    dates = [event_date for event_date, _ in records]
    recent = [event_date for event_date in dates if event_date >= start_30d]
    most_recent = max(dates) if dates else None
    return {
        'transfers_30d': len(recent),
        'days_since_most_recent_transfer': (reference_date - most_recent).days if most_recent else None,
        'transfer_data_available_30d': bool(recent),
    }