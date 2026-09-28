"""Development-only generator for synthetic Phase 3B operational history."""

from datetime import date, timedelta
import os
import random
from uuid import NAMESPACE_URL, uuid5

from src.db.mongodb import get_database
from src.db.repositories.operational_repository import OperationalRepository
from src.db.repositories.personnel_repository import PersonnelRepository


SCENARIOS = (
    'LOW_LOAD',
    'NORMAL',
    'HIGH_WORKLOAD',
    'HIGH_NIGHT_DUTY',
    'LONG_DEPLOYMENT',
    'FREQUENT_TRANSFERS',
    'LOW_LEAVE',
    'RECOVERY',
    'TRAINING_HEAVY',
    'NORMAL',
)


def _record_id(domain, personnel_id, key, seed_batch_id=None):
    batch = f'{seed_batch_id}:' if seed_batch_id else ''
    return str(uuid5(NAMESPACE_URL, f'surakshai:{batch}{domain}:{personnel_id}:{key}'))


def _leave_days(scenario, history_days):
    if scenario == 'LOW_LEAVE':
        return []
    if scenario == 'RECOVERY':
        return list(range(max(0, history_days - 15), history_days - 3))
    return [min(18, history_days - 1), min(19, history_days - 1)]


def seed_operational_data(
    database,
    *,
    personnel_count=12,
    history_days=75,
    as_of=None,
    random_seed=2026,
    personnel_ids=None,
    seed_batch_id=None,
    operational_service=None,
):
    if not 1 <= personnel_count <= 50:
        raise ValueError('personnel_count must be between 1 and 50')
    if not 60 <= history_days <= 90:
        raise ValueError('history_days must be between 60 and 90')

    as_of = as_of or date.today()
    rng = random.Random(random_seed)
    personnel_repository = PersonnelRepository(database=database)
    operational_repository = OperationalRepository(database=database)
    personnel_ids = personnel_ids or [f'P{index:03d}' for index in range(1, personnel_count + 1)]
    if len(personnel_ids) != personnel_count:
        raise ValueError('personnel_ids must contain exactly personnel_count identifiers')
    created = {domain: 0 for domain in operational_repository.domains}

    for index, personnel_id in enumerate(personnel_ids, start=1):
        existing_personnel = (
            personnel_repository.collection.find_one({'personnel_id': personnel_id})
            if personnel_repository.collection is not None else None
        )
        if existing_personnel is None:
            record = {
                'personnel_id': personnel_id,
                'unit_id': f'STG-U-{((index - 1) % 5) + 1:02d}',
                'rank': ('JCO', 'Naik', 'Havildar', 'Captain')[index % 4],
                'service_years': index % 16,
                'posting_type': ('FIELD', 'TRAINING', 'DEPLOYED', 'ADMIN')[index % 4],
                'status': 'ACTIVE',
            }
            if seed_batch_id is not None:
                record['seed_batch_id'] = seed_batch_id
            personnel_repository.create(record)

        scenario = SCENARIOS[(index - 1) % len(SCENARIOS)]
        first_day = as_of - timedelta(days=history_days - 1)
        days_off = set(_leave_days(scenario, history_days))

        if scenario != 'LOW_LEAVE' and history_days > 20:
            leave_start = first_day + timedelta(days=_leave_days(scenario, history_days)[0])
            leave_end = leave_start + timedelta(days=len(_leave_days(scenario, history_days)) - 1)
            leave = {
                'personnel_id': personnel_id,
                'leave_type': 'ANNUAL' if index % 2 else 'COMPENSATORY',
                'start_date': leave_start.isoformat(),
                'end_date': leave_end.isoformat(),
                'duration_days': (leave_end - leave_start).days + 1,
            }
            created['leave_records'] += _create_if_missing(
                operational_repository, 'leave_records', leave,
                _record_id('leave_records', personnel_id, leave_start.isoformat(), seed_batch_id),
                operational_service=operational_service,
                seed_batch_id=seed_batch_id,
            )

        deployment_start = first_day if scenario == 'LONG_DEPLOYMENT' else first_day + timedelta(days=25 + index % 20)
        deployment_end = None if scenario == 'LONG_DEPLOYMENT' else min(as_of, deployment_start + timedelta(days=20 + index % 15))
        deployment = {
            'personnel_id': personnel_id,
            'deployment_type': ('FIELD', 'SUPPORT', 'PEACEKEEPING')[index % 3],
            'start_date': deployment_start.isoformat(),
            'end_date': deployment_end.isoformat() if deployment_end else None,
            'operational_intensity': 'HIGH' if scenario in {'HIGH_WORKLOAD', 'HIGH_NIGHT_DUTY', 'LONG_DEPLOYMENT'} else 'MODERATE',
            'location_category': ('DOMESTIC', 'BORDER', 'REMOTE', 'TRAINING_AREA')[index % 4],
        }
        created['deployment_records'] += _create_if_missing(
            operational_repository, 'deployment_records', deployment,
            _record_id('deployment_records', personnel_id, deployment_start.isoformat(), seed_batch_id),
            operational_service=operational_service,
            seed_batch_id=seed_batch_id,
        )

        transfer_count = 4 if scenario == 'FREQUENT_TRANSFERS' else (1 if index % 3 == 0 else 0)
        posting_cycle = ('FIELD', 'SUPPORT', 'TRAINING', 'ADMIN', 'FIELD')
        for transfer_index in range(transfer_count):
            transfer_date = first_day + timedelta(days=(history_days * (transfer_index + 1)) // (transfer_count + 1))
            transfer = {
                'personnel_id': personnel_id,
                'transfer_date': transfer_date.isoformat(),
                'previous_posting': posting_cycle[transfer_index],
                'new_posting': posting_cycle[transfer_index + 1],
                'reason_category': 'ROTATION',
            }
            created['transfer_records'] += _create_if_missing(
                operational_repository, 'transfer_records', transfer,
                _record_id('transfer_records', personnel_id, transfer_date.isoformat(), seed_batch_id),
                operational_service=operational_service,
                seed_batch_id=seed_batch_id,
            )

        training_count = 4 if scenario == 'TRAINING_HEAVY' else 1
        for training_index in range(training_count):
            training_start = first_day + timedelta(days=max(1, (history_days * (training_index + 1)) // (training_count + 1)))
            training_end = min(as_of, training_start + timedelta(days=2))
            training = {
                'personnel_id': personnel_id,
                'training_type': ('SAFETY', 'LEADERSHIP', 'TECHNICAL')[training_index % 3],
                'start_date': training_start.isoformat(),
                'end_date': training_end.isoformat(),
                'training_hours': 18 if training_count == 1 else 30,
                'intensity': 'HIGH' if training_count > 1 else 'MODERATE',
            }
            created['training_records'] += _create_if_missing(
                operational_repository, 'training_records', training,
                _record_id('training_records', personnel_id, training_start.isoformat(), seed_batch_id),
                operational_service=operational_service,
                seed_batch_id=seed_batch_id,
            )

        consecutive_days = 0
        for day_index in range(history_days):
            current_date = first_day + timedelta(days=day_index)
            if day_index in days_off:
                consecutive_days = 0
                continue
            consecutive_days += 1
            if scenario == 'LOW_LOAD':
                duty_hours = round(5 + rng.random() * 2, 1)
            elif scenario == 'HIGH_WORKLOAD':
                duty_hours = round(11 + rng.random() * 2, 1)
            elif scenario == 'HIGH_NIGHT_DUTY':
                duty_hours = round(8 + rng.random() * 1.5, 1)
            elif scenario == 'RECOVERY' and day_index >= history_days - 18:
                duty_hours = round(6 + rng.random() * 1.5, 1)
            else:
                duty_hours = round(7 + rng.random() * 2, 1)

            night_duty = scenario == 'HIGH_NIGHT_DUTY' and day_index % 2 == 0
            night_duty = night_duty or (scenario == 'HIGH_WORKLOAD' and day_index % 5 == 0)
            shift_type = 'NIGHT' if night_duty else ('ROTATING' if day_index % 7 == 0 else 'DAY')
            duty = {
                'personnel_id': personnel_id,
                'date': current_date.isoformat(),
                'duty_hours': duty_hours,
                'night_duty': night_duty,
                'shift_type': shift_type,
                'operational_intensity': 'HIGH' if duty_hours >= 11 else ('LOW' if duty_hours <= 7 else 'MODERATE'),
            }
            created['duty_records'] += _create_if_missing(
                operational_repository, 'duty_records', duty,
                _record_id('duty_records', personnel_id, current_date.isoformat(), seed_batch_id),
                operational_service=operational_service,
                seed_batch_id=seed_batch_id,
            )

            task_count = max(1, round(duty_hours * 1.5 + rng.randint(-2, 3)))
            workload_score = min(100, round(20 + duty_hours * 3 + task_count * 0.7 + (12 if night_duty else 0)))
            workload = {
                'personnel_id': personnel_id,
                'date': current_date.isoformat(),
                'workload_score': workload_score,
                'duty_hours': duty_hours,
                'task_count': task_count,
                'night_duty': night_duty,
                'consecutive_duty_days': consecutive_days,
            }
            created['workload_records'] += _create_if_missing(
                operational_repository, 'workload_records', workload,
                _record_id('workload_records', personnel_id, current_date.isoformat(), seed_batch_id),
                operational_service=operational_service,
                seed_batch_id=seed_batch_id,
            )

    return {'personnel_count': personnel_count, 'history_days': history_days, 'scenarios': list(SCENARIOS), 'created': created}


def _create_if_missing(
    repository,
    domain,
    payload,
    record_id,
    *,
    operational_service=None,
    seed_batch_id=None,
):
    if repository.get(domain, record_id) is not None:
        return 0
    if operational_service is not None:
        operational_service.create(
            domain,
            payload,
            record_id=record_id,
            seed_batch_id=seed_batch_id,
        )
    else:
        repository.create(
            domain,
            payload,
            record_id=record_id,
            seed_batch_id=seed_batch_id,
        )
    return 1


if __name__ == '__main__':
    from config import validate_synthetic_seed_configuration

    validate_synthetic_seed_configuration()
    database = get_database()
    if database is None:
        raise SystemExit('MongoDB is not configured.')
    result = seed_operational_data(database)
    print(f"Seeded synthetic operational data for {result['personnel_count']} personnel over {result['history_days']} days.")
