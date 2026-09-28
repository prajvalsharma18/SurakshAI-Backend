"""MongoDB persistence for user accounts."""


class UserRepository:
    COLLECTION_NAME = 'users'

    def __init__(self, database=None):
        self.database = database
        self.collection = None
        self._indexes_ready = False

    def _ensure_collection(self):
        if self.collection is None and self.database is None:
            from src.db.mongodb import get_database
            self.database = get_database()
        if self.collection is None and self.database is not None:
            self.collection = self.database.get_collection(self.COLLECTION_NAME)
        if self.collection is not None and not self._indexes_ready:
            self._indexes_ready = True
            self.collection.create_index('user_id', unique=True)
            self.collection.create_index('username', unique=True)
            self.collection.create_index('personnel_id', sparse=True)
            self.collection.create_index('role')
            self.collection.create_index('status')

    def get_by_username(self, username):
        self._ensure_collection()
        return None if self.collection is None else self.collection.find_one({'username': username})

    def get_by_user_id(self, user_id):
        self._ensure_collection()
        return None if self.collection is None else self.collection.find_one({'user_id': user_id})

    def list(self, *, status=None, role=None):
        self._ensure_collection()
        if self.collection is None:
            return []
        query = {}
        if status is not None:
            query['status'] = status
        if role is not None:
            query['role'] = role
        try:
            return list(self.collection.find(query).sort([('created_at', -1), ('username', 1)]))
        except (AttributeError, TypeError):
            records = list(self.collection.find(query))
            return sorted(records, key=lambda item: (item.get('created_at', ''), item.get('username', '')), reverse=True)

    def create(self, record):
        self._ensure_collection()
        if self.collection is None:
            raise RuntimeError('User database is unavailable')
        if self.get_by_username(record['username']) is not None:
            raise ValueError(f"User {record['username']} already exists")
        if self.get_by_user_id(record['user_id']) is not None:
            raise ValueError(f"User {record['user_id']} already exists")
        self.collection.insert_one(dict(record))
        return dict(record)

    def update(self, user_id, updates):
        self._ensure_collection()
        if self.collection is None:
            raise RuntimeError('User database is unavailable')
        existing = self.get_by_user_id(user_id)
        if existing is None:
            raise ValueError(f'User {user_id} not found')
        patch = dict(updates)
        patch.pop('user_id', None)
        self.collection.update_one({'user_id': user_id}, {'$set': patch})
        return self.get_by_user_id(user_id)
