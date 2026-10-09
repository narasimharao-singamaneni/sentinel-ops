from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ExecutionData:
    park: Any = None
    date: Any = None
    state: Any = None
    availability: Any = None
    change_detected: Optional[bool] = None
    context: Any = None
    explanation: Any = None
    alert: Any = None
    change_type: Any = None
    priority: Any = None
    previous_availability: Any = None
    reason: Any = None
    action_hint: Any = None
    urgency: Any = None