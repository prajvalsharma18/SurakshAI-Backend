import unittest
from unittest.mock import patch

from src.db.repositories.operational_repository import OperationalRepository
from src.db.repositories.wellness_repository import WellnessRepository
from src.db.repositories.consent_repository import ConsentRepository
from src.ml.welfare_rag import WelfareRagService
from src.services.operational_service import OperationalService
from src.services.wellness_service import WellnessService
from src.security.audit import get_audit_service
from src.security.consent import CONSENT_WELLNESS_DATA_PROCESSING
from src.security.consent import ConsentService
from tests.user_test_support import install_test_users


class FakeCursor(list):
    def sort(self, field, direction):
        return FakeCursor(sorted(self, key=lambda item: item.get(field), reverse=direction < 0))


class FakeCollection:
    def __init__(self):
        self.records = []

    def create_index(self, *args, **kwargs):
        return None

    @staticmethod
    def _matches(record, query):
        for field, expected in query.items():
            actual = record.get(field)
            if isinstance(expected, dict):
                if '$in' in expected and actual not in expected['$in']:
                    return False
                if '$gte' in expected and (actual is None or actual < expected['$gte']):
                    return False
                if '$lte' in expected and (actual is None or actual > expected['$lte']):
                    return False
            elif actual != expected:
                return False
        return True

    def find(self, query=None):
        return FakeCursor([dict(item) for item in self.records if self._matches(item, query or {})])

    def find_one(self, query):
        for item in self.records:
            if self._matches(item, query):
                return dict(item)
        return None

    def insert_one(self, record):
        self.records.append(dict(record))

    def update_one(self, query, update, upsert=False):
        record = self.find_one(query)
        if record is None:
            if upsert:
                record = {**query, **update.get('$set', {})}
                self.records.append(record)
            return
        record.update(update.get('$set', {}))
        for index, item in enumerate(self.records):
            if self._matches(item, query):
                self.records[index] = record
                return

    def delete_one(self, query):
        for index, item in enumerate(self.records):
            if self._matches(item, query):
                self.records.pop(index)
                return type('DeleteResult', (), {'deleted_count': 1})()
        return type('DeleteResult', (), {'deleted_count': 0})()


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def get_collection(self, name):
        return self.collections.setdefault(name, FakeCollection())


class FakeRiskService:
    def predict_for_personnel(self, personnel_id, reference_date=None, identity=None):
        return {
            'personnel_id': personnel_id,
            'reference_date': reference_date or '2026-09-26',
            'risk_category': 'ELEVATED',
            'probabilities': {'LOW': 0.1, 'ELEVATED': 0.8, 'HIGH': 0.1},
            'model_version': 'surakshai-risk-v0.1',
            'data_mode': 'OPERATIONAL_ONLY',
        }

    def explain_for_personnel(self, personnel_id, reference_date=None, identity=None):
        result = self.predict_for_personnel(personnel_id, reference_date, identity)
        result['explanation'] = {
            'method': 'SHAP',
            'predicted_class': 'ELEVATED',
            'top_contributors': [
                {'feature': 'workload_mean_7d', 'label': 'Mean workload', 'shap_value': 0.5, 'direction': 'increases_predicted_risk'}
            ],
        }
        return result


class FakeRecommendationService:
    def recommend_for_personnel(self, personnel_id, reference_date=None, identity=None):
        return {
            'personnel_id': personnel_id,
            'reference_date': reference_date or '2026-09-26',
            'risk_category': 'ELEVATED',
            'model_version': 'surakshai-risk-v0.1',
            'data_mode': 'OPERATIONAL_ONLY',
            'recommendations': [{'category': 'RECOVERY', 'action': 'Review recovery time', 'priority': 'MEDIUM', 'rationale': 'Grounded support', 'sources': ['source-1']}],
        }


class FakeWorkflowService:
    def __init__(self):
        self.alert = {'alert_id': 'alert-1', 'personnel_id': 'P001', 'status': 'OPEN', 'severity': 'ATTENTION'}
        self.intervention = None

    def list_alerts(self, personnel_id=None):
        return [self.alert] if personnel_id in (None, 'P001') else []

    def evaluate(self, personnel_id, reference_date=None, identity=None):
        return self.alert

    def get_alert(self, alert_id):
        if alert_id != self.alert['alert_id']:
            raise RuntimeError('Alert not found')
        return self.alert

    def transition(self, alert_id, status, actor, **fields):
        self.alert.update(status=status, **{key: value for key, value in fields.items() if value is not None})
        return self.alert

    def create_intervention(self, alert_id, personnel_id, action_type, actor, scheduled_follow_up=None):
        self.intervention = {'intervention_id': 'intervention-1', 'alert_id': alert_id, 'personnel_id': personnel_id, 'action_type': action_type, 'status': 'PLANNED'}
        return self.intervention

    def update_intervention(self, intervention_id, status, actor, outcome_category=None):
        self.intervention['status'] = status
        if outcome_category:
            self.intervention['outcome_category'] = outcome_category
        return self.intervention


class CurrentSurakshaiApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import api_server
        cls.api_server = api_server
        cls.client = api_server.app.test_client()
        cls.database = FakeDatabase()
        cls.audit = get_audit_service()
        cls.audit._events.clear()
        install_test_users(api_server)
        api_server.consent_service = ConsentService(
            ConsentRepository(cls.database),
            cls.audit,
        )
        operational = OperationalService(OperationalRepository(cls.database), cls.audit)
        api_server.operational_service = operational
        api_server.feature_service = None
        api_server.wellness_service = WellnessService(WellnessRepository(cls.database), api_server.consent_service, cls.audit)
        api_server.risk_service = FakeRiskService()
        api_server.welfare_recommendation_service = FakeRecommendationService()
        api_server.welfare_workflow_service = FakeWorkflowService()

    def setUp(self):
        self.api_server.consent_service._records.clear()
        self.api_server.consent_service._personnel_user_ids.clear()
        self.api_server.consent_service._user_personnel_ids.clear()
        self.api_server.consent_service.repository._ensure_collection()
        self.api_server.consent_service.repository.collection.records.clear()
        self.api_server.audit_service._events.clear()

    def token(self, username, password):
        response = self.client.post('/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200)
        return response.get_json()['access_token']

    def headers(self, username, password):
        return {'Authorization': f'Bearer {self.token(username, password)}'}

    def test_root_health_and_current_metadata(self):
        root = self.client.get('/')
        self.assertEqual(root.status_code, 200)
        payload = root.get_json()
        self.assertEqual(payload['api'], 'SURAKSHAI Personnel Welfare Intelligence API')
        self.assertEqual(payload['model_info']['model_version'], 'surakshai-risk-v0.1')
        self.assertEqual(payload['model_info']['feature_count'], 31)
        self.assertNotIn('Random Forest Classifier', str(payload))
        self.assertNotIn('/predict', str(payload))
        self.assertNotIn('/employees', str(payload))
        self.assertIn('GET /personnel/<personnel_id>/risk-explanation', payload['endpoints'])
        health = self.client.get('/health')
        self.assertEqual(health.status_code, 200)
        self.assertNotIn('legacy_model_available', str(health.get_json()))

    def test_all_development_roles_can_login(self):
        credentials = [('demo_personnel', 'demo-personnel-password', 'PERSONNEL'), ('demo_welfare', 'demo-welfare-password', 'WELFARE_OFFICER'), ('demo_commander', 'demo-commander-password', 'COMMANDER'), ('demo_admin', 'demo-admin-password', 'ADMIN')]
        for username, password, role in credentials:
            response = self.client.post('/auth/login', json={'username': username, 'password': password})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['user']['role'], role)

    def test_operational_crud_and_role_boundaries(self):
        admin = self.headers('demo_admin', 'demo-admin-password')
        payload = {'personnel_id': 'P001', 'date': '2026-09-26', 'duty_hours': 8, 'night_duty': False, 'shift_type': 'DAY', 'operational_intensity': 'MODERATE'}
        created = self.client.post('/operational/duty_records', json=payload, headers=admin)
        self.assertEqual(created.status_code, 201)
        record_id = created.get_json()['record_id']
        self.assertEqual(self.client.get(f'/operational/duty_records/{record_id}', headers=admin).status_code, 200)
        self.assertEqual(self.client.patch(f'/operational/duty_records/{record_id}', json={'duty_hours': 9}, headers=admin).status_code, 200)
        self.assertEqual(self.client.delete(f'/operational/duty_records/{record_id}', headers=admin).status_code, 200)
        personnel = self.headers('demo_personnel', 'demo-personnel-password')
        self.assertEqual(self.client.get('/personnel/P001/operational/duty_records', headers=personnel).status_code, 200)
        self.assertEqual(self.client.get('/personnel/P002/features', headers=personnel).status_code, 403)
        commander = self.headers('demo_commander', 'demo-commander-password')
        self.assertEqual(self.client.get('/operational/summary', headers=commander).status_code, 200)

    def test_wellness_requires_consent_and_supports_lifecycle(self):
        headers = self.headers('demo_personnel', 'demo-personnel-password')
        wellness = {'assessment_date': '2026-09-26', 'sleep_quality': 3, 'fatigue_level': 3, 'perceived_stress': 3, 'mood_wellbeing': 4}
        self.assertEqual(self.client.post('/personnel/me/wellness', json=wellness, headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/consent', json={'consent_type': CONSENT_WELLNESS_DATA_PROCESSING, 'granted': True}, headers=headers).status_code, 200)
        created = self.client.post('/personnel/me/wellness', json=wellness, headers=headers)
        self.assertEqual(created.status_code, 201)
        assessment_id = created.get_json()['assessment_id']
        self.assertEqual(self.client.get('/personnel/me/wellness', headers=headers).status_code, 200)
        self.assertEqual(self.client.get(f'/personnel/me/wellness/{assessment_id}', headers=headers).status_code, 200)
        self.assertEqual(self.client.delete(f'/personnel/me/wellness/{assessment_id}', headers=headers).status_code, 200)
        self.assertEqual(self.client.post('/consent', json={'consent_type': CONSENT_WELLNESS_DATA_PROCESSING, 'granted': False}, headers=headers).status_code, 200)
        self.assertEqual(self.client.get('/personnel/me/wellness', headers=headers).status_code, 403)

    def test_risk_shap_recommendations_and_rbac(self):
        personnel = self.headers('demo_personnel', 'demo-personnel-password')
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        admin = self.headers('demo_admin', 'demo-admin-password')
        commander = self.headers('demo_commander', 'demo-commander-password')
        for route in ('risk-prediction', 'risk-explanation', 'welfare-recommendations'):
            self.assertEqual(self.client.get(f'/personnel/P001/{route}', headers=personnel).status_code, 200)
            self.assertEqual(self.client.get(f'/personnel/P001/{route}', headers=welfare).status_code, 200)
            self.assertEqual(self.client.get(f'/personnel/P001/{route}', headers=admin).status_code, 200)
            self.assertEqual(self.client.get(f'/personnel/P001/{route}', headers=commander).status_code, 403)
        explanation = self.client.get('/personnel/P001/risk-explanation', headers=personnel).get_json()
        self.assertEqual(explanation['explanation']['method'], 'SHAP')
        self.assertTrue(explanation['explanation']['top_contributors'])
        self.assertNotIn('raw_values', str(explanation))

    def test_alert_and_intervention_workflow_actions_are_authorized(self):
        welfare = self.headers('demo_welfare', 'demo-welfare-password')
        personnel = self.headers('demo_personnel', 'demo-personnel-password')
        self.assertEqual(self.client.post('/welfare/alerts/evaluate/P001', json={}, headers=welfare).status_code, 200)
        self.assertEqual(self.client.get('/welfare/alerts', headers=welfare).status_code, 200)
        self.assertEqual(self.client.get('/personnel/P001/alerts', headers=personnel).status_code, 200)
        self.assertEqual(self.client.post('/welfare/alerts/alert-1/acknowledge', headers=welfare).status_code, 200)
        self.assertEqual(self.client.post('/welfare/alerts/alert-1/review', headers=welfare).status_code, 200)
        self.assertEqual(self.client.post('/welfare/alerts/alert-1/intervention', json={'action_type': 'WELFARE_CHECK_IN'}, headers=welfare).status_code, 201)
        self.assertEqual(self.client.patch('/welfare/interventions/intervention-1', json={'status': 'COMPLETED'}, headers=welfare).status_code, 200)
        self.assertEqual(self.client.post('/welfare/alerts/alert-1/follow-up', json={'scheduled_follow_up': '2026-10-01'}, headers=welfare).status_code, 200)
        self.assertEqual(self.client.post('/welfare/alerts/alert-1/resolve', json={'resolution_type': 'SUPPORTED'}, headers=welfare).status_code, 200)
        self.assertEqual(self.client.post('/welfare/alerts/alert-1/dismiss', headers=personnel).status_code, 403)

    def test_audit_events_are_minimized(self):
        self.client.post('/auth/login', json={'username': 'demo_personnel', 'password': 'wrong-password'})
        events = self.api_server.audit_service.list_events()
        self.assertEqual(events[-1]['action'], 'LOGIN_FAILURE')
        self.assertNotIn('wrong-password', str(events))
        self.assertNotIn('access_token', str(events))
        self.assertNotIn('password_hash', str(events))


class CurrentComponentContractTests(unittest.TestCase):
    def test_rag_uses_dedicated_surakshai_collection(self):
        self.assertEqual(WelfareRagService.COLLECTION_NAME, 'surakshai_welfare_knowledge')
        self.assertNotIn('stress_management', WelfareRagService.COLLECTION_NAME)

    def test_shared_mongodb_client_is_reused_and_closeable(self):
        import src.db.mongodb as mongodb
        fake_client = object()
        with patch.object(mongodb, 'MongoClient', return_value=fake_client):
            mongodb._clients.clear()
            first = mongodb.create_mongo_client('mongodb://unit-test', 100)
            second = mongodb.create_mongo_client('mongodb://unit-test', 100)
            self.assertIs(first, second)
        mongodb._clients.clear()


if __name__ == '__main__':
    unittest.main()
