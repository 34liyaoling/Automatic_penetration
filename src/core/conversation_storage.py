import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import threading

logger = logging.getLogger(__name__)

class ConversationStorage:
    _lock = threading.Lock()

    def __init__(self, storage_dir: Path = None):
        if storage_dir is None:
            BASE_DIR = Path(__file__).resolve().parent.parent.parent
            storage_dir = BASE_DIR / "data" / "conversations"

        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._conversations: Dict[str, Dict[str, Any]] = {}
        self._load_all()

    def _get_conversation_file(self, conversation_id: str) -> Path:
        safe_id = conversation_id.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.storage_dir / f"{safe_id}.json"

    def _load_all(self):
        with self._lock:
            self._conversations = {}
            if not self.storage_dir.exists():
                return

            for file_path in self.storage_dir.glob("*.json"):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        conv = json.load(f)
                        conv_id = conv.get("id")
                        if conv_id:
                            self._conversations[conv_id] = conv
                except Exception as e:
                    logger.error(f"Failed to load conversation from {file_path}: {e}")

            logger.info(f"Loaded {len(self._conversations)} conversations from storage")

    def _save_conversation(self, conversation: Dict[str, Any]):
        conv_id = conversation.get("id")
        if not conv_id:
            return

        file_path = self._get_conversation_file(conv_id)
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(conversation, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved conversation {conv_id} to {file_path}, messages count: {len(conversation.get('messages', []))}")
        except Exception as e:
            logger.error(f"Failed to save conversation {conv_id}: {e}")

    def _delete_conversation_file(self, conversation_id: str):
        file_path = self._get_conversation_file(conversation_id)
        if file_path.exists():
            try:
                file_path.unlink()
                logger.debug(f"Deleted conversation file {file_path}")
            except Exception as e:
                logger.error(f"Failed to delete conversation file {file_path}: {e}")

    def create_conversation(self, conversation_id: str = None, name: str = "新对话") -> Dict[str, Any]:
        conv_id = conversation_id or f"conv_{int(datetime.now().timestamp() * 1000)}"

        if conv_id in self._conversations:
            return self._conversations[conv_id]

        conversation = {
            "id": conv_id,
            "name": name,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "messages": [],
            "task_ids": [],
            "tasks": {}
        }

        self._conversations[conv_id] = conversation
        self._save_conversation(conversation)
        return conversation

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self._conversations.get(conversation_id)

    def list_conversations(self) -> List[Dict[str, Any]]:
        conversations = list(self._conversations.values())
        return sorted(conversations, key=lambda x: x.get('updated_at', ''), reverse=True)

    def update_conversation(self, conversation_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if conversation_id not in self._conversations:
            return None

        conversation = self._conversations[conversation_id]
        conversation.update(updates)
        conversation['updated_at'] = datetime.now().isoformat()
        self._save_conversation(conversation)
        return conversation

    def add_message(self, conversation_id: str, role: str, content: str, metadata: Dict[str, Any] = None) -> bool:
        if conversation_id not in self._conversations:
            logger.warning(f"[STORAGE] add_message FAILED: conversation {conversation_id} not found")
            return False

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if metadata:
            message["metadata"] = metadata

        conversation = self._conversations[conversation_id]
        conversation["messages"].append(message)
        conversation["updated_at"] = datetime.now().isoformat()

        if len(conversation["messages"]) > 100:
            conversation["messages"] = conversation["messages"][-100:]

        self._save_conversation(conversation)
        logger.info(f"[STORAGE] add_message SUCCESS: conv={conversation_id}, role={role}, content='{content[:50]}...', total_msgs={len(conversation['messages'])}")
        return True

    def get_messages(self, conversation_id: str, max_messages: int = None) -> List[Dict[str, Any]]:
        if conversation_id not in self._conversations:
            return []

        messages = self._conversations[conversation_id]["messages"]
        if max_messages:
            return messages[-max_messages:]
        return messages

    def create_task(self, conversation_id: str, task_id: str, target: str) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id in conversation.get("tasks", {}):
            return True

        if "tasks" not in conversation:
            conversation["tasks"] = {}

        conversation["tasks"][task_id] = {
            "id": task_id,
            "target": target,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "messages": [],
            "vulnerabilities": [],
            "risk_assessment": None,
            "shell_state": None,
            "final_report": None
        }

        if task_id not in conversation["task_ids"]:
            conversation["task_ids"].append(task_id)

        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        logger.info(f"[STORAGE] create_task SUCCESS: conv={conversation_id}, task={task_id}, target={target}")
        return True

    def update_task_status(self, conversation_id: str, task_id: str, status: str) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id not in conversation.get("tasks", {}):
            return False

        task = conversation["tasks"][task_id]
        task["status"] = status
        if status in ["completed", "failed", "cancelled"]:
            task["completed_at"] = datetime.now().isoformat()

        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        logger.info(f"[STORAGE] update_task_status: conv={conversation_id}, task={task_id}, status={status}")
        return True

    def add_task_message(self, conversation_id: str, task_id: str, msg_type: str, content: str,
                        command: str = None, success: bool = None) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id not in conversation.get("tasks", {}):
            logger.warning(f"[STORAGE] add_task_message FAILED: task {task_id} not found")
            return False

        task = conversation["tasks"][task_id]
        message = {
            "type": msg_type,
            "content": content,
            "timestamp": datetime.now().isoformat()
        }
        if command:
            message["command"] = command
        if success is not None:
            message["success"] = success

        task["messages"].append(message)
        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        logger.info(f"[STORAGE] add_task_message: conv={conversation_id}, task={task_id}, type={msg_type}")
        return True

    def set_task_final_report(self, conversation_id: str, task_id: str, report: str) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id not in conversation.get("tasks", {}):
            return False

        task = conversation["tasks"][task_id]
        task["final_report"] = report
        task["messages"].append({
            "type": "final_report",
            "content": report,
            "timestamp": datetime.now().isoformat()
        })

        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        logger.info(f"[STORAGE] set_task_final_report: conv={conversation_id}, task={task_id}")
        return True

    def set_task_vulnerabilities(self, conversation_id: str, task_id: str, vulnerabilities: List[Dict]) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id not in conversation.get("tasks", {}):
            return False

        task = conversation["tasks"][task_id]
        task["vulnerabilities"] = vulnerabilities

        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        return True

    def set_task_risk_assessment(self, conversation_id: str, task_id: str, risk_assessment: Dict) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id not in conversation.get("tasks", {}):
            return False

        task = conversation["tasks"][task_id]
        task["risk_assessment"] = risk_assessment

        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        return True

    def set_task_shell_state(self, conversation_id: str, task_id: str, shell_state: Dict) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id not in conversation.get("tasks", {}):
            return False

        task = conversation["tasks"][task_id]
        task["shell_state"] = shell_state

        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        return True

    def get_task(self, conversation_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        if conversation_id not in self._conversations:
            return None
        conversation = self._conversations[conversation_id]
        return conversation.get("tasks", {}).get(task_id)

    def add_task_id(self, conversation_id: str, task_id: str) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        if task_id not in conversation["task_ids"]:
            conversation["task_ids"].append(task_id)
            conversation["updated_at"] = datetime.now().isoformat()
            self._save_conversation(conversation)
        return True

    def clear_messages(self, conversation_id: str) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        conversation["messages"] = []
        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        return True

    def delete_conversation(self, conversation_id: str) -> bool:
        if conversation_id in self._conversations:
            del self._conversations[conversation_id]
            self._delete_conversation_file(conversation_id)
            return True
        return False

    def rename_conversation(self, conversation_id: str, new_name: str) -> bool:
        if conversation_id not in self._conversations:
            return False

        conversation = self._conversations[conversation_id]
        conversation["name"] = new_name
        conversation["updated_at"] = datetime.now().isoformat()
        self._save_conversation(conversation)
        return True

    def search_conversations(self, keyword: str) -> List[Dict[str, Any]]:
        results = []
        for conv in self._conversations.values():
            if keyword.lower() in conv.get("name", "").lower():
                results.append(conv)
                continue

            for msg in conv.get("messages", []):
                if keyword.lower() in msg.get("content", "").lower():
                    results.append(conv)
                    break

        return sorted(results, key=lambda x: x.get('updated_at', ''), reverse=True)

    def get_conversation_count(self) -> int:
        return len(self._conversations)

    def export_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        return self._conversations.get(conversation_id)

    def import_conversation(self, conversation: Dict[str, Any]) -> bool:
        try:
            conv_id = conversation.get("id")
            if not conv_id:
                return False

            conversation["imported_at"] = datetime.now().isoformat()
            self._conversations[conv_id] = conversation
            self._save_conversation(conversation)
            return True
        except Exception as e:
            logger.error(f"Failed to import conversation: {e}")
            return False

    def sync_conversations(self, client_conversations: List[Dict[str, Any]]) -> Dict[str, Any]:
        result = {
            "synced": 0,
            "conflicts": [],
            "new_conversations": []
        }

        client_map = {conv.get("id"): conv for conv in client_conversations if conv.get("id")}

        for conv_id, server_conv in self._conversations.items():
            if conv_id in client_map:
                client_conv = client_map[conv_id]
                server_updated = server_conv.get("updated_at", "")
                client_updated = client_conv.get("updated_at", "")

                if server_updated > client_updated:
                    result["conflicts"].append({
                        "id": conv_id,
                        "server_version": server_conv,
                        "client_version": client_conv
                    })
                else:
                    self._conversations[conv_id] = client_conv
                    self._save_conversation(client_conv)
                    result["synced"] += 1
            else:
                result["new_conversations"].append(server_conv)

        for conv_id, client_conv in client_map.items():
            if conv_id not in self._conversations:
                self._conversations[conv_id] = client_conv
                self._save_conversation(client_conv)
                result["new_conversations"].append(client_conv)
                result["synced"] += 1

        return result

storage = ConversationStorage()
