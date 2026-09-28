import io
import os
import unittest
from unittest.mock import patch

from pypdf import PdfReader

from src.reports.welfare_report_builder import WelfareReportBuilder
from src.reports.welfare_report_service import WelfareReportService
from src.security.audit import AuditService
from src.security.pseudonymization import Pseudonymizer


class FakeFeatureService:
    def compute(self, personnel_id, reference_date=None, identity=None):
        return {
            'workload_7d_avg': 72.0,
            'workload_30d_avg': 60.0,
            'workload_trend': 12.0,
            'current_consecutive_duty_days': 3,
            'sleep_quality': None,
        }


class FakeRiskService:
    def explain_for_personnel(self, personnel_id, reference_date=None, identity=None):
        return {
            'personnel_id': personnel_id,
            'reference_date': reference_date,
            'risk_category': 'ELEVATED',
            'model_version': 'surakshai-risk-v0.1',
            'data_mode': 'OPERATIONAL_ONLY',
            'explanation': {
                'method': 'SHAP',
                'top_contributors': [{
                    'feature': 'workload_7d_avg',
                    'label': 'Average workload over the last 7 days',
                    'shap_value': 0.42,
                    'direction': 'increases_predicted_risk',
                }],
            },
        }


class FakeWellnessService:
    def list_for_self(self, identity, *, end_date=None):
        return [{'assessment_date': '2026-09-25', 'sleep_quality': 4, 'fatigue_level': 2, 'perceived_stress': 2, 'mood_wellbeing': 4}]

    def list_for_authorized_personnel(self, personnel_id, identity, *, end_date=None):
        return self.list_for_self(identity, end_date=end_date)


class FakeConsentService:
    def __init__(self, granted=True):
        self.granted = granted

    def link_personnel_identity(self, user_id, personnel_id):
        return None

    def has_personnel_consent(self, personnel_id, consent_type):
        return self.granted


class FakeRecommendationService:
    def recommend_for_personnel(self, personnel_id, reference_date=None, identity=None):
        return {'recommendations': [{
            'category': 'RECOVERY',
            'action': 'Discuss recovery opportunities with a welfare officer.',
            'priority': 'MEDIUM',
            'rationale': 'Grounded operational support.',
            'sources': ['surakshai_welfare_knowledge::1'],
        }]}


class FakePredictionRepository:
    def list_recent(self, personnel_id, start_date=None, end_date=None):
        return [{'reference_date': '2026-09-25', 'risk_category': 'ELEVATED'}]


class FakeInterventionRepository:
    def list_for_alert(self, alert_id):
        return [{'action_type': 'WELFARE_CHECK_IN', 'created_at': '2026-09-25T00:00:00+00:00', 'status': 'PLANNED', 'scheduled_follow_up': None, 'outcome_category': None}]


class FakeWorkflowService:
    def __init__(self):
        self.predictions = FakePredictionRepository()
        self.interventions = FakeInterventionRepository()

    def list_alerts(self, personnel_id=None):
        return [{'alert_id': 'alert-1', 'severity': 'ATTENTION', 'created_at': '2026-09-25T00:00:00+00:00', 'status': 'OPEN', 'acknowledged_at': None, 'reviewed_at': None, 'scheduled_follow_up': '2026-10-01', 'resolution_type': None}]


class WelfareReportServiceTests(unittest.TestCase):
    def make_service(self, consent=True):
        return WelfareReportService(
            feature_service=FakeFeatureService(),
            risk_service=FakeRiskService(),
            wellness_service=FakeWellnessService(),
            recommendation_service=FakeRecommendationService(),
            workflow_service=FakeWorkflowService(),
            consent_service=FakeConsentService(consent),
            pseudonymizer=Pseudonymizer('report-test-secret'),
            audit_service=AuditService(),
        )

    def test_report_dto_is_minimized_and_uses_pseudonym(self):
        service = self.make_service(consent=True)
        report = service.generate('P001', reference_date='2026-09-26', identity={'user_id': 'w1', 'role': 'WELFARE_OFFICER'})
        self.assertTrue(report.personnel.pseudonymous_reference.startswith('psn_'))
        self.assertTrue(report.wellness.available)
        self.assertEqual(report.metadata.model_version, 'surakshai-risk-v0.1')
        self.assertEqual(report.recommendations[0].sources, ('surakshai_welfare_knowledge::1',))
        serialized = str(report)
        for prohibited in ('Prajval', 'password', 'access_token', 'mongodb://', 'api_key'):
            self.assertNotIn(prohibited, serialized)

    def test_wellness_is_omitted_without_current_consent(self):
        report = self.make_service(consent=False).generate('P001', reference_date='2026-09-26', identity={'user_id': 'w1', 'role': 'WELFARE_OFFICER'})
        self.assertFalse(report.wellness.available)
        self.assertEqual(report.wellness.message, 'Voluntary wellness data unavailable under current consent.')

    def test_builder_contains_required_sections_and_is_parseable(self):
        report = self.make_service().generate('P001', reference_date='2026-09-26', identity={'user_id': 'w1', 'role': 'WELFARE_OFFICER'})
        pdf = WelfareReportBuilder().build(report)
        self.assertTrue(pdf.startswith(b'%PDF'))
        text = '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(pdf)).pages)
        for section in ('Report Information', 'Risk Summary', 'Risk Trend', 'Key Contributing Factors', 'Operational Summary', 'Voluntary Wellness Summary', 'Grounded Welfare Recommendations', 'Welfare Alerts', 'Welfare Interventions', 'Privacy and Safety Notice'):
            self.assertIn(section, text)
        self.assertIn(report.personnel.pseudonymous_reference, text)
        self.assertIn('CONFIDENTIAL', text)
        self.assertNotIn('P001', text)


class WelfareReportApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import api_server
        cls.previous_secret = os.environ.get('JWT_SECRET_KEY')
        os.environ['JWT_SECRET_KEY'] = 'welfare-report-api-test-secret-key'
        cls.api_server = api_server
        cls.client = api_server.app.test_client()
        from tests.user_test_support import install_test_users
        install_test_users(api_server)
        cls.service = WelfareReportService(
            feature_service=FakeFeatureService(),
            risk_service=FakeRiskService(),
            wellness_service=FakeWellnessService(),
            recommendation_service=FakeRecommendationService(),
            workflow_service=FakeWorkflowService(),
            consent_service=FakeConsentService(True),
            pseudonymizer=Pseudonymizer('report-api-test-secret'),
            audit_service=api_server.audit_service,
        )

    @classmethod
    def tearDownClass(cls):
        if cls.previous_secret is None:
            os.environ.pop('JWT_SECRET_KEY', None)
        else:
            os.environ['JWT_SECRET_KEY'] = cls.previous_secret

    def token(self, username, password):
        response = self.client.post('/auth/login', json={'username': username, 'password': password})
        self.assertEqual(response.status_code, 200)
        return response.get_json()['access_token']

    def headers(self, username, password):
        return {'Authorization': f'Bearer {self.token(username, password)}'}

    def test_authorized_roles_and_forbidden_commander(self):
        original = self.api_server.welfare_report_service
        self.api_server.welfare_report_service = self.service
        try:
            welfare = self.client.get('/personnel/P001/welfare-report', headers=self.headers('demo_welfare', 'demo-welfare-password'))
            personnel = self.client.get('/personnel/P001/welfare-report', headers=self.headers('demo_personnel', 'demo-personnel-password'))
            other = self.client.get('/personnel/P002/welfare-report', headers=self.headers('demo_personnel', 'demo-personnel-password'))
            commander = self.client.get('/personnel/P001/welfare-report', headers=self.headers('demo_commander', 'demo-commander-password'))
            admin = self.client.get('/personnel/P001/welfare-report', headers=self.headers('demo_admin', 'demo-admin-password'))
            self.assertEqual(welfare.status_code, 200)
            self.assertEqual(personnel.status_code, 200)
            self.assertEqual(other.status_code, 403)
            self.assertEqual(commander.status_code, 403)
            self.assertEqual(admin.status_code, 200)
            self.assertEqual(welfare.mimetype, 'application/pdf')
            self.assertIn('psn_', welfare.headers['Content-Disposition'])
        finally:
            self.api_server.welfare_report_service = original

    def test_invalid_or_missing_authentication_does_not_generate_pdf(self):
        original = self.api_server.welfare_report_service
        self.api_server.welfare_report_service = self.service
        try:
            self.assertEqual(self.client.get('/personnel/P001/welfare-report').status_code, 401)
            self.assertEqual(self.client.get('/personnel/P001/welfare-report?reference_date=tomorrow', headers=self.headers('demo_welfare', 'demo-welfare-password')).status_code, 400)
        finally:
            self.api_server.welfare_report_service = original


if __name__ == '__main__':
    unittest.main()
