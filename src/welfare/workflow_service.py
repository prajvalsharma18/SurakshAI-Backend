"""Phase 7 human-in-the-loop welfare alert workflow orchestration."""

from datetime import date, timedelta

from src.security.audit import RESULT_SUCCESS, get_audit_service
from src.welfare.alert_policy import AlertPolicy
from src.welfare.repositories import ACTIVE_STATUSES, WelfareAlertRepository, WelfareInterventionRepository, RiskPredictionRepository, new_id, utc_now


class WorkflowError(RuntimeError):
    """Raised when a welfare workflow operation cannot be completed safely."""


class WelfareWorkflowService:
    TRANSITIONS = {
        'OPEN': {'ACKNOWLEDGED', 'DISMISSED'},
        'ACKNOWLEDGED': {'UNDER_REVIEW'},
        'UNDER_REVIEW': {'ACTION_PLANNED', 'DISMISSED'},
        'ACTION_PLANNED': {'FOLLOW_UP', 'RESOLVED'},
        'FOLLOW_UP': {'RESOLVED', 'ACTION_PLANNED'},
        'RESOLVED': set(),
        'DISMISSED': set(),
    }
    ACTION_TYPES = {
        'WELFARE_CHECK_IN', 'WORKLOAD_REVIEW', 'RECOVERY_DISCUSSION',
        'LEAVE_PLANNING_DISCUSSION', 'COUNSELLING_REFERRAL', 'FOLLOW_UP', 'OTHER_SUPPORT',
    }
    INTERVENTION_STATUSES = {'PLANNED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'}

    def __init__(self, risk_service, prediction_repository, alert_repository, intervention_repository,
                 policy=None, audit_service=None):
        self.risk_service = risk_service
        self.predictions = prediction_repository
        self.alerts = alert_repository
        self.interventions = intervention_repository
        self.policy = policy or AlertPolicy()
        self.audit = audit_service or get_audit_service()

    def _audit(self, actor, action, alert=None, personnel_id=None, status=None):
        self.audit.record_event(
            actor_user_id=(actor or {}).get('user_id'),
            actor_role=(actor or {}).get('role'),
            action=action,
            resource_type='welfare_alert',
            resource_id=(alert or {}).get('alert_id'),
            result=RESULT_SUCCESS,
            source='welfare_workflow_service',
            purpose='Human-in-the-loop welfare workflow',
            target_personnel_id=personnel_id or (alert or {}).get('personnel_id'),
            status=status or (alert or {}).get('status'),
        )

    def evaluate(self, personnel_id, reference_date=None, identity=None):
        prediction = self.risk_service.predict_for_personnel(personnel_id, reference_date, identity)
        observation = {
            'prediction_id': new_id('prediction'),
            'personnel_id': personnel_id,
            'reference_date': prediction['reference_date'],
            'risk_category': prediction['risk_category'],
            'model_version': prediction['model_version'],
            'data_mode': prediction['data_mode'],
            'created_at': utc_now(),
        }
        seed_batch_id = (identity or {}).get('seed_batch_id')
        if seed_batch_id is not None:
            observation['seed_batch_id'] = seed_batch_id
        self.predictions.save(observation)
        recent = self.predictions.list_recent(
            personnel_id,
            start_date=(date.fromisoformat(prediction['reference_date']) - timedelta(days=self.policy.persistence_window_days)).isoformat(),
            end_date=prediction['reference_date'],
        )
        existing = next(iter(self.alerts.list(personnel_id)), None)
        decision = self.policy.evaluate(recent, existing_alert=existing, now=date.fromisoformat(prediction['reference_date']))
        if not decision.qualifies:
            return {'created': False, 'decision': decision.reason, 'prediction': observation, 'alert': existing}
        if decision.should_escalate and existing:
            alert = self.alerts.update(existing['alert_id'], {
                'severity': decision.severity,
                'trigger_type': decision.trigger_type,
                'current_risk_category': decision.risk_category,
                'reference_date': prediction['reference_date'],
                'persistence_reason': decision.reason,
            })
            self._audit(identity, 'ALERT_CREATED', alert)
            return {'created': True, 'escalated': True, 'prediction': observation, 'alert': alert}
        alert_payload = {
            'alert_id': new_id('alert'),
            'personnel_id': personnel_id,
            'created_at': utc_now(),
            'reference_date': prediction['reference_date'],
            'severity': decision.severity,
            'trigger_type': decision.trigger_type,
            'current_risk_category': decision.risk_category,
            'model_version': prediction['model_version'],
            'status': 'OPEN',
            'assigned_to': None,
            'acknowledged_at': None,
            'reviewed_at': None,
            'resolved_at': None,
            'resolution_type': None,
            'source': 'welfare_workflow_service.evaluate',
            'created_by': (identity or {}).get('user_id'),
            'persistence_reason': decision.reason,
        }
        if seed_batch_id is not None:
            alert_payload['seed_batch_id'] = seed_batch_id
        alert = self.alerts.create(alert_payload)
        self._audit(identity, 'ALERT_CREATED', alert)
        return {'created': True, 'escalated': False, 'prediction': observation, 'alert': alert}

    def list_alerts(self, personnel_id=None, statuses=None):
        return self.alerts.list(personnel_id, statuses)

    def get_alert(self, alert_id):
        alert = self.alerts.get(alert_id)
        if alert is None:
            raise WorkflowError('Alert not found')
        return alert

    def transition(self, alert_id, target_status, actor, **fields):
        alert = self.get_alert(alert_id)
        if target_status not in self.TRANSITIONS.get(alert['status'], set()):
            raise WorkflowError(f"Invalid alert transition: {alert['status']} -> {target_status}")
        updates = {'status': target_status}
        updates.update({key: value for key, value in fields.items() if value is not None})
        if target_status == 'ACKNOWLEDGED':
            updates['acknowledged_at'] = utc_now()
            action = 'ALERT_ACKNOWLEDGED'
        elif target_status == 'UNDER_REVIEW':
            updates['reviewed_at'] = utc_now()
            action = 'ALERT_REVIEWED'
        elif target_status == 'FOLLOW_UP':
            action = 'FOLLOW_UP_SCHEDULED'
        elif target_status == 'RESOLVED':
            updates['resolved_at'] = utc_now()
            updates['resolution_type'] = fields.get('resolution_type', 'EXPLICIT_HUMAN_RESOLUTION')
            action = 'ALERT_RESOLVED'
        elif target_status == 'DISMISSED':
            updates['resolved_at'] = utc_now()
            updates['resolution_type'] = fields.get('resolution_type', 'DISMISSED_BY_AUTHORIZED_REVIEW')
            action = 'ALERT_DISMISSED'
        else:
            action = 'ALERT_REVIEWED'
        updated = self.alerts.update(alert_id, updates)
        self._audit(actor, action, updated)
        return updated

    def create_intervention(self, alert_id, personnel_id, action_type, actor, scheduled_follow_up=None):
        alert = self.get_alert(alert_id)
        if alert['personnel_id'] != personnel_id or alert['status'] not in {'UNDER_REVIEW', 'ACTION_PLANNED', 'FOLLOW_UP'}:
            raise WorkflowError('Alert is not available for intervention planning')
        if action_type not in self.ACTION_TYPES:
            raise ValueError('Unsupported intervention action type')
        intervention_payload = {
            'intervention_id': new_id('intervention'),
            'alert_id': alert_id,
            'personnel_id': personnel_id,
            'action_type': action_type,
            'status': 'PLANNED',
            'created_at': utc_now(),
            'scheduled_follow_up': scheduled_follow_up,
            'completed_at': None,
            'created_by': (actor or {}).get('user_id'),
            'outcome_category': None,
        }
        seed_batch_id = (actor or {}).get('seed_batch_id')
        if seed_batch_id is not None:
            intervention_payload['seed_batch_id'] = seed_batch_id
        intervention = self.interventions.create(intervention_payload)
        if alert['status'] == 'UNDER_REVIEW':
            self.transition(alert_id, 'ACTION_PLANNED', actor)
        self._audit(actor, 'INTERVENTION_CREATED', alert)
        return intervention

    def update_intervention(self, intervention_id, status, actor, outcome_category=None):
        if status not in self.INTERVENTION_STATUSES:
            raise ValueError('Unsupported intervention status')
        updates = {'status': status}
        if outcome_category is not None:
            updates['outcome_category'] = outcome_category
        if status == 'COMPLETED':
            updates['completed_at'] = utc_now()
        intervention = self.interventions.update(intervention_id, updates)
        if intervention is None:
            raise WorkflowError('Intervention not found')
        alert = self.get_alert(intervention['alert_id'])
        self._audit(actor, 'INTERVENTION_UPDATED', alert)
        return intervention
