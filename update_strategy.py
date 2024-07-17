

from enum import Enum

class UpdateStrategyException(Exception):
    pass

from enum import Enum

class UpdateStrategy(Enum):
    """
    Enumeration representing different update strategies.
    
    Attributes:
        ALL_AT_ONCE: Update all devices at once.
        DATACENTER_DEVICES_FIRST: Update datacenter devices first.
        EDGE_DEVICES_FIRST: Update edge devices first.
    """
    ALL_AT_ONCE = 0
    DATACENTER_DEVICES_FIRST = 1
    EDGE_DEVICES_FIRST = 2