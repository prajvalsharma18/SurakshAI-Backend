"""Thin XGBoost model loader for the Phase 4 SURAKSHAI risk classifier."""

import json
from pathlib import Path
import re
from threading import RLock

import numpy as np

from src.ml.feature_schema import MODEL_FEATURES, TARGET_CLASSES


class RiskModelError(RuntimeError):
    """Raised when the persisted model or metadata is invalid."""


class RiskModel:
    def __init__(self, model_path=None, metadata_path=None, risk_model_dir=None):
        base_dir = Path(__file__).resolve().parents[2]
        self._risk_model_dir = Path(risk_model_dir or base_dir / 'models' / 'risk')
        self._active_pointer_path = (
            self._risk_model_dir / 'active.json'
            if model_path is None and metadata_path is None
            else None
        )
        self._lock = RLock()
        self.model_path = Path(model_path or self._risk_model_dir / 'surakshai_risk_model.json')
        self.metadata_path = Path(metadata_path or self._risk_model_dir / 'metadata.json')
        self._model = None
        self._metadata = {}
        self._active_model_id = 'baseline'
        self._refresh_active_model(force=True)

    def _refresh_active_model(self, force=False):
        if self._active_pointer_path is None:
            if force:
                self._load_metadata()
                self._load_model()
            return
        with self._lock:
            model_id = 'baseline'
            if self._active_pointer_path.exists():
                try:
                    pointer = json.loads(self._active_pointer_path.read_text(encoding='utf-8'))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RiskModelError('Active model pointer is invalid') from exc
                model_id = pointer.get('model_id') if isinstance(pointer, dict) else None
                if model_id != 'baseline' and (
                    not isinstance(model_id, str) or not re.fullmatch(r'[0-9a-f]{32}', model_id)
                ):
                    raise RiskModelError('Active model pointer is invalid')
            if not force and model_id == self._active_model_id:
                return
            if model_id == 'baseline':
                model_path = self._risk_model_dir / 'surakshai_risk_model.json'
                metadata_path = self._risk_model_dir / 'metadata.json'
            else:
                candidate_dir = self._risk_model_dir / 'candidates' / model_id
                model_path = candidate_dir / 'surakshai_risk_model.json'
                metadata_path = candidate_dir / 'metadata.json'
            prior_model_path, prior_metadata_path = self.model_path, self.metadata_path
            self.model_path, self.metadata_path = model_path, metadata_path
            try:
                self._load_metadata()
                self._load_model()
            except Exception:
                self.model_path, self.metadata_path = prior_model_path, prior_metadata_path
                raise
            self._active_model_id = model_id

    @property
    def model_version(self):
        with self._lock:
            self._refresh_active_model()
            return self._metadata.get('model_version', 'unknown')

    def _load_metadata(self):
        if not self.metadata_path.exists():
            raise RiskModelError(f'Model metadata not found at {self.metadata_path}')
        with self.metadata_path.open('r', encoding='utf-8') as handle:
            self._metadata = json.load(handle)
        feature_list = self._metadata.get('feature_list') or self._metadata.get('features') or []
        if feature_list != MODEL_FEATURES:
            raise RiskModelError('Feature metadata does not match the canonical Phase 4 feature contract')
        metadata_classes = self._metadata.get('classes', {})
        if metadata_classes != {'0': 'LOW', '1': 'ELEVATED', '2': 'HIGH'}:
            raise RiskModelError('Metadata class mapping does not match the canonical target classes')
        if self._metadata.get('feature_count') != len(MODEL_FEATURES):
            raise RiskModelError('Metadata feature count does not match the canonical contract')
        if self._metadata.get('feature_version') != 'surakshai-phase4-feature-v1':
            raise RiskModelError('Metadata feature version is incompatible with the canonical contract')

    def _load_model(self):
        if not self.model_path.exists():
            raise RiskModelError(f'Model artifact not found at {self.model_path}')
        try:
            import xgboost as xgb
        except ModuleNotFoundError as exc:
            raise RiskModelError('XGBoost is required to run the SURAKSHAI risk model') from exc
        self._model = xgb.XGBClassifier()
        self._model.load_model(str(self.model_path))
        if getattr(self._model, 'n_features_in_', None) is not None and self._model.n_features_in_ != len(MODEL_FEATURES):
            raise RiskModelError('Persisted model feature count does not match the canonical contract')

    def _ensure_vector(self, values):
        if isinstance(values, dict):
            ordered = [values.get(feature) for feature in MODEL_FEATURES]
        elif isinstance(values, (list, tuple, np.ndarray)):
            ordered = list(values)
        else:
            raise RiskModelError('Feature payload must be a mapping or list-like object')
        if len(ordered) != len(MODEL_FEATURES):
            raise RiskModelError('Feature payload is not the canonical 31-feature vector')
        arr = []
        for value in ordered:
            if value is None:
                arr.append(np.nan)
            else:
                arr.append(float(value) if isinstance(value, (int, float)) else value)
        return np.asarray(arr, dtype=float).reshape(1, -1)

    def predict_class(self, features):
        probabilities = self.predict_proba(features)
        return max(probabilities, key=probabilities.get)

    def model_input(self, features):
        """Return the exact numeric matrix used by the loaded XGBoost model."""
        with self._lock:
            self._refresh_active_model()
            if self._model is None:
                raise RiskModelError('Risk model has not been loaded')
            return self._ensure_vector(features)

    def estimator(self):
        """Expose the loaded estimator to isolated model explanation layers."""
        with self._lock:
            self._refresh_active_model()
            if self._model is None:
                raise RiskModelError('Risk model has not been loaded')
            return self._model

    def predict_proba(self, features):
        with self._lock:
            self._refresh_active_model()
            if self._model is None:
                raise RiskModelError('Risk model has not been loaded')
            vector = self._ensure_vector(features)
            raw_result = self._model.predict_proba(vector)[0]
            class_ids = list(self._model.classes_)
            label_lookup = {int(str(key)): value for key, value in (self._metadata.get('classes') or {}).items()}
            probability_map = {}
            for class_id, value in zip(class_ids, raw_result):
                label = label_lookup.get(int(class_id), str(class_id))
                probability_map[label] = float(value)
            ordered = {label: probability_map.get(label, 0.0) for label in TARGET_CLASSES}
            return ordered

    def metadata(self):
        with self._lock:
            self._refresh_active_model()
            return dict(self._metadata)

    def feature_names(self):
        return list(MODEL_FEATURES)
