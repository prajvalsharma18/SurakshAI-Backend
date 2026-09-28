"""Consent-aware Phase 4 risk prediction orchestration for authorized personnel."""

from __future__ import annotations

from datetime import date

from src.features.feature_service import FeatureService
from src.ml.feature_schema import MODEL_FEATURES, TARGET_CLASSES
from src.ml.risk_features import build_canonical_feature_vector
from src.ml.risk_model import RiskModel, RiskModelError
from src.ml.shap_explainer import ShapExplanationError, SurakshaiShapExplainer
from src.security.audit import RESULT_FAILURE, RESULT_SUCCESS, get_audit_service
from src.security.consent import CONSENT_WELLNESS_DATA_PROCESSING


class RiskService:
    def __init__(self, feature_service, wellness_service=None, consent_service=None, audit_service=None, model=None, shap_explainer=None):
        self.feature_service = feature_service
        self.wellness_service = wellness_service
        self.consent_service = consent_service
        self.audit_service = audit_service or get_audit_service()
        self.model = model or RiskModel()
        self.shap_explainer = shap_explainer

    def _validate_reference_date(self, reference_date):
        if reference_date is None:
            reference_date = date.today().isoformat()
        try:
            parsed = date.fromisoformat(reference_date)
        except (TypeError, ValueError) as exc:
            raise ValueError('reference_date must use YYYY-MM-DD format') from exc
        if parsed.isoformat() != reference_date:
            raise ValueError('reference_date must use YYYY-MM-DD format')
        if parsed > date.today():
            raise ValueError('reference_date cannot be in the future')
        return parsed.isoformat()

    def _authorized_wellness(self, personnel_id, identity, reference_date):
        if self.consent_service is None or self.wellness_service is None:
            return None
        user_id = (identity or {}).get('user_id')
        if not user_id:
            return None
        if not self.consent_service.has_consent(user_id, CONSENT_WELLNESS_DATA_PROCESSING):
            return None
        assessments = self.wellness_service.repository.list_for_personnel(
            personnel_id,
            end_date=reference_date,
        )
        if not assessments:
            return None
        return assessments[0]

    def predict_for_personnel(self, personnel_id, reference_date=None, identity=None):
        prediction, _ = self._build_prediction(personnel_id, reference_date, identity)
        self._audit_prediction(identity, personnel_id, prediction)
        return prediction

    def _authorize(self, personnel_id, identity):
        identity = identity or {}
        role = identity.get('role')
        if role == 'PERSONNEL' and identity.get('personnel_id') != personnel_id:
            raise PermissionError('Forbidden')
        if role == 'COMMANDER':
            raise PermissionError('Forbidden')
        if role not in {'PERSONNEL', 'WELFARE_OFFICER', 'ADMIN'}:
            raise PermissionError('Forbidden')
        return identity

    def _build_prediction(self, personnel_id, reference_date=None, identity=None):
        identity = self._authorize(personnel_id, identity)
        reference_date = self._validate_reference_date(reference_date)
        feature_vector = self.feature_service.compute(personnel_id, reference_date=reference_date, identity=identity)
        if not isinstance(feature_vector, dict):
            raise ValueError('Feature vector unavailable')
        wellness_record = self._authorized_wellness(personnel_id, identity, reference_date)
        if wellness_record is not None:
            data_mode = 'OPERATIONAL_AND_WELLNESS'
        else:
            data_mode = 'OPERATIONAL_ONLY'
        canonical = build_canonical_feature_vector(feature_vector, wellness_record)
        probabilities = self.model.predict_proba(canonical)
        risk_category = self.model.predict_class(canonical)

        payload = {
            'personnel_id': personnel_id,
            'reference_date': reference_date,
            'risk_category': risk_category,
            'probabilities': probabilities,
            'model_version': self.model.model_version,
            'data_mode': data_mode,
        }
        return payload, canonical

    def _audit_prediction(self, identity, personnel_id, payload):
        self.audit_service.record_event(
            actor_user_id=identity.get('user_id'),
            actor_role=identity.get('role'),
            action='RISK_PREDICTION',
            resource_type='personnel_risk',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='risk_service.predict_for_personnel',
            purpose='Personnel risk decision support',
            reference_date=payload['reference_date'],
            model_version=self.model.model_version,
            risk_category=payload['risk_category'],
            data_mode=payload['data_mode'],
        )
        return payload

    def explain_for_personnel(self, personnel_id, reference_date=None, identity=None, top_n=5):
        identity = self._authorize(personnel_id, identity)
        prediction, canonical = self._build_prediction(personnel_id, reference_date, identity)
        if self.shap_explainer is None:
            self.shap_explainer = SurakshaiShapExplainer(self.model)
        try:
            explanation = self.shap_explainer.explain(
                canonical,
                prediction['risk_category'],
                top_n=top_n,
            )
        except (ShapExplanationError, ValueError) as error:
            self.audit_service.record_event(
                actor_user_id=identity.get('user_id'),
                actor_role=identity.get('role'),
                action='RISK_EXPLANATION',
                resource_type='personnel_risk',
                resource_id=personnel_id,
                result=RESULT_FAILURE,
                source='risk_service.explain_for_personnel',
                purpose='Explain personnel risk decision support',
                reference_date=prediction['reference_date'],
                model_version=self.model.model_version,
            )
            raise RuntimeError('Risk explanation service unavailable') from error
        self.audit_service.record_event(
            actor_user_id=identity.get('user_id'),
            actor_role=identity.get('role'),
            action='RISK_EXPLANATION',
            resource_type='personnel_risk',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='risk_service.explain_for_personnel',
            purpose='Explain personnel risk decision support',
            reference_date=prediction['reference_date'],
            model_version=self.model.model_version,
        )
        prediction['explanation'] = explanation
        return prediction
