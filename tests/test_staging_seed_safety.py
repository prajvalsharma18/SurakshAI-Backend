import os
import unittest
from unittest.mock import Mock, patch

from config import validate_synthetic_seed_configuration
from scripts.seed_staging_dataset import (
    SEED_COLLECTIONS,
    _account_definitions,
    _ensure_consent_state,
    _ensure_user,
    reset_staging_dataset,
)
from scripts.seed_operational_data import _leave_days, _record_id
from src.ml.risk_service import RiskService
from src.schemas.operational import normalize_operational_record


class _DeleteResult:
    def __init__(self, deleted_count):
        self.deleted_count = deleted_count


class _Collection:
    def __init__(self):
        self.records = [
            {'seed_batch_id': 'surakshai-staging-test', 'value': 'synthetic'},
            {'seed_batch_id': 'unrelated-batch', 'value': 'keep'},
            {'value': 'unmarked'},
        ]
        self.delete_query = None

    def delete_many(self, query):
        self.delete_query = query
        before = len(self.records)
        self.records = [
            record for record in self.records
            if record.get('seed_batch_id') != query['seed_batch_id']
        ]
        return _DeleteResult(before - len(self.records))


class _Database:
    def __init__(self):
        self.collections = {name: _Collection() for name in SEED_COLLECTIONS}

    def get_collection(self, name):
        return self.collections[name]


class _Consent:
    @staticmethod
    def has_consent(user_id, consent_type):
        return True


class _WellnessRepository:
    def __init__(self):
        self.query = None

    def list_for_personnel(self, personnel_id, *, end_date=None):
        self.query = {'personnel_id': personnel_id, 'end_date': end_date}
        return [{'assessment_date': end_date}]


class StagingSeedSafetyTests(unittest.TestCase):
    def test_staging_account_definitions_cover_all_roles_and_personnel_mappings(self):
        accounts = _account_definitions(['P900', 'P901'])
        role_counts = {
            role: sum(account[2] == role for account in accounts)
            for role in ('PERSONNEL', 'WELFARE_OFFICER', 'COMMANDER', 'ADMIN')
        }
        self.assertEqual(role_counts, {
            'PERSONNEL': 2,
            'WELFARE_OFFICER': 3,
            'COMMANDER': 2,
            'ADMIN': 2,
        })
        personnel_accounts = [account for account in accounts if account[2] == 'PERSONNEL']
        self.assertEqual({account[3] for account in personnel_accounts}, {'P900', 'P901'})

    def test_existing_same_batch_user_is_reused_without_recreation(self):
        username = 'stg_personnel_p900'
        user_id = 'seed-user-p900'
        existing = {
            'username': username,
            'user_id': user_id,
            'role': 'PERSONNEL',
            'status': 'ACTIVE',
            'personnel_id': 'P900',
            'seed_batch_id': 'surakshai-staging-test',
        }
        repository = Mock()
        repository.get_by_username.return_value = existing
        service = Mock()
        service.authenticate.return_value = {
            'user_id': user_id,
            'username': username,
            'role': 'PERSONNEL',
            'personnel_id': 'P900',
        }

        _, identity = _ensure_user(
            service,
            repository,
            username=username,
            user_id=user_id,
            role='PERSONNEL',
            personnel_id='P900',
            password='not-printed-test-password',
            batch_id='surakshai-staging-test',
        )

        service.create_user.assert_not_called()
        service.update_user.assert_not_called()
        self.assertEqual(identity['seed_batch_id'], 'surakshai-staging-test')

    def test_consent_state_reuse_does_not_append_duplicate_transition(self):
        service = Mock()
        service.get_history.return_value = [{'status': 'GRANTED'}]

        _ensure_consent_state(
            service,
            'seed-user-p900',
            'P900',
            'WELLNESS_DATA_PROCESSING',
            'GRANTED',
            'surakshai-staging-test',
        )

        service.grant_consent.assert_not_called()
        service.revoke_consent.assert_not_called()

    def test_seed_configuration_refuses_production_even_with_seed_flag(self):
        environment = {
            'APP_ENV': 'production',
            'SURAKSHAI_ENABLE_SYNTHETIC_SEED': '1',
            'MONGODB_DATABASE': 'surakshai_staging',
        }
        with patch.dict(os.environ, environment):
            with self.assertRaisesRegex(ValueError, 'disabled in production'):
                validate_synthetic_seed_configuration()

    def test_seed_configuration_requires_explicit_flag_and_safe_database_name(self):
        environment = {
            'APP_ENV': 'development',
            'SURAKSHAI_ENABLE_SYNTHETIC_SEED': '1',
            'MONGODB_DATABASE': 'surakshai_staging',
        }
        with patch.dict(os.environ, environment):
            self.assertEqual(validate_synthetic_seed_configuration(), 'surakshai_staging')
            with self.assertRaisesRegex(ValueError, 'RESET_SYNTHETIC_DATA'):
                validate_synthetic_seed_configuration(reset=True)
        with patch.dict(os.environ, {**environment, 'MONGODB_DATABASE': 'personnel_welfare'}):
            with self.assertRaisesRegex(ValueError, 'development or staging'):
                validate_synthetic_seed_configuration()
        with patch.dict(os.environ, {**environment, 'SURAKSHAI_ENABLE_SYNTHETIC_SEED': '0'}):
            with self.assertRaisesRegex(ValueError, 'ENABLE_SYNTHETIC_SEED=1'):
                validate_synthetic_seed_configuration()

    def test_reset_deletes_only_documents_with_selected_batch_marker(self):
        environment = {
            'APP_ENV': 'development',
            'SURAKSHAI_ENABLE_SYNTHETIC_SEED': '1',
            'SURAKSHAI_RESET_SYNTHETIC_DATA': '1',
            'MONGODB_DATABASE': 'surakshai_staging',
        }
        database = _Database()
        with patch.dict(os.environ, environment):
            deleted = reset_staging_dataset(database, batch_id='surakshai-staging-test')
        self.assertEqual(set(deleted), set(SEED_COLLECTIONS))
        self.assertTrue(all(count == 1 for count in deleted.values()))
        for collection in database.collections.values():
            self.assertEqual(
                collection.delete_query,
                {'seed_batch_id': 'surakshai-staging-test'},
            )
            self.assertEqual(len(collection.records), 2)
            self.assertNotEqual(collection.records[0]['seed_batch_id'], 'surakshai-staging-test')

    def test_operational_schema_accepts_only_a_valid_internal_batch_marker(self):
        payload = {
            'personnel_id': 'P900',
            'date': '2026-09-28',
            'duty_hours': 8,
            'night_duty': False,
            'shift_type': 'DAY',
            'operational_intensity': 'MODERATE',
            'seed_batch_id': 'surakshai-staging-test',
        }
        record = normalize_operational_record('duty_records', payload)
        self.assertEqual(record['seed_batch_id'], payload['seed_batch_id'])
        with self.assertRaisesRegex(ValueError, 'seed_batch_id'):
            normalize_operational_record(
                'duty_records',
                {**payload, 'seed_batch_id': ' '},
            )

    def test_seeded_operational_record_ids_are_stable_per_batch(self):
        record_id = _record_id('duty_records', 'P900', '2026-09-28', 'surakshai-staging-test')
        self.assertEqual(
            record_id,
            _record_id('duty_records', 'P900', '2026-09-28', 'surakshai-staging-test'),
        )
        self.assertNotEqual(
            record_id,
            _record_id('duty_records', 'P900', '2026-09-28', 'another-batch'),
        )

    def test_recovery_scenario_leave_matches_its_duty_days_off(self):
        leave_offsets = _leave_days('RECOVERY', 75)
        self.assertEqual(leave_offsets, list(range(60, 72)))

    def test_risk_service_does_not_use_wellness_after_reference_date(self):
        repository = _WellnessRepository()
        wellness_service = type('WellnessServiceStub', (), {'repository': repository})()
        risk_service = RiskService(
            feature_service=None,
            wellness_service=wellness_service,
            consent_service=_Consent(),
        )
        assessment = risk_service._authorized_wellness(
            'P900',
            {'user_id': 'synthetic-user'},
            '2026-09-28',
        )
        self.assertEqual(repository.query, {'personnel_id': 'P900', 'end_date': '2026-09-28'})
        self.assertEqual(assessment['assessment_date'], '2026-09-28')


if __name__ == '__main__':
    unittest.main()
