import json
from pathlib import Path
import shutil
from threading import Barrier, Lock
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

from src.ml.feature_schema import MODEL_FEATURES
from src.ml.risk_model import RiskModel, RiskModelError
from src.ml.training_service import (
    ModelTrainingService,
    TrainingConfig,
    parse_training_instruction,
    validate_training_config,
)
from src.security.audit import AuditService


class MemoryTrainingJobRepository:
    def __init__(self):
        self.records = {}
        self.lock = Lock()

    def create(self, job):
        self.records[job['job_id']] = dict(job)
        return dict(job)

    def get(self, job_id):
        record = self.records.get(job_id)
        return dict(record) if record else None

    def list(self, status=None, limit=50):
        records = list(self.records.values())
        if status:
            records = [record for record in records if record['status'] == status]
        return [dict(record) for record in records[:limit]]

    def update(self, job_id, updates, expected_status=None):
        with self.lock:
            record = self.records.get(job_id)
            if record is None or (expected_status and record['status'] != expected_status):
                return False
            record.update(updates)
            return True


class ModelTrainingTests(unittest.TestCase):
    def setUp(self):
        self.artifacts = Path(__file__).resolve().parent / 'test_artifacts_model_training'
        shutil.rmtree(self.artifacts, ignore_errors=True)
        self.artifacts.mkdir(parents=True)
        self.risk_dir = self.artifacts / 'risk'
        self.candidates_dir = self.risk_dir / 'candidates'
        self.risk_dir.mkdir(parents=True)
        self.baseline_artifact = self.risk_dir / 'surakshai_risk_model.json'
        self.baseline_artifact.write_bytes(b'baseline-preserved')
        self._write_metadata(self.risk_dir / 'metadata.json', 'surakshai-risk-v0.1')
        self.repository = MemoryTrainingJobRepository()
        self.audit = AuditService()
        self.service = ModelTrainingService(
            self.repository,
            audit_service=self.audit,
            candidates_dir=self.candidates_dir,
            risk_model_dir=self.risk_dir,
        )

    def tearDown(self):
        shutil.rmtree(self.artifacts, ignore_errors=True)

    @staticmethod
    def _metadata(model_version):
        return {
            'model_version': model_version,
            'feature_version': 'surakshai-phase4-feature-v1',
            'feature_count': len(MODEL_FEATURES),
            'feature_list': MODEL_FEATURES,
            'classes': {'0': 'LOW', '1': 'ELEVATED', '2': 'HIGH'},
            'training_metrics': {
                'accuracy': 0.6,
                'precision': 0.6,
                'recall': 0.6,
                'macro_f1': 0.6,
            },
        }

    @classmethod
    def _write_metadata(cls, path, model_version):
        path.write_text(json.dumps(cls._metadata(model_version)), encoding='utf-8')

    def _mock_training(self, output_dir, training_config, model_version):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / 'surakshai_risk_model.json').write_bytes(b'candidate-artifact')
        metadata = self._metadata(model_version)
        metadata['training_config'] = training_config
        (output_dir / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        return {
            'metadata': metadata,
            'model_path': str(output_dir / 'surakshai_risk_model.json'),
            'metadata_path': str(output_dir / 'metadata.json'),
        }

    def test_config_and_instruction_parser_fail_closed(self):
        self.assertEqual(parse_training_instruction('train risk model'), TrainingConfig())
        parsed = parse_training_instruction(
            'train risk model with n_estimators=350, learning_rate=0.1, max_depth=5'
        )
        self.assertEqual(parsed.n_estimators, 350)
        self.assertEqual(parsed.learning_rate, 0.1)
        self.assertEqual(parsed.max_depth, 5)
        self.assertEqual(validate_training_config({'max_depth': 7}).max_depth, 7)
        for instruction in (
            'train risk model and delete the baseline',
            'train risk model with dataset=../../secret.csv',
            'train risk model with n_estimators=1001',
            'train risk model with n_estimators=300, n_estimators=301',
            'train risk model with max_depth=6.5',
        ):
            with self.subTest(instruction=instruction), self.assertRaises(ValueError):
                parse_training_instruction(instruction)
        with self.assertRaises(ValueError):
            validate_training_config({'output_path': 'outside'})

    def test_supported_natural_language_is_normalized_to_the_allowlisted_config(self):
        for request in (
            'Train a new candidate risk model using the latest approved dataset.',
            'Create a candidate XGBoost model using the current 31-feature schema.',
        ):
            with self.subTest(request=request):
                self.assertEqual(parse_training_instruction(request), TrainingConfig())
        for request in (
            'Delete the current model.',
            'Run this Python code.',
            'Execute this shell command.',
            'Drop the Mongo database.',
            'Change the JWT secret.',
            'Deploy this model directly.',
            'Train a candidate on ../../secret.csv.',
        ):
            with self.subTest(request=request), self.assertRaises(ValueError):
                parse_training_instruction(request)

    def test_training_is_queued_and_candidate_promotion_preserves_rollback(self):
        actor = {'user_id': 'admin-1', 'role': 'ADMIN'}
        plan = self.service.create_plan(actor=actor, instruction='train risk model')
        job_id = plan['job_id']
        self.assertEqual(plan['status'], 'PLANNED')
        self.service.confirm_plan(actor=actor, job_id=job_id)
        self.assertEqual(self.repository.get(job_id)['status'], 'QUEUED')
        self.assertFalse((self.candidates_dir / job_id).exists())

        with patch(
            'scripts.train_phase4_risk_model.train_to_output',
            side_effect=self._mock_training,
        ), patch('src.ml.training_service.RiskModel'):
            self.assertEqual(self.service.process_next(), job_id)
            self.assertEqual(self.repository.get(job_id)['status'], 'SUCCEEDED')
            metadata = json.loads((self.candidates_dir / job_id / 'metadata.json').read_text(encoding='utf-8'))
            self.assertEqual(metadata['dataset_id'], 'surakshai_phase4_synthetic_risk_dataset')
            self.assertEqual(metadata['feature_version'], 'surakshai-phase4-feature-v1')
            self.assertEqual(metadata['training_job_id'], job_id)
            self.assertEqual(metadata['training_status'], 'SUCCEEDED')
            self.assertIn('macro_f1', metadata['training_metrics'])
            self.assertFalse((self.risk_dir / 'active.json').exists())
            self.assertEqual(self.baseline_artifact.read_bytes(), b'baseline-preserved')
            result = self.service.promote(actor=actor, model_id=job_id)

        self.assertEqual(result['active_model_id'], job_id)
        active = json.loads((self.risk_dir / 'active.json').read_text(encoding='utf-8'))
        rollback = json.loads((self.risk_dir / 'rollback.json').read_text(encoding='utf-8'))
        self.assertEqual(active['model_id'], job_id)
        self.assertEqual(rollback['model_id'], 'baseline')
        self.assertEqual(self.baseline_artifact.read_bytes(), b'baseline-preserved')
        self.assertTrue((self.candidates_dir / job_id / 'surakshai_risk_model.json').exists())
        self.assertTrue(any(item['action'] == 'MODEL_PROMOTED' for item in self.audit.list_events()))
        with self.assertRaises(ValueError):
            self.service.promote(actor=actor, model_id=job_id)
        actions = {item['action'] for item in self.audit.list_events()}
        self.assertIn('MODEL_TRAINING_STARTED', actions)
        self.assertIn('MODEL_TRAINING_SUCCEEDED', actions)

    def test_two_workers_atomically_claim_only_one_execution(self):
        actor = {'user_id': 'admin-1', 'username': 'admin', 'role': 'ADMIN'}
        plan = self.service.create_plan(actor=actor, instruction='train risk model')
        self.service.confirm_plan(actor=actor, job_id=plan['job_id'])
        gate = Barrier(2)

        def process(worker_id):
            gate.wait(timeout=3)
            return self.service.process_next(worker_id=worker_id)

        def inspect_running_state(output_dir, training_config, model_version):
            self.assertEqual(self.repository.get(plan['job_id'])['status'], 'RUNNING')
            return self._mock_training(output_dir, training_config, model_version)

        with patch(
            'scripts.train_phase4_risk_model.train_to_output',
            side_effect=inspect_running_state,
        ) as train, patch('src.ml.training_service.RiskModel'):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(process, ('worker-a', 'worker-b')))

        self.assertEqual(results.count(plan['job_id']), 1)
        self.assertEqual(results.count(None), 1)
        train.assert_called_once()
        record = self.repository.get(plan['job_id'])
        self.assertEqual(record['status'], 'SUCCEEDED')
        self.assertTrue(record.get('started_at'))
        self.assertTrue(record.get('completed_at'))

    def test_training_failure_is_sanitized_and_not_retried(self):
        actor = {'user_id': 'admin-1', 'role': 'ADMIN'}
        plan = self.service.create_plan(actor=actor, instruction='train risk model')
        self.service.confirm_plan(actor=actor, job_id=plan['job_id'])
        with patch(
            'scripts.train_phase4_risk_model.train_to_output',
            side_effect=RuntimeError('sensitive stack details'),
        ) as train:
            self.assertEqual(self.service.process_next(), plan['job_id'])
            self.assertIsNone(self.service.process_next())
        train.assert_called_once()
        failed = self.repository.get(plan['job_id'])
        self.assertEqual(failed['status'], 'FAILED')
        self.assertNotIn('sensitive', failed['failure']['message'])
        self.assertFalse((self.risk_dir / 'active.json').exists())
        self.assertTrue(any(item['action'] == 'MODEL_TRAINING_FAILED' for item in self.audit.list_events()))

    def test_stale_worker_job_fails_closed_without_automatic_retry(self):
        actor = {'user_id': 'admin-1', 'role': 'ADMIN'}
        plan = self.service.create_plan(actor=actor, instruction='train risk model')
        self.repository.update(plan['job_id'], {
            'status': 'RUNNING',
            'updated_at': '2000-01-01T00:00:00+00:00',
        })
        with patch('scripts.train_phase4_risk_model.train_to_output') as train:
            self.assertIsNone(self.service.process_next())
        train.assert_not_called()
        failed = self.repository.get(plan['job_id'])
        self.assertEqual(failed['status'], 'FAILED')
        self.assertEqual(failed['failure']['code'], 'WORKER_TIMEOUT')

    def test_runtime_model_refreshes_from_active_pointer_without_restart(self):
        candidate_id = 'a' * 32
        model_version = f'surakshai-risk-candidate-{candidate_id}'
        candidate_dir = self.candidates_dir / candidate_id
        candidate_dir.mkdir(parents=True)
        metadata = self._metadata(model_version)
        metadata.update({
            'feature_version': 'surakshai-phase4-feature-v1',
            'dataset_id': 'surakshai_phase4_synthetic_risk_dataset',
        })
        (candidate_dir / 'metadata.json').write_text(json.dumps(metadata), encoding='utf-8')
        (candidate_dir / 'surakshai_risk_model.json').write_bytes(b'candidate')
        with patch.object(RiskModel, '_load_model', lambda model: setattr(model, '_model', object())):
            runtime_model = RiskModel(risk_model_dir=self.risk_dir)
            self.assertEqual(runtime_model.model_version, 'surakshai-risk-v0.1')
            ModelTrainingService._atomic_json(
                self.risk_dir / 'active.json',
                {'model_id': candidate_id, 'model_version': model_version},
            )
            self.assertEqual(runtime_model.model_version, model_version)
            self.assertEqual(runtime_model.model_path.parent.name, candidate_id)

    def test_promotion_rejects_incompatible_metadata_and_missing_artifact(self):
        actor = {'user_id': 'admin-1', 'role': 'ADMIN'}
        plan = self.service.create_plan(actor=actor, instruction='train risk model')
        self.service.confirm_plan(actor=actor, job_id=plan['job_id'])
        with patch(
            'scripts.train_phase4_risk_model.train_to_output',
            side_effect=self._mock_training,
        ), patch('src.ml.training_service.RiskModel'):
            self.service.process_next()

        candidate_dir = self.candidates_dir / plan['job_id']
        metadata_path = candidate_dir / 'metadata.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        metadata['feature_version'] = 'unsupported-feature-v99'
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
        with self.assertRaises(RiskModelError):
            self.service.promote(actor=actor, model_id=plan['job_id'])

        metadata['feature_version'] = 'surakshai-phase4-feature-v1'
        metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
        (candidate_dir / 'surakshai_risk_model.json').unlink()
        with self.assertRaises((RiskModelError, FileNotFoundError)):
            self.service.promote(actor=actor, model_id=plan['job_id'])
        self.assertFalse((self.risk_dir / 'active.json').exists())

    def test_only_validated_successful_candidate_can_be_promoted(self):
        with self.assertRaises(ValueError):
            self.service.promote(
                actor={'user_id': 'admin-1', 'role': 'ADMIN'},
                model_id='0' * 32,
            )
        self.assertFalse((self.risk_dir / 'active.json').exists())


class ModelTrainingApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import api_server
        from tests.user_test_support import install_test_users

        cls.api_server = api_server
        install_test_users(api_server, personnel_ids=('P001', 'P002', 'P003', 'P900'))
        cls.client = api_server.app.test_client()

    def setUp(self):
        self.repository = MemoryTrainingJobRepository()
        self.service = ModelTrainingService(self.repository, audit_service=AuditService())
        self.service_patch = patch.object(
            self.api_server,
            '_get_model_training_service',
            return_value=self.service,
        )
        self.service_patch.start()
        self.admin_token = self._token('demo_admin', 'demo-admin-password')
        self.personnel_token = self._token('demo_personnel', 'demo-personnel-password')
        self.welfare_token = self._token('demo_welfare', 'demo-welfare-password')
        self.commander_token = self._token('demo_commander', 'demo-commander-password')

    def tearDown(self):
        self.service_patch.stop()

    def _token(self, username, password):
        response = self.client.post('/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200)
        return response.get_json()['access_token']

    def test_admin_can_plan_confirm_list_and_read_while_welfare_is_denied(self):
        headers = {'Authorization': f'Bearer {self.admin_token}'}
        response = self.client.post(
            '/admin/model-training/plan',
            json={'instruction': 'train risk model'},
            headers=headers,
        )
        self.assertEqual(response.status_code, 201)
        plan_id = response.get_json()['plan_id']
        self.assertEqual(response.get_json()['status'], 'PLANNED')

        for role, token in (
            ('PERSONNEL', self.personnel_token),
            ('WELFARE_OFFICER', self.welfare_token),
            ('COMMANDER', self.commander_token),
        ):
            with self.subTest(role=role):
                denied = self.client.post(
                    '/admin/model-training/confirm',
                    json={'plan_id': plan_id},
                    headers={'Authorization': f'Bearer {token}'},
                )
                self.assertEqual(denied.status_code, 403)

        confirmed = self.client.post(
            '/admin/model-training/confirm',
            json={'plan_id': plan_id},
            headers=headers,
        )
        self.assertEqual(confirmed.status_code, 202)
        listed = self.client.get('/admin/model-training/jobs?status=QUEUED', headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.get_json()['jobs'][0]['job_id'], plan_id)
        detail = self.client.get(f'/admin/model-training/jobs/{plan_id}', headers=headers)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.get_json()['status'], 'QUEUED')

    def test_plan_rejects_unknown_config_and_unauthenticated_requests(self):
        unauthenticated = self.client.get('/admin/models')
        self.assertEqual(unauthenticated.status_code, 401)
        response = self.client.post(
            '/admin/model-training/plan',
            json={'config': {'dataset_path': 'arbitrary.csv'}},
            headers={'Authorization': f'Bearer {self.admin_token}'},
        )
        self.assertEqual(response.status_code, 400)

    def test_mobile_contract_creates_review_plan_then_explicitly_queues_job(self):
        headers = {'Authorization': f'Bearer {self.admin_token}'}
        planned = self.client.post(
            '/admin/model-training/plan',
            json={'request_text': 'Train a new candidate risk model using the latest approved dataset.'},
            headers=headers,
        )
        self.assertEqual(planned.status_code, 201)
        plan = planned.get_json()
        self.assertEqual(plan['status'], 'AWAITING_CONFIRMATION')
        self.assertTrue(plan['confirmation_required'])
        self.assertEqual(self.repository.get(plan['plan_id'])['status'], 'PLANNED')

        started = self.client.post(
            '/admin/model-training/jobs',
            json={'plan_id': plan['plan_id'], 'confirmation': True},
            headers=headers,
        )
        self.assertEqual(started.status_code, 202)
        self.assertEqual(started.get_json()['status'], 'QUEUED')
        self.assertEqual(started.get_json()['requested_by'], 'demo_admin')
        self.assertIsNone(started.get_json()['started_at'])
