"""Populate a guarded, synthetic SURAKSHAI development/staging dataset."""

from __future__ import annotations

from datetime import date, timedelta
import os
from pathlib import Path
import random
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_database_name, validate_synthetic_seed_configuration
from scripts.seed_operational_data import seed_operational_data
from src.db.mongodb import get_database
from src.db.repositories.consent_repository import ConsentRepository
from src.db.repositories.operational_repository import OperationalRepository
from src.db.repositories.personnel_repository import PersonnelRepository
from src.db.repositories.user_repository import UserRepository
from src.db.repositories.wellness_repository import WellnessRepository
from src.features.feature_service import FeatureService
from src.ml.risk_service import RiskService
from src.security.audit import get_audit_service
from src.security.consent import (
    CONSENT_RECOMMENDATION_PROCESSING,
    CONSENT_WELLNESS_DATA_PROCESSING,
    ConsentService,
)
from src.services.operational_service import OperationalService
from src.services.personnel_service import PersonnelService
from src.services.risk_history_service import RiskHistoryService
from src.services.user_service import UserService
from src.services.welfare_dashboard_service import WelfareDashboardService
from src.services.wellness_service import WellnessService
from src.welfare.repositories import (
    RiskPredictionRepository,
    WelfareAlertRepository,
    WelfareInterventionRepository,
)
from src.welfare.workflow_service import WelfareWorkflowService
from src.schemas.user import validate_password


SEED_COLLECTIONS = (
    'welfare_interventions',
    'welfare_alerts',
    'risk_predictions',
    'wellness_assessments',
    'consents',
    'duty_records',
    'leave_records',
    'deployment_records',
    'transfer_records',
    'training_records',
    'workload_records',
    'users',
    'personnel_identity',
)
PERSONNEL_ID_START = 900
ROLE_USER_DEFINITIONS = (
    ('stg_welfare_officer', 'seed-user-welfare-officer', 'WELFARE_OFFICER', 'SURAKSHAI_STAGING_WELFARE_PASSWORD'),
    ('stg_welfare_002', 'seed-user-welfare-002', 'WELFARE_OFFICER', 'SURAKSHAI_STAGING_WELFARE_PASSWORD'),
    ('stg_welfare_003', 'seed-user-welfare-003', 'WELFARE_OFFICER', 'SURAKSHAI_STAGING_WELFARE_PASSWORD'),
    ('stg_commander_001', 'seed-user-commander-001', 'COMMANDER', 'SURAKSHAI_STAGING_COMMANDER_PASSWORD'),
    ('stg_commander_002', 'seed-user-commander-002', 'COMMANDER', 'SURAKSHAI_STAGING_COMMANDER_PASSWORD'),
    ('stg_admin_001', 'seed-user-admin-001', 'ADMIN', 'SURAKSHAI_STAGING_ADMIN_PASSWORD'),
    ('stg_admin_002', 'seed-user-admin-002', 'ADMIN', 'SURAKSHAI_STAGING_ADMIN_PASSWORD'),
)


def _int_setting(name, default, minimum, maximum):
    value = os.getenv(name, str(default))
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f'{name} must be an integer') from error
    if not minimum <= parsed <= maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return parsed


def _batch_id(value=None):
    batch_id = value or os.getenv(
        'SURAKSHAI_SYNTHETIC_SEED_BATCH_ID',
        f'surakshai-staging-{date.today():%Y-%m}',
    )
    if not isinstance(batch_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', batch_id):
        raise ValueError('Synthetic seed batch ID must contain only letters, numbers, dots, underscores, and hyphens')
    return batch_id


def _collection(database, name):
    return database.get_collection(name) if hasattr(database, 'get_collection') else database[name]


def _records(collection, query, projection=None):
    return [dict(record) for record in collection.find(query, projection)]


def _count(collection, query):
    try:
        return collection.count_documents(query)
    except AttributeError:
        return sum(1 for _ in collection.find(query))


def _delete_batch(database, batch_id):
    deleted = {}
    for name in SEED_COLLECTIONS:
        result = _collection(database, name).delete_many({'seed_batch_id': batch_id})
        deleted[name] = int(result.deleted_count)
    return deleted


def reset_staging_dataset(database=None, *, batch_id=None):
    """Delete only records carrying this seed's marker from a guarded dev database."""
    validate_synthetic_seed_configuration(reset=True)
    if database is None:
        database = get_database()
    if database is None:
        raise RuntimeError('MongoDB is not configured for synthetic staging data')
    return _delete_batch(database, _batch_id(batch_id))


def _account_definitions(personnel_ids):
    personnel_accounts = [
        (
            f'stg_personnel_{personnel_id.lower()}',
            f'seed-user-{personnel_id.lower()}',
            'PERSONNEL',
            personnel_id,
        )
        for personnel_id in personnel_ids
    ]
    role_accounts = [
        (username, user_id, role, None)
        for username, user_id, role, _ in ROLE_USER_DEFINITIONS
    ]
    return personnel_accounts + role_accounts


def _check_identifier_conflicts(personnel_repository, user_repository, personnel_ids, batch_id):
    for personnel_id in personnel_ids:
        record = personnel_repository.get_by_personnel_id(personnel_id, include_inactive=True)
        if record is not None and record.get('seed_batch_id') != batch_id:
            raise ValueError(f'Synthetic personnel ID {personnel_id} is already owned by another record')
    for username, user_id, role, personnel_id in _account_definitions(personnel_ids):
        by_username = user_repository.get_by_username(username)
        by_user_id = user_repository.get_by_user_id(user_id)
        for record in (by_username, by_user_id):
            if record is not None and record.get('seed_batch_id') != batch_id:
                raise ValueError(f'Synthetic account {username} is already owned by another record')
            if record is not None and (
                record.get('username') != username
                or record.get('user_id') != user_id
                or record.get('role') != role
                or record.get('personnel_id') != personnel_id
            ):
                raise ValueError(f'Synthetic account {username} conflicts with an existing same-batch account')


def _ensure_user(
    user_service,
    user_repository,
    *,
    username,
    user_id,
    role,
    personnel_id,
    password,
    batch_id,
):
    existing = user_repository.get_by_username(username)
    if existing is None:
        created = user_service.create_user(
            username=username,
            password=password,
            role=role,
            personnel_id=personnel_id,
            user_id=user_id,
            source='synthetic_staging_seed',
            seed_batch_id=batch_id,
        )
    else:
        if (
            existing.get('seed_batch_id') != batch_id
            or existing.get('user_id') != user_id
            or existing.get('role') != role
            or existing.get('personnel_id') != personnel_id
        ):
            raise ValueError(f'Synthetic account {username} does not match this seed batch')
        created = existing
        if existing.get('status') != 'ACTIVE':
            created = user_service.update_user(user_id, status='ACTIVE')

    identity = user_service.authenticate(username, password)
    if identity is None and existing is not None:
        user_service.update_user(user_id, password=password)
        identity = user_service.authenticate(username, password)
    if identity is None:
        raise RuntimeError(f'Synthetic {role} account could not authenticate after provisioning')
    identity['seed_batch_id'] = batch_id
    return created, identity


def _ensure_consent_state(
    consent_service,
    user_id,
    personnel_id,
    consent_type,
    target_status,
    batch_id,
):
    if target_status is None:
        return
    history = consent_service.get_history(personnel_id, consent_type)
    current_status = history[0].get('status') if history else None
    if target_status == 'REVOKED' and current_status not in {'GRANTED', 'REVOKED'}:
        consent_service.grant_consent(
            user_id, consent_type, personnel_id, seed_batch_id=batch_id,
        )
        current_status = 'GRANTED'
    if current_status != target_status:
        setter = (
            consent_service.grant_consent
            if target_status == 'GRANTED'
            else consent_service.revoke_consent
        )
        setter(user_id, consent_type, personnel_id, seed_batch_id=batch_id)


def _personnel_records(database, batch_id):
    return _records(_collection(database, 'personnel_identity'), {'seed_batch_id': batch_id})


def _verify_dataset(
    database,
    batch_id,
    personnel_ids,
    consent_service,
):
    personnel = _personnel_records(database, batch_id)
    users = _records(
        _collection(database, 'users'),
        {'seed_batch_id': batch_id},
        {
            '_id': 0,
            'user_id': 1,
            'username': 1,
            'role': 1,
            'status': 1,
            'personnel_id': 1,
            'seed_batch_id': 1,
        },
    )
    consents = _records(_collection(database, 'consents'), {'seed_batch_id': batch_id})
    wellness = _records(_collection(database, 'wellness_assessments'), {'seed_batch_id': batch_id})
    predictions = _records(_collection(database, 'risk_predictions'), {'seed_batch_id': batch_id})
    alerts = _records(_collection(database, 'welfare_alerts'), {'seed_batch_id': batch_id})
    interventions = _records(_collection(database, 'welfare_interventions'), {'seed_batch_id': batch_id})
    known_ids = {item['personnel_id'] for item in personnel}
    if len(known_ids) != len(personnel):
        raise RuntimeError('Synthetic personnel IDs are not unique')
    if known_ids != set(personnel_ids):
        raise RuntimeError('Synthetic personnel batch does not match the requested personnel IDs')
    usernames = [item['username'] for item in users]
    if len(usernames) != len(set(usernames)):
        raise RuntimeError('Synthetic usernames are not unique')
    users_by_role = {
        role: sum(user.get('role') == role for user in users)
        for role in ('PERSONNEL', 'WELFARE_OFFICER', 'COMMANDER', 'ADMIN')
    }
    if users_by_role != {
        'PERSONNEL': len(personnel_ids),
        'WELFARE_OFFICER': 3,
        'COMMANDER': 2,
        'ADMIN': 2,
    }:
        raise RuntimeError('Synthetic account role counts do not match the staging seed contract')
    if any(user.get('status') != 'ACTIVE' for user in users):
        raise RuntimeError('A synthetic staging account is not active')
    personnel_mappings = {
        user.get('personnel_id')
        for user in users
        if user.get('role') == 'PERSONNEL'
    }
    if personnel_mappings != known_ids:
        raise RuntimeError('Synthetic personnel accounts do not map one-to-one to seeded personnel')
    for user in users:
        personnel_id = user.get('personnel_id')
        if personnel_id is not None and personnel_id not in known_ids:
            raise RuntimeError('A synthetic personnel user does not map to seeded personnel')
        if user.get('role') != 'PERSONNEL' and personnel_id is not None:
            raise RuntimeError('A non-personnel synthetic account must not map to personnel')
    for record in consents:
        if record.get('personnel_id') not in known_ids:
            raise RuntimeError('A synthetic consent record references unknown personnel')
        if record.get('actor_user_id') not in {user['user_id'] for user in users}:
            raise RuntimeError('A synthetic consent record references an unknown user')
    for record in wellness:
        if record.get('personnel_id') not in known_ids:
            raise RuntimeError('A synthetic wellness assessment references unknown personnel')
        if not consent_service.has_personnel_consent(
            record['personnel_id'],
            CONSENT_WELLNESS_DATA_PROCESSING,
        ):
            raise RuntimeError('Wellness data exists without current wellness consent')
    prediction_keys = {
        (item.get('personnel_id'), item.get('reference_date'))
        for item in predictions
    }
    for record in predictions:
        if record.get('personnel_id') not in known_ids:
            raise RuntimeError('A synthetic risk prediction references unknown personnel')
    alerts_by_id = {item['alert_id']: item for item in alerts}
    for record in alerts:
        if record.get('personnel_id') not in known_ids:
            raise RuntimeError('A synthetic alert references unknown personnel')
        if (record.get('personnel_id'), record.get('reference_date')) not in prediction_keys:
            raise RuntimeError('A synthetic alert has no matching persisted risk prediction')
    for record in interventions:
        alert = alerts_by_id.get(record.get('alert_id'))
        if alert is None or alert.get('personnel_id') != record.get('personnel_id'):
            raise RuntimeError('A synthetic intervention references an unknown alert or personnel member')
    operational = {
        domain: _count(_collection(database, domain), {'seed_batch_id': batch_id})
        for domain in OperationalRepository(database).domains
    }
    for domain in operational:
        records = _records(_collection(database, domain), {'seed_batch_id': batch_id})
        if any(record.get('personnel_id') not in known_ids for record in records):
            raise RuntimeError(f'A synthetic {domain} record references unknown personnel')
    return {
        'personnel': personnel,
        'users': users,
        'users_by_role': users_by_role,
        'consents': consents,
        'wellness': wellness,
        'predictions': predictions,
        'alerts': alerts,
        'interventions': interventions,
        'operational': operational,
    }


def seed_staging_dataset(
    database=None,
    *,
    personnel_count=None,
    history_days=75,
    random_seed=None,
    as_of=None,
    batch_id=None,
):
    """Create synthetic records using the backend's validated service workflows."""
    reset_requested = os.getenv('SURAKSHAI_RESET_SYNTHETIC_DATA') == '1'
    validate_synthetic_seed_configuration(reset=reset_requested)
    batch_id = _batch_id(batch_id)
    if personnel_count is None:
        personnel_count = _int_setting(
            'SURAKSHAI_SYNTHETIC_PERSONNEL_COUNT', 30, 20, 50,
        )
    if not 20 <= personnel_count <= 50:
        raise ValueError('personnel_count must be between 20 and 50')
    random_seed = (
        _int_setting('SYNTHETIC_DATA_RANDOM_SEED', 42, 0, 2**31 - 1)
        if random_seed is None else random_seed
    )
    if isinstance(random_seed, bool) or not isinstance(random_seed, int) or random_seed < 0:
        raise ValueError('random_seed must be a non-negative integer')
    if not 60 <= history_days <= 90:
        raise ValueError('history_days must be between 60 and 90')
    password_names = {
        'PERSONNEL': 'SURAKSHAI_STAGING_PERSONNEL_PASSWORD',
        **{
            role: password_name
            for _, _, role, password_name in ROLE_USER_DEFINITIONS
        },
    }
    passwords = {}
    for role, password_name in password_names.items():
        password = os.getenv(password_name)
        if not password:
            raise ValueError(f'Set {password_name} for development-only synthetic {role} accounts')
        validate_password(password)
        passwords[role] = password
    if database is None:
        database = get_database()
    if database is None:
        raise RuntimeError('MongoDB is not configured for synthetic staging data')

    personnel_ids = [
        f'P{PERSONNEL_ID_START + index:03d}'
        for index in range(personnel_count)
    ]
    personnel_repository = PersonnelRepository(database)
    user_repository = UserRepository(database)
    _check_identifier_conflicts(personnel_repository, user_repository, personnel_ids, batch_id)
    audit_service = get_audit_service()
    operational_repository = OperationalRepository(database)
    wellness_repository = WellnessRepository(database)
    consent_service = ConsentService(ConsentRepository(database), audit_service=audit_service)
    user_service = UserService(user_repository, personnel_repository, audit_service=audit_service)
    personnel_service = PersonnelService(personnel_repository, audit_service=audit_service)
    operational_service = OperationalService(operational_repository, audit_service=audit_service)
    wellness_service = WellnessService(
        wellness_repository,
        consent_service,
        audit_service=audit_service,
    )
    risk_service = RiskService(
        FeatureService(operational_repository, audit_service=audit_service),
        wellness_service=wellness_service,
        consent_service=consent_service,
        audit_service=audit_service,
    )
    risk_repository = RiskPredictionRepository(database)
    alert_repository = WelfareAlertRepository(database)
    intervention_repository = WelfareInterventionRepository(database)
    workflow = WelfareWorkflowService(
        risk_service,
        risk_repository,
        alert_repository,
        intervention_repository,
        audit_service=audit_service,
    )

    if as_of is None:
        configured_date = os.getenv('SYNTHETIC_DATA_AS_OF')
        if configured_date:
            try:
                as_of = date.fromisoformat(configured_date)
            except ValueError as error:
                raise ValueError('SYNTHETIC_DATA_AS_OF must use YYYY-MM-DD format') from error
            if as_of.isoformat() != configured_date:
                raise ValueError('SYNTHETIC_DATA_AS_OF must use YYYY-MM-DD format')
        else:
            as_of = date.today()
    if type(as_of) is not date:
        raise ValueError('as_of must be a date')
    if as_of > date.today():
        raise ValueError('as_of cannot be in the future')
    if reset_requested:
        _delete_batch(database, batch_id)
    _check_identifier_conflicts(personnel_repository, user_repository, personnel_ids, batch_id)
    rng = random.Random(random_seed)
    consented_count = int(personnel_count * 0.6)
    revoked_count = max(1, personnel_count // 10)
    consented_indices = set(range(consented_count))
    revoked_indices = set(range(consented_count, consented_count + revoked_count))
    no_consent_indices = set(range(consented_count + revoked_count, personnel_count))

    for index, personnel_id in enumerate(personnel_ids):
        if personnel_repository.get_by_personnel_id(
            personnel_id,
            include_inactive=True,
        ) is None:
            personnel_service.create_personnel({
                'personnel_id': personnel_id,
                'unit_id': f'STG-U-{index % 5 + 1:02d}',
                'rank': ('JCO', 'Naik', 'Havildar', 'Captain', 'Subedar')[index % 5],
                'service_years': 1 + (index * 3) % 30,
                'posting_type': ('FIELD', 'TRAINING', 'DEPLOYED', 'ADMIN', 'OTHER')[index % 5],
                'status': 'ACTIVE',
            }, seed_batch_id=batch_id)

    staging_users = {}
    for index, personnel_id in enumerate(personnel_ids):
        username = f'stg_personnel_{personnel_id.lower()}'
        _, identity = _ensure_user(
            user_service,
            user_repository,
            username=username,
            user_id=f'seed-user-{personnel_id.lower()}',
            role='PERSONNEL',
            personnel_id=personnel_id,
            password=passwords['PERSONNEL'],
            batch_id=batch_id,
        )
        staging_users[personnel_id] = identity

        wellness_status = (
            'GRANTED' if index in consented_indices
            else 'REVOKED' if index in revoked_indices
            else None
        )
        _ensure_consent_state(
            consent_service,
            identity['user_id'],
            personnel_id,
            CONSENT_WELLNESS_DATA_PROCESSING,
            wellness_status,
            batch_id,
        )
        recommendation_status = (
            'GRANTED' if index < 6
            else 'REVOKED' if index < 9
            else None
        )
        _ensure_consent_state(
            consent_service,
            identity['user_id'],
            personnel_id,
            CONSENT_RECOMMENDATION_PROCESSING,
            recommendation_status,
            batch_id,
        )
        if wellness_status != 'GRANTED':
            continue
        for day_offset in (30, 0):
            assessment_date = as_of - timedelta(days=day_offset)
            if wellness_repository.collection is None:
                raise RuntimeError('Wellness database is unavailable for synthetic staging data')
            if wellness_repository.collection.find_one({
                'personnel_id': personnel_id,
                'assessment_date': assessment_date.isoformat(),
                'seed_batch_id': batch_id,
            }):
                continue
            wellness_service.create_for_self(
                {
                    'assessment_date': assessment_date.isoformat(),
                    'sleep_quality': rng.randint(2, 5),
                    'fatigue_level': rng.randint(1, 4),
                    'perceived_stress': rng.randint(1, 4),
                    'mood_wellbeing': rng.randint(2, 5),
                },
                identity,
                seed_batch_id=batch_id,
            )

    operational_result = seed_operational_data(
        database,
        personnel_count=personnel_count,
        history_days=history_days,
        as_of=as_of,
        random_seed=random_seed,
        personnel_ids=personnel_ids,
        seed_batch_id=batch_id,
        operational_service=operational_service,
    )

    role_identities = {}
    for username, user_id, role, _ in ROLE_USER_DEFINITIONS:
        _, identity = _ensure_user(
            user_service,
            user_repository,
            username=username,
            user_id=user_id,
            role=role,
            personnel_id=None,
            password=passwords[role],
            batch_id=batch_id,
        )
        role_identities.setdefault(role, []).append(identity)
    seed_identity = role_identities['WELFARE_OFFICER'][0]
    evaluation_offsets = {personnel_id: {0} for personnel_id in personnel_ids}
    for personnel_id in personnel_ids[:min(8, personnel_count)]:
        evaluation_offsets[personnel_id].update({3, 6})
    generated_predictions = 0
    observed_alerts = {}
    for personnel_id in personnel_ids:
        identity = dict(staging_users.get(personnel_id, seed_identity))
        identity['seed_batch_id'] = batch_id
        for day_offset in sorted(evaluation_offsets[personnel_id], reverse=True):
            reference_date = as_of - timedelta(days=day_offset)
            result = workflow.evaluate(
                personnel_id,
                reference_date=reference_date.isoformat(),
                identity=identity,
            )
            generated_predictions += 1
            alert = result.get('alert')
            if alert and alert.get('seed_batch_id') == batch_id:
                observed_alerts[alert['alert_id']] = alert

    actor = dict(seed_identity)
    intervention_statuses = []
    interventions_created = 0
    for alert in list(observed_alerts.values())[:3]:
        if intervention_repository.list_for_alert(alert['alert_id']):
            continue
        current = workflow.get_alert(alert['alert_id'])
        if current['status'] == 'OPEN':
            current = workflow.transition(alert['alert_id'], 'ACKNOWLEDGED', actor)
        if current['status'] == 'ACKNOWLEDGED':
            current = workflow.transition(alert['alert_id'], 'UNDER_REVIEW', actor)
        if current['status'] not in {'UNDER_REVIEW', 'ACTION_PLANNED', 'FOLLOW_UP'}:
            continue
        intervention = workflow.create_intervention(
            alert['alert_id'],
            alert['personnel_id'],
            'WELFARE_CHECK_IN',
            actor,
            scheduled_follow_up=(as_of + timedelta(days=14)).isoformat(),
        )
        intervention_statuses.append(intervention['status'])
        interventions_created += 1
        if len(intervention_statuses) == 1:
            intervention = workflow.update_intervention(
                intervention['intervention_id'],
                'IN_PROGRESS',
                actor,
            )
            intervention_statuses[-1] = intervention['status']
        if len(intervention_statuses) == 2:
            if current['status'] == 'UNDER_REVIEW':
                current = workflow.transition(
                    alert['alert_id'],
                    'ACTION_PLANNED',
                    actor,
                )
            if current['status'] != 'FOLLOW_UP':
                workflow.transition(
                    alert['alert_id'],
                    'FOLLOW_UP',
                    actor,
                    scheduled_follow_up=(as_of + timedelta(days=14)).isoformat(),
                )
            follow_up = workflow.create_intervention(
                alert['alert_id'],
                alert['personnel_id'],
                'FOLLOW_UP',
                actor,
                scheduled_follow_up=(as_of + timedelta(days=14)).isoformat(),
            )
            intervention_statuses.append(follow_up['status'])
            interventions_created += 1

    verified = _verify_dataset(
        database,
        batch_id,
        personnel_ids,
        consent_service,
    )
    if len(verified['predictions']) != generated_predictions:
        raise RuntimeError('Persisted risk-prediction count does not match generated observations')
    own_history = RiskHistoryService(risk_repository, audit_service=audit_service).get_history(
        staging_users[personnel_ids[0]],
        personnel_ids[0],
        page=1,
        page_size=100,
    )
    if own_history['total'] < len(evaluation_offsets[personnel_ids[0]]):
        raise RuntimeError('Synthetic personnel risk history did not return the generated observations')

    dashboard = WelfareDashboardService(
        personnel_repository,
        risk_repository,
        alert_repository,
        intervention_repository,
        audit_service=audit_service,
    ).get_summary(seed_identity)
    risk_summary = RiskHistoryService(risk_repository, audit_service=audit_service).get_summary(seed_identity)
    if not dashboard['personnel']['total_authorized'] or risk_summary['total_personnel'] < personnel_count:
        raise RuntimeError('Dashboard or risk-summary verification did not include the seeded dataset')
    if personnel_ids[0] not in staging_users:
        raise RuntimeError('The first synthetic personnel does not have a development account')
    try:
        risk_service.predict_for_personnel(
            personnel_ids[1],
            reference_date=as_of.isoformat(),
            identity=staging_users[personnel_ids[0]],
        )
    except PermissionError:
        cross_personnel_denied = True
    else:
        raise RuntimeError('Synthetic personnel account was able to access another personnel member')
    explanation = risk_service.explain_for_personnel(
        personnel_ids[0],
        reference_date=as_of.isoformat(),
        identity={**staging_users[personnel_ids[0]], 'seed_batch_id': batch_id},
    )
    if not explanation.get('explanation'):
        raise RuntimeError('SHAP explanation verification returned no explanation')

    current_consent_count = sum(
        consent_service.has_personnel_consent(personnel_id, CONSENT_WELLNESS_DATA_PROCESSING)
        for personnel_id in personnel_ids
    )
    if current_consent_count != len(consented_indices):
        raise RuntimeError('Current consent distribution does not match the generated synthetic scenarios')
    if len(verified['users']) != personnel_count + len(ROLE_USER_DEFINITIONS):
        raise RuntimeError('Synthetic account count does not match the staging role population')
    return {
        'batch_id': batch_id,
        'database_name': get_database_name(),
        'personnel_count': len(verified['personnel']),
        'users_created': len(verified['users']),
        'users_by_role': verified['users_by_role'],
        'personnel_user_mappings': sum(bool(item.get('personnel_id')) for item in verified['users']),
        'wellness_consent_current': current_consent_count,
        'wellness_consent_revoked': len(revoked_indices),
        'wellness_consent_none': len(no_consent_indices),
        'operational_records': verified['operational'],
        'wellness_assessments': len(verified['wellness']),
        'risk_predictions': len(verified['predictions']),
        'risk_predictions_generated': len(verified['predictions']),
        'alerts': len(verified['alerts']),
        'interventions': len(verified['interventions']),
        'intervention_statuses': intervention_statuses,
        'interventions_created': interventions_created,
        'dashboard_verified': True,
        'risk_summary_verified': True,
        'shap_verified': True,
        'own_resource_verified': own_history['total'] > 0,
        'cross_personnel_denied': cross_personnel_denied,
        'operational_scenarios': operational_result['scenarios'],
        'random_seed': random_seed,
    }


def print_seed_report(report):
    print('Synthetic staging seed completed')
    print(f"Dataset: {report['batch_id']}")
    print(f"Personnel created: {report['personnel_count']}")
    print(f"Staging users: {report['users_created']}")
    print(f"Staging users by role: {report['users_by_role']}")
    print(f"Personnel-user mappings: {report['personnel_user_mappings']}")
    print(
        'Wellness consent (current / revoked / none): '
        f"{report['wellness_consent_current']} / "
        f"{report['wellness_consent_revoked']} / "
        f"{report['wellness_consent_none']}"
    )
    print(f"Operational records: {sum(report['operational_records'].values())}")
    print(f"Wellness assessments: {report['wellness_assessments']}")
    print(f"Risk predictions: {report['risk_predictions']}")
    print(f"Alerts: {report['alerts']}")
    print(f"Interventions: {report['interventions']}")
    print(f"Dashboard / risk summary / SHAP verified: {report['dashboard_verified']} / {report['risk_summary_verified']} / {report['shap_verified']}")
    print(f"Own-resource / cross-personnel access verified: {report['own_resource_verified']} / {report['cross_personnel_denied']}")
    print(f"Reproducibility seed: {report['random_seed']}")


if __name__ == '__main__':
    print_seed_report(seed_staging_dataset())
