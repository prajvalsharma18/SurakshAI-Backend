"""Thin XGBoost model loader for the Phase 4 SURAKSHAI risk classifier."""

import json
from pathlib import Path

import numpy as np

from src.ml.feature_schema import MODEL_FEATURES, TARGET_CLASSES


class RiskModelError(RuntimeError):
    """Raised when the persisted model or metadata is invalid."""


class RiskModel:
    def __init__(self, model_path=None, metadata_path=None):
        base_dir = Path(__file__).resolve().parents[2]
        self.model_path = Path(model_path or base_dir / 'models' / 'risk' / 'surakshai_risk_model.json')
        self.metadata_path = Path(metadata_path or base_dir / 'models' / 'risk' / 'metadata.json')
        self._model = None
        self._metadata = {}
        self._load_metadata()
        self._load_model()

    @property
    def model_version(self):
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
        if sorted(metadata_classes.values()) != sorted(TARGET_CLASSES):
            raise RiskModelError('Metadata class mapping does not match the canonical target classes')

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
        if self._model is None:
            raise RiskModelError('Risk model has not been loaded')
        return self._ensure_vector(features)

    def estimator(self):
        """Expose the loaded estimator to isolated model explanation layers."""
        if self._model is None:
            raise RiskModelError('Risk model has not been loaded')
        return self._model

    def predict_proba(self, features):
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
        return dict(self._metadata)

    def feature_names(self):
        return list(MODEL_FEATURES)
