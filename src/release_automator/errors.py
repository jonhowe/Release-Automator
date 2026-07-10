class AutomatorError(RuntimeError):
    """A safe, user-facing workflow failure."""


class ConfigurationError(AutomatorError):
    """Invalid or incomplete repository configuration."""


class PlanDriftError(AutomatorError):
    """The repository no longer matches its frozen plan."""


class ExternalServiceError(AutomatorError):
    """OpenAI or GitHub returned an unusable response."""
