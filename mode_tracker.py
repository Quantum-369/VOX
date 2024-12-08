from enum import Enum
from typing import List, Dict, Optional
from datetime import datetime
import json

class ConversationMode(Enum):
    GENERAL = "general"
    DATABASE = "database"
    CREATIVE = "creative"
    EXPLANATION = "explanation"
    TRANSITIONING = "transitioning"

class ModeTransition:
    def __init__(self, from_mode: ConversationMode, to_mode: ConversationMode, 
                 confidence: float, timestamp: datetime):
        self.from_mode = from_mode
        self.to_mode = to_mode
        self.confidence = confidence
        self.timestamp = timestamp

class ModeTracker:
    def __init__(self, decay_rate: float = 0.1, confidence_threshold: float = 0.7):
        self.current_mode = ConversationMode.GENERAL
        self.confidence = 1.0
        self.mode_start_time = datetime.now()
        self.previous_mode = None
        self.transitions: List[ModeTransition] = []
        self.decay_rate = decay_rate
        self.confidence_threshold = confidence_threshold
        self.mode_locks: Dict[str, bool] = {}
    def set_db_connection(self, is_active: bool):
        self.db_connection_active = is_active
        if not is_active and self.current_mode == ConversationMode.DATABASE:
            self.revert_to_previous_mode()
    def update_mode(self, new_mode: ConversationMode, confidence: float) -> bool:
        if self.is_mode_locked():
            return False

        if confidence >= self.confidence_threshold:
            print(f"\nMode Switch: {self.current_mode.value} -> {new_mode.value}")
            print(f"Confidence: {confidence}")
            transition = ModeTransition(
                self.current_mode,
                new_mode,
                confidence,
                datetime.now()
            )
            self.transitions.append(transition)
            self.previous_mode = self.current_mode
            self.current_mode = new_mode
            self.confidence = confidence
            self.mode_start_time = datetime.now()
            return True
        return False

    def lock_mode(self, task_id: str):
        self.mode_locks[task_id] = True

    def unlock_mode(self, task_id: str):
        self.mode_locks.pop(task_id, None)

    def is_mode_locked(self) -> bool:
        return bool(self.mode_locks)

    def decay_confidence(self):
        time_diff = (datetime.now() - self.mode_start_time).total_seconds()
        self.confidence *= (1 - self.decay_rate * time_diff)
        
        if self.confidence < self.confidence_threshold and self.previous_mode:
            self.revert_to_previous_mode()

    def revert_to_previous_mode(self):
        if self.previous_mode:
            self.current_mode = self.previous_mode
            self.confidence = self.confidence_threshold
            self.mode_start_time = datetime.now()
            self.previous_mode = None

    def get_mode_history(self, limit: int = 3) -> List[ModeTransition]:
        return sorted(self.transitions, key=lambda x: x.timestamp, reverse=True)[:limit]

    def to_dict(self) -> dict:
        return {
            "current_mode": self.current_mode.value,
            "confidence": self.confidence,
            "mode_start_time": self.mode_start_time.isoformat(),
            "previous_mode": self.previous_mode.value if self.previous_mode else None,
            "transitions": [
                {
                    "from_mode": t.from_mode.value,
                    "to_mode": t.to_mode.value,
                    "confidence": t.confidence,
                    "timestamp": t.timestamp.isoformat()
                } for t in self.transitions[-3:]
            ]
        }