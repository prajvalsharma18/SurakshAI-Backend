"""Allow-listed, persistent orchestration for isolated risk-model candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
from threading import Lock
from uuid import uuid4

from src.ml.feature_schema import MODEL_FEATURES
from src.ml.risk_model import RiskModel, RiskModelError
from src.security.audit import RESULT_FAILURE, RESULT_SUCCESS, get_audit_service


BASE_DIR = Path(__file__).resolve().parents[2]
RISK_MODEL_DIR = BASE_DIR / 'models' / 'risk'
CANDIDATE_DIR = RISK_MODEL_DIR / 'candidates'
JOBS_COLLECTION = 'model_training_jobs'

_CONFIG_DEFAULTS = {
    'n_estimators': 300,
    'learning_rate': 0.05,
    'max_depth': 6,
    'subsample': 0.9,
    'colsample_bytree': 0.9,
}
_CONFIG_LIMITS = {
    'n_estimators': (50, 1000),
    'learning_rate': (0.001, 0.3),
    'max_depth': (2, 12),
    'subsample': (0.5, 1.0),
    'colsample_bytree': (0.5, 1.0),
}
_PARAMETER_RE = re.compile(r'([a-z_]+)\s*=\s*(\d+(?:\.\d+)?)')
_INSTRUCTION_RE = re.compile(r'^train risk model(?: with (.+))?$')
_JOB_ID_RE = re.compile(r'^[0-9a-f]{32}$')
_model_lock = Lock()


@dataclass(frozen=True)
class TrainingConfig:
    """Only model hyperparameters explicitly exposed to administrators."""

    n_estimators: int = 300
    learning_rate: float = 0.05
    max_depth: int = 6
    subsample: float = 0.9
    colsample_bytree: float = 0.9

    def to_dict(self):
        return asdict(self)


def validate_training_config(raw):
    if not isinstance(raw, dict):
        raise ValueError('config must be an object')
    unknown = set(raw) - set(_CONFIG_DEFAULTS)
    if unknown:
        raise ValueError('config contains unsupported fields')
    values = dict(_CONFIG_DEFAULTS)
    values.update(raw)
    for key, (minimum, maximum) in _CONFIG_LIMITS.items():
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError('config values must be numeric')
        if not math.isfinite(value) or value < minimum or value > maximum:
            raise ValueError('config value is outside its allowed range')
    for key in ('n_estimators', 'max_depth'):
        if not isinstance(values[key], int):
            raise ValueError('integer config values must be whole numbers')
    return TrainingConfig(**values)


def parse_training_instruction(instruction):
    """Parse only the single documented training command; reject all ambiguity."""
    if not isinstance(instruction, str) or len(instruction) > 300:
        raise ValueError('instruction is not supported')
    normalized = ' '.join(instruction.strip().lower().split())
    # Natural-language requests are accepted only when they unambiguously ask
    # for the one approved candidate pipeline. The text never becomes code or
    # a path, and explicit operational/destructive verbs fail closed.
    safe_requests = {
        'train a new candidate risk model using the latest approved dataset.',
        'train a new candidate risk model using the latest approved dataset',
        'train a new candidate using the latest approved dataset.',
        'train a new candidate using the latest approved dataset',
        'create a candidate xgboost model using the current 31-feature schema.',
        'create a candidate xgboost model using the current 31-feature schema',
    }
    if normalized in safe_requests:
        return TrainingConfig()
    match = _INSTRUCTION_RE.fullmatch(instruction.strip().lower())
    if not match:
        raise ValueError('instruction is not supported')
    arguments = match.group(1)
    if arguments is None:
        return TrainingConfig()
    config = {}
    tokens = arguments.split(',')
    if any(not token.strip() for token in tokens):
        raise ValueError('instruction is not supported')
    for token in tokens:
        value_match = _PARAMETER_RE.fullmatch(token.strip())
        if value_match is None:
            raise ValueError('instruction is not supported')
        key, value = value_match.groups()
        if key not in _CONFIG_DEFAULTS or key in config:
            raise ValueError('instruction contains unsupported or duplicate parameters')
        config[key] = float(value) if '.' in value else int(value)
    return validate_training_config(config)


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


class TrainingJobRepository:
    """Mongo-backed job persistence; queued work survives web process restarts."""

    def __init__(self, database=None):
        self.database = database
        self.collection = None

    def _collection(self):
        if self.collection is None and self.database is None:
            from src.db.mongodb import get_database
            self.database = get_database()
        if self.collection is None and self.database is not None:
            self.collection = self.database.get_collection(JOBS_COLLECTION)
            self.collection.create_index('job_id', unique=True)
            self.collection.create_index([('status', 1), ('created_at', 1)])
        if self.collection is None:
            raise RuntimeError('Training job storage is unavailable')
        return self.collection

    def create(self, job):
        self._collection().insert_one(dict(job))
        return dict(job)

    def get(self, job_id):
        return self._collection().find_one({'job_id': job_id})

    def list(self, status=None, limit=50):
        query = {'status': status} if status else {}
        records = list(self._collection().find(query))
        records.sort(key=lambda item: item.get('created_at', ''), reverse=True)
        return records[:limit]

    def update(self, job_id, updates, expected_status=None):
        query = {'job_id': job_id}
        if expected_status is not None:
            query['status'] = expected_status
        result = self._collection().update_one(query, {'$set': dict(updates)})
        if getattr(result, 'matched_count', 1) == 0:
            return False
        return True


class ModelTrainingService:
    def __init__(self, repository, audit_service=None, candidates_dir=None, risk_model_dir=None):
        self.repository = repository
        self.audit_service = audit_service or get_audit_service()
        self.candidates_dir = Path(candidates_dir or CANDIDATE_DIR)
        self.risk_model_dir = Path(risk_model_dir or RISK_MODEL_DIR)

    def create_plan(self, *, actor, instruction=None, config=None):
        if (actor or {}).get('role') != 'ADMIN':
            raise PermissionError('Only ADMIN may create training plans')
        if (instruction is None) == (config is None):
            raise ValueError('provide exactly one of instruction or config')
        parsed = parse_training_instruction(instruction) if instruction is not None else validate_training_config(config)
        job_id = uuid4().hex
        now = _utc_now()
        record = {
            'job_id': job_id,
            'status': 'PLANNED',
            'config': parsed.to_dict(),
            'request_text': instruction if instruction is not None else None,
            'requested_by': actor.get('username') or actor.get('user_id'),
            'dataset_id': 'surakshai_phase4_synthetic_risk_dataset',
            'feature_version': 'surakshai-phase4-feature-v1',
            'created_at': now,
            'updated_at': now,
            'created_by': actor.get('user_id'),
        }
        self.repository.create(record)
        self._audit(actor, 'MODEL_TRAINING_PLANNED', job_id, RESULT_SUCCESS)
        return record

    def confirm_plan(self, *, actor, job_id):
        if (actor or {}).get('role') != 'ADMIN':
            raise PermissionError('Only ADMIN may confirm training plans')
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise ValueError('job_id is invalid')
        record = self.repository.get(job_id)
        if record is None:
            return None
        if record.get('status') == 'QUEUED':
            return record
        if record.get('status') != 'PLANNED':
            raise ValueError('Only a planned job can be confirmed')
        changed = self.repository.update(
            job_id,
            {'status': 'QUEUED', 'updated_at': _utc_now(), 'confirmed_by': actor.get('user_id')},
            expected_status='PLANNED',
        )
        if not changed:
            latest = self.repository.get(job_id)
            if latest and latest.get('status') == 'QUEUED':
                return latest
            raise ValueError('Training plan is no longer confirmable')
        self._audit(actor, 'MODEL_TRAINING_CONFIRMED', job_id, RESULT_SUCCESS)
        return self.repository.get(job_id)

    def get_job(self, job_id):
        if not isinstance(job_id, str) or not _JOB_ID_RE.fullmatch(job_id):
            raise ValueError('job_id is invalid')
        return self.repository.get(job_id)

    def list_jobs(self, status=None, limit=50):
        if status is not None and status not in {'PLANNED', 'QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'PROMOTED'}:
            raise ValueError('status is invalid')
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError('limit must be between 1 and 100')
        return self.repository.list(status=status, limit=limit)

    def process_next(self, worker_id='model-training-worker'):
        self._recover_stale_jobs()
        queued = self.repository.list(status='QUEUED', limit=100)
        for record in reversed(queued):
            job_id = record.get('job_id')
            if not self.repository.update(
                job_id,
                {'status': 'RUNNING', 'started_at': _utc_now(), 'updated_at': _utc_now(), 'worker_id': worker_id},
                expected_status='QUEUED',
            ):
                continue
            queued_record = self.repository.get(job_id) or record
            self._audit(
                {'user_id': queued_record.get('created_by'), 'role': 'ADMIN'},
                'MODEL_TRAINING_STARTED', job_id, RESULT_SUCCESS,
            )
            self._execute(job_id)
            return job_id
        return None

    def _recover_stale_jobs(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        for record in self.repository.list(status='RUNNING', limit=100):
            try:
                updated_at = datetime.fromisoformat(record['updated_at'].replace('Z', '+00:00'))
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=timezone.utc)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if updated_at < cutoff:
                changed = self.repository.update(
                    record['job_id'],
                    {
                        'status': 'FAILED',
                        'updated_at': _utc_now(),
                        'completed_at': _utc_now(),
                        'failure': {
                            'code': 'WORKER_TIMEOUT',
                            'message': 'Training worker stopped before completing this job; no automatic retry was attempted.',
                        },
                    },
                    expected_status='RUNNING',
                )
                if changed:
                    self._audit(
                        {'user_id': record.get('created_by'), 'role': 'ADMIN'},
                        'MODEL_TRAINING_FAILED', record['job_id'], RESULT_FAILURE,
                    )

    def _execute(self, job_id):
        record = self.repository.get(job_id)
        output_dir = self.candidates_dir / job_id
        try:
            from scripts.train_phase4_risk_model import train_to_output
            trained = train_to_output(
                output_dir,
                training_config=record['config'],
                model_version=f'surakshai-risk-candidate-{job_id}',
            )
            metadata_path = output_dir / 'metadata.json'
            metadata = trained['metadata']
            metadata.update({
                'dataset_id': 'surakshai_phase4_synthetic_risk_dataset',
                'feature_version': metadata.get('feature_version', 'surakshai-phase4-feature-v1'),
                'training_job_id': job_id,
                'training_status': 'RUNNING',
                'training_timestamp': _utc_now(),
            })
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
            self._validate_candidate(output_dir)
            metadata['training_status'] = 'SUCCEEDED'
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
            metrics = trained['metadata']['training_metrics']
            result = {
                'model_id': job_id,
                'model_version': trained['metadata']['model_version'],
                'dataset_id': 'surakshai_phase4_synthetic_risk_dataset',
                'feature_version': trained['metadata']['feature_version'],
                'trained_at': trained['metadata']['training_timestamp'],
                'metrics': metrics,
                'feature_count': trained['metadata']['feature_count'],
                'artifact_reference': str((output_dir / 'surakshai_risk_model.json').resolve()),
            }
            self.repository.update(job_id, {
                'status': 'SUCCEEDED',
                'completed_at': _utc_now(),
                'updated_at': _utc_now(),
                'candidate': result,
            }, expected_status='RUNNING')
            self._audit(
                {'user_id': record.get('created_by'), 'role': 'ADMIN'},
                'MODEL_TRAINING_SUCCEEDED',
                job_id,
                RESULT_SUCCESS,
            )
        except Exception:
            self.repository.update(job_id, {
                'status': 'FAILED',
                'completed_at': _utc_now(),
                'updated_at': _utc_now(),
                'failure': {'code': 'TRAINING_OR_VALIDATION_FAILED', 'message': 'Candidate training or validation failed'},
            }, expected_status='RUNNING')
            self._audit(
                {'user_id': record.get('created_by'), 'role': 'ADMIN'},
                'MODEL_TRAINING_FAILED',
                job_id,
                RESULT_FAILURE,
            )

    @staticmethod
    def _validate_candidate(directory):
        directory = Path(directory).resolve()
        model_path = directory / 'surakshai_risk_model.json'
        metadata_path = directory / 'metadata.json'
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        model_version = metadata.get('model_version')
        if not isinstance(model_version, str) or not re.fullmatch(
            r'surakshai-risk-candidate-[0-9a-f]{32}', model_version
        ):
            raise RiskModelError('Candidate model version is invalid')
        if metadata.get('feature_version') != 'surakshai-phase4-feature-v1':
            raise RiskModelError('Candidate feature version is incompatible')
        if metadata.get('dataset_id') != 'surakshai_phase4_synthetic_risk_dataset':
            raise RiskModelError('Candidate dataset is not approved')
        if metadata.get('feature_list') != MODEL_FEATURES or metadata.get('feature_count') != len(MODEL_FEATURES):
            raise RiskModelError('Candidate feature schema mismatch')
        if metadata.get('classes') != {'0': 'LOW', '1': 'ELEVATED', '2': 'HIGH'}:
            raise RiskModelError('Candidate classes mismatch')
        metrics = metadata.get('training_metrics')
        if not isinstance(metrics, dict):
            raise RiskModelError('Candidate metrics are missing')
        for key in ('accuracy', 'precision', 'recall', 'macro_f1'):
            value = metrics.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise RiskModelError('Candidate metrics are invalid')
        RiskModel(model_path=model_path, metadata_path=metadata_path)
        return metadata

    def list_models(self):
        active = self._read_pointer(self.risk_model_dir / 'active.json')
        records = self.repository.list(limit=100)
        models = []
        for record in records:
            candidate = record.get('candidate')
            if candidate:
                models.append({
                    **candidate,
                    'status': 'ACTIVE' if active and active.get('model_id') == candidate['model_id'] else record['status'],
                    'created_at': record.get('created_at'),
                })
        baseline_metadata_path = self.risk_model_dir / 'metadata.json'
        if baseline_metadata_path.exists():
            baseline_metadata = json.loads(baseline_metadata_path.read_text(encoding='utf-8'))
            models.append({
                'model_id': 'baseline',
                'model_version': baseline_metadata.get('model_version'),
                'feature_count': baseline_metadata.get('feature_count'),
                'metrics': baseline_metadata.get('training_metrics'),
                'status': 'ACTIVE' if not active else 'ROLLBACK_AVAILABLE',
            })
        return {'active_model_id': (active or {}).get('model_id', 'baseline'), 'models': models}

    def promote(self, *, actor, model_id):
        if (actor or {}).get('role') != 'ADMIN':
            raise PermissionError('Only ADMIN may promote a model')
        if not isinstance(model_id, str) or not _JOB_ID_RE.fullmatch(model_id):
            raise ValueError('model_id is invalid')
        record = self.repository.get(model_id)
        if record is None or record.get('status') != 'SUCCEEDED':
            raise ValueError('Only a successfully validated candidate can be promoted')
        candidate_dir = (self.candidates_dir / model_id).resolve()
        if candidate_dir.parent != self.candidates_dir.resolve():
            raise ValueError('Candidate artifact is invalid')
        metadata = self._validate_candidate(candidate_dir)
        if metadata.get('training_job_id') != model_id or metadata.get('training_status') != 'SUCCEEDED':
            raise ValueError('Candidate training metadata is incomplete')
        with _model_lock:
            pointer_path = self.risk_model_dir / 'active.json'
            previous = self._read_pointer(pointer_path)
            if previous and previous.get('model_id') == model_id:
                raise ValueError('Candidate is already active')
            if not previous and metadata.get('model_version') == json.loads(
                (self.risk_model_dir / 'metadata.json').read_text(encoding='utf-8')
            ).get('model_version'):
                raise ValueError('Candidate is already active')
            if previous is None:
                baseline = json.loads((self.risk_model_dir / 'metadata.json').read_text(encoding='utf-8'))
                previous = {'model_id': 'baseline', 'model_version': baseline.get('model_version')}
            self._atomic_json(self.risk_model_dir / 'rollback.json', previous)
            active = {'model_id': model_id, 'model_version': metadata['model_version'], 'promoted_at': _utc_now()}
            self._atomic_json(pointer_path, active)
        self.repository.update(model_id, {
            'status': 'PROMOTED',
            'updated_at': _utc_now(),
            'promoted_by': actor.get('user_id'),
        })
        self._audit(actor, 'MODEL_PROMOTED', model_id, RESULT_SUCCESS)
        return {'active_model_id': model_id, 'model_version': metadata['model_version'], 'rollback_model_id': previous['model_id']}

    def _audit(self, actor, action, job_id, result):
        self.audit_service.record_event(
            actor_user_id=actor.get('user_id'),
            actor_role=actor.get('role'),
            action=action,
            resource_type='risk_model_training',
            resource_id=job_id,
            result=result,
            source='model_training_api',
            purpose='Manage validated risk-model candidates',
        )

    @staticmethod
    def _read_pointer(path):
        if not path.exists():
            return None
        pointer = json.loads(path.read_text(encoding='utf-8'))
        model_id = pointer.get('model_id') if isinstance(pointer, dict) else None
        if model_id != 'baseline' and (not isinstance(model_id, str) or not _JOB_ID_RE.fullmatch(model_id)):
            raise RiskModelError('Active model pointer is invalid')
        return pointer

    @staticmethod
    def _atomic_json(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
        temporary_path.write_text(json.dumps(payload, indent=2), encoding='utf-8')
        os.replace(temporary_path, path)
