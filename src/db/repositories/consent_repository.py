"""MongoDB persistence for immutable consent state transitions."""


def _clean(record):
    return None if record is None else {key: value for key, value in record.items() if key != '_id'}


class ConsentRepository:
    def __init__(self, database=None):
        self.database = database
        self.collection = None
        self._indexes_ready = False

    def _ensure_collection(self):
        if self.collection is None and self.database is None:
            from src.db.mongodb import get_database
            self.database = get_database()
        if self.collection is None and self.database is not None:
            self.collection = self.database.get_collection('consents')
        if self.collection is not None and not self._indexes_ready:
            self._indexes_ready = True
            self.collection.create_index([('personnel_id', 1), ('consent_type', 1), ('updated_at', -1)])
            self.collection.create_index([('actor_user_id', 1), ('consent_type', 1), ('updated_at', -1)])
            self.collection.create_index([('status', 1), ('updated_at', -1)])

    def append_transition(self, record):
        self._ensure_collection()
        if self.collection is None:
            raise RuntimeError('Consent database is unavailable')
        self.collection.insert_one(dict(record))
        return dict(record)

    def _subject_query(self, personnel_id=None, actor_user_id=None, consent_type=None):
        query = {}
        if personnel_id is not None:
            query['personnel_id'] = personnel_id
        elif actor_user_id is not None:
            query['actor_user_id'] = actor_user_id
        if consent_type is not None:
            query['consent_type'] = consent_type
        return query

    def get_current_consent(self, *, personnel_id=None, actor_user_id=None, consent_type):
        self._ensure_collection()
        if self.collection is None:
            return None
        query = self._subject_query(personnel_id, actor_user_id, consent_type)
        try:
            return _clean(next(iter(self.collection.find(query).sort('updated_at', -1)), None))
        except (AttributeError, TypeError):
            records = [_clean(record) for record in self.collection.find(query)]
            records.sort(key=lambda record: record.get('updated_at', ''), reverse=True)
            return records[0] if records else None

    def get_current_consents(self, *, personnel_id=None, actor_user_id=None):
        self._ensure_collection()
        if self.collection is None:
            return {}
        query = self._subject_query(personnel_id, actor_user_id)
        try:
            records = list(self.collection.find(query).sort('updated_at', -1))
        except (AttributeError, TypeError):
            records = list(self.collection.find(query))
            records.sort(key=lambda record: record.get('updated_at', ''), reverse=True)
        current = {}
        for record in records:
            current.setdefault(record.get('consent_type'), _clean(record))
        return current

    def get_history(self, *, personnel_id=None, actor_user_id=None, consent_type=None):
        self._ensure_collection()
        if self.collection is None:
            return []
        query = self._subject_query(personnel_id, actor_user_id, consent_type)
        try:
            records = self.collection.find(query).sort('updated_at', -1)
        except (AttributeError, TypeError):
            records = sorted(self.collection.find(query), key=lambda record: record.get('updated_at', ''), reverse=True)
        return [_clean(record) for record in records]
