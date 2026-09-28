"""SHAP explanations for the canonical SURAKSHAI Phase 4 XGBoost model."""

from __future__ import annotations

import numpy as np

from src.ml.feature_schema import FEATURE_DESCRIPTIONS, MODEL_FEATURES, TARGET_CLASSES
from src.ml.risk_model import RiskModel


class ShapExplanationError(RuntimeError):
    """Raised when a SURAKSHAI SHAP explanation cannot be computed safely."""


class SurakshaiShapExplainer:
    """Create one cached TreeExplainer for the already-loaded Phase 4 estimator."""

    def __init__(self, risk_model: RiskModel, explainer=None):
        try:
            import shap
        except ImportError as exc:
            raise ShapExplanationError('SHAP is required for SURAKSHAI explanations') from exc
        self.risk_model = risk_model
        self._shap = shap
        self._explainer = explainer or shap.TreeExplainer(risk_model.estimator())

    def _class_matrix(self, raw_values):
        """Normalize SHAP list, 2D, and current multiclass 3D outputs to class x feature."""
        values = raw_values.values if hasattr(raw_values, 'values') else raw_values
        if isinstance(values, list):
            arrays = [np.asarray(item) for item in values]
            if not arrays:
                raise ShapExplanationError('SHAP returned no class values')
            values = np.stack([item[0] if item.ndim > 1 else item for item in arrays])
        else:
            values = np.asarray(values)
            if values.ndim == 3:
                if values.shape[0] != 1:
                    raise ShapExplanationError('SHAP explanation must contain one input row')
                values = values[0]
                if values.shape[0] == len(MODEL_FEATURES):
                    values = values.T
            elif values.ndim == 2:
                if values.shape[0] == 1:
                    values = values
                elif values.shape[1] == len(MODEL_FEATURES):
                    values = values[:1]
            elif values.ndim == 1:
                values = values.reshape(1, -1)
            else:
                raise ShapExplanationError('Unsupported SHAP output shape')
        if values.shape != (len(TARGET_CLASSES), len(MODEL_FEATURES)):
            raise ShapExplanationError(f'Unexpected normalized SHAP shape: {values.shape}')
        return values

    def explain(self, features, predicted_class, top_n=5):
        if predicted_class not in TARGET_CLASSES:
            raise ShapExplanationError('Predicted class is not in the canonical class contract')
        if not isinstance(top_n, int) or top_n < 1:
            raise ValueError('top_n must be a positive integer')
        if list(features.keys()) != MODEL_FEATURES:
            raise ShapExplanationError('Explanation input is not the canonical feature vector')

        matrix = self.risk_model.model_input(features)
        class_index = TARGET_CLASSES.index(predicted_class)
        normalized = self._class_matrix(self._explainer.shap_values(matrix))
        class_values = normalized[class_index]
        indices = sorted(range(len(MODEL_FEATURES)), key=lambda index: (-abs(float(class_values[index])), index))
        contributors = []
        for index in indices[:min(top_n, len(MODEL_FEATURES))]:
            value = float(class_values[index])
            feature = MODEL_FEATURES[index]
            contributors.append({
                'feature': feature,
                'label': FEATURE_DESCRIPTIONS[feature],
                'shap_value': value,
                'direction': 'increases_predicted_risk' if value > 0 else 'decreases_predicted_risk',
            })
        return {
            'method': 'SHAP',
            'predicted_class': predicted_class,
            'top_contributors': contributors,
        }