class PolicyViolation(ValueError):
    """Raised when a request falls outside the declared collection policy. Never escalate; stop."""
