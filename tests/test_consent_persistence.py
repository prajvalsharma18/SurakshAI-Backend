import unittest

from src.db.repositories.consent_repository import ConsentRepository
from src.security.audit import AuditService
from src.security.consent import CONSENT_WELLNESS_DATA_PROCESSING, ConsentService


class ConsentCollection:
    def __init__(self):
        self.records = []

    def create_index(self, *args, **kwargs):
        return None

    def insert_one(self, record):
        self.records.append(dict(record))

    def find(self, query=None):
        query = query or {}
        return [
            dict(record) for record in self.records
            if all(record.get(key) == value for key, value in query.items())
        ]


class ConsentDatabase:
    def __init__(self):
        self.collection = ConsentCollection()

    def get_collection(self, name):
        return self.collection


class ConsentPersistenceTests(unittest.TestCase):
    def test_default_deny_and_restart_persistence(self):
        database = ConsentDatabase()
        audit = AuditService()
        first = ConsentService(ConsentRepository(database), audit)
        self.assertFalse(first.has_personnel_consent('P001', CONSENT_WELLNESS_DATA_PROCESSING))
        first.grant_consent('user-1', CONSENT_WELLNESS_DATA_PROCESSING, personnel_id='P001')

        restarted = ConsentService(ConsentRepository(database), audit)
        self.assertTrue(restarted.has_personnel_consent('P001', CONSENT_WELLNESS_DATA_PROCESSING))
        self.assertTrue(restarted.get_user_consents('user-1', personnel_id='P001')[0]['granted'])

    def test_revoke_and_history_survive_service_restart(self):
        database = ConsentDatabase()
        audit = AuditService()
        service = ConsentService(ConsentRepository(database), audit)
        service.grant_consent('user-1', CONSENT_WELLNESS_DATA_PROCESSING, personnel_id='P001')
        service.revoke_consent('user-1', CONSENT_WELLNESS_DATA_PROCESSING, personnel_id='P001')
        service.grant_consent('user-1', CONSENT_WELLNESS_DATA_PROCESSING, personnel_id='P001')

        restarted = ConsentService(ConsentRepository(database), audit)
        self.assertTrue(restarted.has_personnel_consent('P001', CONSENT_WELLNESS_DATA_PROCESSING))
        history = restarted.get_history('P001', CONSENT_WELLNESS_DATA_PROCESSING)
        self.assertEqual([record['status'] for record in reversed(history)], ['GRANTED', 'REVOKED', 'GRANTED'])
        actions = [event['action'] for event in audit.list_events()]
        self.assertEqual(actions.count('CONSENT_GRANTED'), 2)
        self.assertEqual(actions.count('CONSENT_REVOKED'), 1)

    def test_personnel_scoping_is_not_cross_readable(self):
        database = ConsentDatabase()
        service = ConsentService(ConsentRepository(database), AuditService())
        service.grant_consent('user-1', CONSENT_WELLNESS_DATA_PROCESSING, personnel_id='P001')
        self.assertFalse(service.has_personnel_consent('P002', CONSENT_WELLNESS_DATA_PROCESSING))
        self.assertFalse(service.get_user_consents('user-2', personnel_id='P002')[0]['granted'])


if __name__ == '__main__':
    unittest.main()
