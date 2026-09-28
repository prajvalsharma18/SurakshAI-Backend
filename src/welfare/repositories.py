"""Minimal Mongo repositories for Phase 7 temporal workflow state."""

from datetime import datetime, timezone
from uuid import uuid4


ACTIVE_STATUSES = {'OPEN', 'ACKNOWLEDGED', 'UNDER_REVIEW', 'ACTION_PLANNED', 'FOLLOW_UP'}


def _clean(record):
    return None if record is None else {key: value for key, value in record.items() if key != '_id'}


class RiskPredictionRepository:
    def __init__(self, database=None):
        self.collection = None if database is None else database.get_collection('risk_predictions')
        if self.collection is not None:
            self.collection.create_index([('personnel_id', 1), ('reference_date', 1)], unique=True)
            self.collection.create_index([('personnel_id', 1), ('created_at', -1)])
            self.collection.create_index([('reference_date', -1), ('personnel_id', 1)])
            self.collection.create_index('risk_category')

    def save(self, observation):
        if self.collection is None:
            raise RuntimeError('Risk prediction database is unavailable')
        self.collection.update_one(
            {'personnel_id': observation['personnel_id'], 'reference_date': observation['reference_date']},
            {'$set': observation},
            upsert=True,
        )
        return observation

    def list_recent(self, personnel_id, start_date=None, end_date=None):
        if self.collection is None:
            raise RuntimeError('Risk prediction database is unavailable')
        query = {'personnel_id': personnel_id}
        if start_date or end_date:
            query['reference_date'] = {key: value for key, value in (('$gte', start_date), ('$lte', end_date)) if value}
        return [_clean(item) for item in self.collection.find(query).sort('reference_date', 1)]

    def get_history(self, personnel_id, *, page, page_size, reference_date_from=None, reference_date_to=None):
        if self.collection is None:
            raise RuntimeError('Risk prediction database is unavailable')
        query = {'personnel_id': personnel_id}
        if reference_date_from or reference_date_to:
            query['reference_date'] = {
                key: value for key, value in (
                    ('$gte', reference_date_from), ('$lte', reference_date_to)
                ) if value
            }
        try:
            total = self.collection.count_documents(query)
            cursor = self.collection.find(query).sort([('reference_date', -1), ('created_at', -1)])
            records = list(cursor.skip((page - 1) * page_size).limit(page_size))
        except (AttributeError, TypeError):
            records = [_clean(item) for item in self.collection.find(query)]
            records.sort(key=lambda item: (item.get('reference_date', ''), item.get('created_at', '')), reverse=True)
            total = len(records)
            start = (page - 1) * page_size
            records = records[start:start + page_size]
        return [_clean(item) for item in records], total

    def get_summary(self, *, reference_date_from=None, reference_date_to=None):
        if self.collection is None:
            raise RuntimeError('Risk prediction database is unavailable')
        query = {}
        if reference_date_from or reference_date_to:
            query['reference_date'] = {
                key: value for key, value in (
                    ('$gte', reference_date_from), ('$lte', reference_date_to)
                ) if value
            }
        try:
            pipeline = [
                {'$match': query},
                {'$sort': {'reference_date': -1, 'created_at': -1}},
                {'$group': {
                    '_id': '$personnel_id',
                    'risk_category': {'$first': '$risk_category'},
                    'reference_date': {'$first': '$reference_date'},
                }},
                {'$group': {
                    '_id': None,
                    'total_personnel': {'$sum': 1},
                    'risk_distribution': {'$push': '$risk_category'},
                    'latest_prediction_date': {'$max': '$reference_date'},
                }},
            ]
            result = next(iter(self.collection.aggregate(pipeline)), None)
            if result is None:
                return {'total_personnel': 0, 'risk_distribution': {}, 'latest_prediction_date': None}
            distribution = {}
            for category in result.get('risk_distribution', []):
                if category:
                    distribution[category] = distribution.get(category, 0) + 1
            return {
                'total_personnel': result.get('total_personnel', 0),
                'risk_distribution': distribution,
                'latest_prediction_date': result.get('latest_prediction_date'),
            }
        except (AttributeError, TypeError):
            records = [_clean(item) for item in self.collection.find(query)]
            records.sort(key=lambda item: (item.get('reference_date', ''), item.get('created_at', '')), reverse=True)
            latest = {}
            for record in records:
                latest.setdefault(record.get('personnel_id'), record)
            distribution = {}
            for record in latest.values():
                category = record.get('risk_category')
                if category:
                    distribution[category] = distribution.get(category, 0) + 1
            dates = [record.get('reference_date') for record in latest.values() if record.get('reference_date')]
            return {
                'total_personnel': len(latest),
                'risk_distribution': distribution,
                'latest_prediction_date': max(dates) if dates else None,
            }


class WelfareAlertRepository:
    def __init__(self, database=None):
        self.collection = None if database is None else database.get_collection('welfare_alerts')
        if self.collection is not None:
            self.collection.create_index('alert_id', unique=True)
            self.collection.create_index([('personnel_id', 1), ('created_at', -1)])
            self.collection.create_index([('personnel_id', 1), ('status', 1)])
            self.collection.create_index([('personnel_id', 1), ('trigger_type', 1), ('status', 1)])
            self.collection.create_index([('status', 1), ('severity', 1)])

    def create(self, payload):
        if self.collection is None:
            raise RuntimeError('Welfare alert database is unavailable')
        self.collection.insert_one(payload)
        return payload

    def get(self, alert_id):
        if self.collection is None:
            raise RuntimeError('Welfare alert database is unavailable')
        return _clean(self.collection.find_one({'alert_id': alert_id}))

    def list(self, personnel_id=None, statuses=None):
        if self.collection is None:
            raise RuntimeError('Welfare alert database is unavailable')
        query = {}
        if personnel_id:
            query['personnel_id'] = personnel_id
        if statuses:
            query['status'] = {'$in': list(statuses)}
        return [_clean(item) for item in self.collection.find(query).sort('created_at', -1)]

    def update(self, alert_id, updates):
        if self.collection is None:
            raise RuntimeError('Welfare alert database is unavailable')
        self.collection.update_one({'alert_id': alert_id}, {'$set': updates})
        return self.get(alert_id)

    def dashboard_summary(self):
        if self.collection is None:
            raise RuntimeError('Welfare alert database is unavailable')
        query = {'status': {'$in': list(ACTIVE_STATUSES)}}
        try:
            pipeline = [
                {'$match': query},
                {'$group': {'_id': '$severity', 'count': {'$sum': 1}}},
            ]
            grouped = list(self.collection.aggregate(pipeline))
            return {
                'open': sum(int(item.get('count', 0)) for item in grouped),
                'attention': next((int(item.get('count', 0)) for item in grouped if item.get('_id') == 'ATTENTION'), 0),
                'priority': next((int(item.get('count', 0)) for item in grouped if item.get('_id') == 'PRIORITY'), 0),
            }
        except (AttributeError, TypeError):
            records = list(self.collection.find(query))
            return {
                'open': len(records),
                'attention': sum(1 for record in records if record.get('severity') == 'ATTENTION'),
                'priority': sum(1 for record in records if record.get('severity') == 'PRIORITY'),
            }


class WelfareInterventionRepository:
    def __init__(self, database=None):
        self.collection = None if database is None else database.get_collection('welfare_interventions')
        if self.collection is not None:
            self.collection.create_index('intervention_id', unique=True)
            self.collection.create_index('alert_id')
            self.collection.create_index([('personnel_id', 1), ('created_at', -1)])
            self.collection.create_index([('status', 1), ('scheduled_follow_up', 1)])

    def create(self, payload):
        if self.collection is None:
            raise RuntimeError('Welfare intervention database is unavailable')
        self.collection.insert_one(payload)
        return payload

    def list_for_alert(self, alert_id):
        if self.collection is None:
            raise RuntimeError('Welfare intervention database is unavailable')
        return [_clean(item) for item in self.collection.find({'alert_id': alert_id}).sort('created_at', -1)]

    def get(self, intervention_id):
        if self.collection is None:
            raise RuntimeError('Welfare intervention database is unavailable')
        return _clean(self.collection.find_one({'intervention_id': intervention_id}))

    def update(self, intervention_id, updates):
        if self.collection is None:
            raise RuntimeError('Welfare intervention database is unavailable')
        self.collection.update_one({'intervention_id': intervention_id}, {'$set': updates})
        return self.get(intervention_id)

    def dashboard_summary(self):
        if self.collection is None:
            raise RuntimeError('Welfare intervention database is unavailable')
        active_statuses = ['PLANNED', 'IN_PROGRESS']
        try:
            pipeline = [{
                '$group': {
                    '_id': None,
                    'active': {'$sum': {'$cond': [{'$in': ['$status', active_statuses]}, 1, 0]}},
                    'follow_up': {'$sum': {'$cond': [{'$and': [
                        {'$in': ['$status', active_statuses]},
                        {'$ne': ['$scheduled_follow_up', None]},
                    ]}, 1, 0]}},
                },
            }]
            result = next(iter(self.collection.aggregate(pipeline)), None) or {}
            return {'active': int(result.get('active', 0)), 'follow_up': int(result.get('follow_up', 0))}
        except (AttributeError, TypeError):
            records = list(self.collection.find())
            active = [record for record in records if record.get('status') in active_statuses]
            return {
                'active': len(active),
                'follow_up': sum(1 for record in active if record.get('scheduled_follow_up') is not None),
            }


def new_id(prefix):
    return f'{prefix}-{uuid4()}'


def utc_now():
    return datetime.now(timezone.utc).isoformat()
