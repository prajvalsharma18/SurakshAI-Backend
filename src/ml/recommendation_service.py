"""Grounded welfare recommendations composed from Phase 4, 5, and local knowledge."""

from __future__ import annotations

from src.ml.welfare_provider import WelfareProviderError, get_welfare_provider
from src.ml.welfare_rag import WelfareRetrievalError, WelfareRagService
from src.security.audit import RESULT_FAILURE, RESULT_SUCCESS, get_audit_service


class WelfareRecommendationError(RuntimeError):
    """Raised when grounded welfare recommendations cannot be produced safely."""


class RecommendationService:
    ALLOWED_CATEGORIES = {'WORKLOAD', 'RECOVERY', 'SLEEP', 'DUTY_SCHEDULING', 'LEAVE', 'SOCIAL_SUPPORT', 'REFERRAL', 'GENERAL'}
    ALLOWED_PRIORITIES = {'LOW', 'MEDIUM', 'HIGH'}

    def __init__(self, risk_service, rag_service=None, provider=None, audit_service=None):
        self.risk_service = risk_service
        self.rag_service = rag_service or WelfareRagService()
        self.provider = provider or get_welfare_provider()
        self.audit_service = audit_service or get_audit_service()

    def _validate_authorization(self, personnel_id, identity):
        identity = identity or {}
        role = identity.get('role')
        if role == 'PERSONNEL' and identity.get('personnel_id') != personnel_id:
            raise PermissionError('Forbidden')
        if role == 'COMMANDER' or role not in {'PERSONNEL', 'WELFARE_OFFICER', 'ADMIN'}:
            raise PermissionError('Forbidden')
        return identity

    def _validate_response(self, response, risk_category, retrieved):
        if not isinstance(response, dict) or response.get('risk_category', risk_category) != risk_category:
            raise WelfareRecommendationError('Recommendation response changed or omitted the Phase 4 risk category')
        recommendations = response.get('recommendations')
        if not isinstance(recommendations, list) or not recommendations:
            raise WelfareRecommendationError('Recommendation response contains no grounded recommendations')
        source_ids = {item['id'] for item in retrieved}
        validated = []
        for recommendation in recommendations:
            if not isinstance(recommendation, dict):
                raise WelfareRecommendationError('Recommendation item is not an object')
            required = {'category', 'action', 'priority', 'rationale', 'sources'}
            if not required.issubset(recommendation):
                raise WelfareRecommendationError('Recommendation item is missing required fields')
            if recommendation['category'] not in self.ALLOWED_CATEGORIES:
                raise WelfareRecommendationError('Recommendation category is not allowed')
            if recommendation['priority'] not in self.ALLOWED_PRIORITIES:
                raise WelfareRecommendationError('Recommendation priority is not allowed')
            sources = recommendation['sources']
            if not isinstance(sources, list) or not sources or not all(source in source_ids for source in sources):
                raise WelfareRecommendationError('Recommendation contains an unsupported source reference')
            validated.append({
                'category': recommendation['category'],
                'action': str(recommendation['action']),
                'priority': recommendation['priority'],
                'rationale': str(recommendation['rationale']),
                'sources': list(dict.fromkeys(sources)),
            })
        return validated

    def recommend_for_personnel(self, personnel_id, reference_date=None, identity=None):
        identity = self._validate_authorization(personnel_id, identity)
        try:
            result = self.risk_service.explain_for_personnel(
                personnel_id,
                reference_date=reference_date,
                identity=identity,
            )
            explanation = result.get('explanation') or {}
            contributors = explanation.get('top_contributors', [])
            query = self.rag_service.build_query(
                result['risk_category'],
                contributors,
                result.get('data_mode', 'OPERATIONAL_ONLY'),
            )
            retrieved = self.rag_service.retrieve_welfare_context(query, top_k=3)
            if not retrieved:
                raise WelfareRecommendationError('No grounded welfare knowledge was retrieved')
            provider_response = self.provider.generate(
                risk_category=result['risk_category'],
                explanation=contributors,
                retrieved_context=retrieved,
                query=query,
            )
            recommendations = self._validate_response(provider_response, result['risk_category'], retrieved)
            response = {
                'personnel_id': result['personnel_id'],
                'reference_date': result['reference_date'],
                'risk_category': result['risk_category'],
                'model_version': result['model_version'],
                'data_mode': result['data_mode'],
                'recommendations': recommendations,
            }
            self.audit_service.record_event(
                actor_user_id=identity.get('user_id'),
                actor_role=identity.get('role'),
                action='WELFARE_RECOMMENDATION',
                resource_type='personnel_welfare_recommendation',
                resource_id=personnel_id,
                result=RESULT_SUCCESS,
                source='recommendation_service.recommend_for_personnel',
                purpose='Grounded welfare support decision aid',
                reference_date=result['reference_date'],
                model_version=result['model_version'],
            )
            return response
        except (WelfareRetrievalError, WelfareProviderError, WelfareRecommendationError, KeyError) as error:
            self.audit_service.record_event(
                actor_user_id=identity.get('user_id'),
                actor_role=identity.get('role'),
                action='WELFARE_RECOMMENDATION',
                resource_type='personnel_welfare_recommendation',
                resource_id=personnel_id,
                result=RESULT_FAILURE,
                source='recommendation_service.recommend_for_personnel',
                purpose='Grounded welfare support decision aid',
                reference_date=reference_date,
            )
            if isinstance(error, WelfareRecommendationError):
                raise
            raise WelfareRecommendationError('Grounded welfare recommendation service unavailable') from error
