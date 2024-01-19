
from enum import Enum


class OrchestratorHealthStates(Enum):
    """
    Enum representing the health states of an orchestrator.
    """
    UNSPECIFIED = 0
    OFFLINE = 1
    RUNNING = 2
    DISABLED = 3
    UNKNOWN = 4

