"""Explicit allowlist-based data minimization utilities."""


class DataMinimizer:
    def minimize(self, data, allowed_fields):
        """Return a new mapping containing only explicitly allowed fields."""
        if data is None:
            return {}
        return {
            field: data[field]
            for field in allowed_fields
            if field in data
        }

    def minimize_nested(self, data, field_allowlists):
        """Minimize nested mappings using an explicit allowlist per section."""
        minimized = {}
        for section, allowed_fields in field_allowlists.items():
            if section in data:
                minimized[section] = self.minimize(data[section], allowed_fields)
        return minimized
