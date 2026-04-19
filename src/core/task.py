import uuid
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from enum import Enum

class TaskCancelledError(Exception):
    """任务被取消时抛出的异常"""
    pass

logger = logging.getLogger(__name__)

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_DECISION = "waiting_decision"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

class TaskProgress:
    def __init__(self):
        self.current_step = 0
        self.total_steps = 5
        self.step_name = ""
        self.step_description = ""
        self.logs: List[str] = []
        self.percentage = 0

    def update(self, step: int, step_name: str, description: str = ""):
        self.current_step = step
        self.step_name = step_name
        self.step_description = description
        self.percentage = int((step / self.total_steps) * 100)
        self.add_log(f"[{step}/{self.total_steps}] {step_name}: {description}")

    def add_log(self, message: str):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{timestamp}] {message}")
        if len(self.logs) > 100:
            self.logs = self.logs[-100:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "step_name": self.step_name,
            "step_description": self.step_description,
            "percentage": self.percentage,
            "logs": self.logs
        }

class Task:
    def __init__(self, target: str, options = None, conversation_id: str = None):
        self.id = str(uuid.uuid4())
        self.target = target
        self.options = options or {}
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.status = TaskStatus.PENDING
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self.results: Dict[str, Any] = {}
        self.errors: List[str] = []
        self.llm_analysis_raw: Optional[str] = None
        self.progress = TaskProgress()
        self._cancelled = False
        self._progress_callback = None
        self.human_interaction_mode = options.get("human_interaction", False) if options else False
        self.pending_findings: List[str] = []
        self.human_commands: List[Dict[str, Any]] = []
        self.decision_requested = False
        self._decision_event = None
        self._terminal = None

    def set_terminal(self, terminal):
        self._terminal = terminal

    def get_terminal(self):
        return self._terminal

    def cleanup(self):
        if self._terminal:
            self._terminal.cleanup()
            self._terminal = None
            logger.info(f"Task {self.id} resources cleaned up")

    def set_progress_callback(self, callback):
        self._progress_callback = callback

    def update_progress(self, step: int, step_name: str, description: str = ""):
        self.progress.update(step, step_name, description)
        if self._progress_callback:
            try:
                asyncio.create_task(self._progress_callback(self))
            except Exception as e:
                logger.error(f"Progress callback error: {e}")

    def add_progress_log(self, message: str):
        self.progress.add_log(message)
        if self._progress_callback:
            try:
                asyncio.create_task(self._progress_callback(self))
            except Exception as e:
                logger.error(f"Progress log callback error: {e}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "target": self.target,
            "conversation_id": self.conversation_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "results": self.results,
            "errors": self.errors,
            "llm_analysis_raw": self.llm_analysis_raw,
            "duration": self.get_duration(),
            "progress": self.progress.to_dict()
        }

    def get_duration(self) -> Optional[float]:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        elif self.started_at:
            return (datetime.now() - self.started_at).total_seconds()
        return None

    def start(self):
        self.status = TaskStatus.RUNNING
        self.started_at = datetime.now()
        self.update_progress(1, "初始化", "任务开始执行")
        logger.info(f"Task {self.id} started for target {self.target}")

    def complete(self):
        self.status = TaskStatus.COMPLETED
        self.completed_at = datetime.now()
        self.update_progress(5, "完成", "任务执行完成")
        logger.info(f"Task {self.id} completed")

    def fail(self, error: str):
        self.status = TaskStatus.FAILED
        self.completed_at = datetime.now()
        self.errors.append(error)
        self.add_progress_log(f"错误: {error}")
        logger.error(f"Task {self.id} failed: {error}")

    def cancel(self):
        self.status = TaskStatus.CANCELLED
        self._cancelled = True
        self.completed_at = datetime.now()
        self.add_progress_log("任务已取消")
        logger.info(f"Task {self.id} cancelled")

    def is_cancelled(self) -> bool:
        return self._cancelled
