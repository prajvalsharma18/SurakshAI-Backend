"""Deterministic persistence policy for Phase 7 welfare workflow signals."""

from dataclasses import dataclass
from datetime import date, timedelta

from config import (
    WELFARE_ALERT_COOLDOWN_DAYS,
    WELFARE_ALERT_ELEVATED_OBSERVATIONS,
    WELFARE_ALERT_HIGH_OBSERVATIONS,
    WELFARE_ALERT_PERSISTENCE_WINDOW_DAYS,
)


@dataclass(frozen=True)
class AlertDecision:
    qualifies: bool
    severity: str | None
    trigger_type: str | None
    reason: str
    risk_category: str | None
    should_escalate: bool = False


class AlertPolicy:
    SEVERITIES = {'INFO', 'ATTENTION', 'PRIORITY'}

    def __init__(self, persistence_window_days=WELFARE_ALERT_PERSISTENCE_WINDOW_DAYS,
                 elevated_observations=WELFARE_ALERT_ELEVATED_OBSERVATIONS,
                 high_observations=WELFARE_ALERT_HIGH_OBSERVATIONS,
                 cooldown_days=WELFARE_ALERT_COOLDOWN_DAYS):
        self.persistence_window_days = persistence_window_days
        self.elevated_observations = elevated_observations
        self.high_observations = high_observations
        self.cooldown_days = cooldown_days

    def evaluate(self, observations, *, existing_alert=None, now=None):
        now = now or date.today()
        normalized = []
        for observation in observations:
            try:
                observed_date = date.fromisoformat(observation['reference_date'])
            except (KeyError, TypeError, ValueError):
                continue
            if now - timedelta(days=self.persistence_window_days) <= observed_date <= now:
                normalized.append((observed_date, observation.get('risk_category')))
        normalized.sort()
        elevated = [item for item in normalized if item[1] == 'ELEVATED']
        high = [item for item in normalized if item[1] == 'HIGH']
        if len(high) >= self.high_observations:
            severity, trigger, count, required = 'PRIORITY', 'PERSISTENT_HIGH_RISK', len(high), self.high_observations
        elif len(elevated) >= self.elevated_observations:
            severity, trigger, count, required = 'ATTENTION', 'REPEATED_ELEVATED_RISK', len(elevated), self.elevated_observations
        else:
            return AlertDecision(False, None, None, 'Persistence criteria not satisfied', None)
        if existing_alert and existing_alert.get('status') not in {'RESOLVED', 'DISMISSED'}:
            current = existing_alert.get('severity')
            if current == 'PRIORITY' or current == severity:
                return AlertDecision(False, severity, trigger, 'Existing unresolved alert suppresses duplicate', high[-1][1] if high else elevated[-1][1])
            if current == 'ATTENTION' and severity == 'PRIORITY':
                return AlertDecision(True, severity, trigger, 'Persistent high risk escalates existing alert', 'HIGH', True)
        latest = max(item[0] for item in normalized)
        if existing_alert and existing_alert.get('created_at'):
            try:
                created = date.fromisoformat(str(existing_alert['created_at'])[:10])
                if latest - created < timedelta(days=self.cooldown_days):
                    return AlertDecision(False, severity, trigger, 'Alert cooldown is active', high[-1][1] if high else elevated[-1][1])
            except ValueError:
                pass
        return AlertDecision(True, severity, trigger, f'{count} qualifying observations out of {required} required', high[-1][1] if high else elevated[-1][1])
