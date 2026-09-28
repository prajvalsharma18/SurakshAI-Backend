"""Compute operational feature vectors through existing repositories."""

from datetime import date

from src.features.feature_engineering import build_longitudinal_features
from src.schemas.operational import OPERATIONAL_SCHEMAS
from src.schemas.personnel import validate_personnel_identifier
from src.security.audit import RESULT_SUCCESS, get_audit_service


class FeatureService:
    def __init__(self, operational_repository, audit_service=None):
        self.operational_repository = operational_repository
        self.audit_service = audit_service or get_audit_service()

    def compute(self, personnel_id, reference_date=None, identity=None):
        personnel_id = validate_personnel_identifier(personnel_id)
        if reference_date is None:
            reference_date = date.today().isoformat()
        try:
            parsed_reference_date = date.fromisoformat(reference_date)
        except (TypeError, ValueError) as error:
            raise ValueError('reference_date must use YYYY-MM-DD format') from error
        if parsed_reference_date.isoformat() != reference_date:
            raise ValueError('reference_date must use YYYY-MM-DD format')
        reference_date = parsed_reference_date.isoformat()
        records_by_domain = {
            domain: self.operational_repository.list(
                domain,
                personnel_id=personnel_id,
                end_date=reference_date,
            )
            for domain in OPERATIONAL_SCHEMAS
        }
        features = build_longitudinal_features(personnel_id, reference_date, records_by_domain)
        identity = identity or {}
        self.audit_service.record_event(
            actor_user_id=identity.get('user_id'),
            actor_role=identity.get('role'),
            action='LONGITUDINAL_FEATURE_VIEW',
            resource_type='personnel_features',
            resource_id=personnel_id,
            result=RESULT_SUCCESS,
            source='feature_service.compute',
            purpose='View operational longitudinal features',
        )
        return features