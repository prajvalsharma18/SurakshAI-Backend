"""Internal metadata validation for synthetic dataset ownership."""


def validate_seed_batch_id(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError('seed_batch_id must be a non-empty string of at most 128 characters')
    return value
