from fastapi import APIRouter, HTTPException, BackgroundTasks, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Optional, Dict, Any
from src.core.workflow import WorkflowEngine
from src.core.conversation_storage import storage
from src.config.settings import settings
import logging
import json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["API"])

workflow_engine = WorkflowEngine()


class ScanRequest(BaseModel):
    user_message: str
    options: Optional[Dict[str, Any]] = None
    conversation_id: Optional[str] = None
    context_settings: Optional[Dict[str, Any]] = None


class ConversationRequest(BaseModel):
    conversation_id: Optional[str] = None
    name: Optional[str] = None


class MessageRequest(BaseModel):
    role: str
    content: str
    metadata: Optional[Dict[str, Any]] = None


class UpdateConversationRequest(BaseModel):
    name: Optional[str] = None
    messages: Optional[list] = None


class HumanDecisionRequest(BaseModel):
    commands: Optional[list] = None
    action: str = "continue"
    reason: Optional[str] = None


class LLMProviderRequest(BaseModel):
    provider: str


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "智能渗透测试系统"}


@router.get("/conversations")
async def list_conversations():
    conversations = storage.list_conversations()
    return {"success": True, "conversations": conversations}


@router.post("/conversations")
async def create_conversation(request: ConversationRequest):
    conv = storage.create_conversation(request.conversation_id, request.name or "新对话")
    return {"success": True, "conversation": conv}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    conv = storage.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True, "conversation": conv}


@router.put("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, request: UpdateConversationRequest):
    updates = {}
    if request.name is not None:
        updates["name"] = request.name
    if request.messages is not None:
        updates["messages"] = request.messages
    conv = storage.update_conversation(conversation_id, updates)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True, "conversation": conv}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    workflow_engine.delete_conversation(conversation_id)
    return {"success": True, "message": "Conversation deleted"}


@router.delete("/conversations")
async def delete_all_conversations():
    all_conversations = storage.list_conversations()
    deleted_count = 0
    for conv in all_conversations:
        workflow_engine.delete_conversation(conv["id"])
        deleted_count += 1
    return {"success": True, "message": f"Deleted {deleted_count} conversations"}


@router.post("/conversations/{conversation_id}/messages")
async def add_message(conversation_id: str, request: MessageRequest):
    logger.info(f"[API] add_message called: conv_id={conversation_id}, role={request.role}, content='{request.content[:50]}...'")
    success = storage.add_message(
        conversation_id,
        request.role,
        request.content,
        request.metadata
    )
    if not success:
        logger.error(f"[API] add_message FAILED: conversation {conversation_id} not found")
        raise HTTPException(status_code=404, detail="Conversation not found")
    logger.info(f"[API] add_message SUCCESS")
    return {"success": True}


@router.get("/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: str, max_messages: Optional[int] = None):
    messages = storage.get_messages(conversation_id, max_messages)
    return {"success": True, "messages": messages}


@router.post("/conversations/{conversation_id}/clear")
async def clear_conversation(conversation_id: str):
    success = storage.clear_messages(conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"success": True, "message": "Conversation cleared"}


@router.get("/llm/providers")
async def get_llm_providers():
    providers = workflow_engine.llm_engine.get_available_providers()
    current_provider = workflow_engine.llm_engine.provider
    return {
        "success": True,
        "providers": providers,
        "current_provider": current_provider
    }


@router.post("/llm/switch")
async def switch_llm_provider(request: LLMProviderRequest):
    try:
        workflow_engine.llm_engine.switch_provider(request.provider)
        return {
            "success": True,
            "message": f"Switched to LLM provider: {request.provider}",
            "provider": request.provider
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to switch LLM provider: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/tasks")
async def create_task(request: ScanRequest):
    try:
        task = workflow_engine.create_task(
            request.user_message,
            request.options,
            request.conversation_id
        )

        return {"success": True, "task_id": task.id, "task": task.to_dict()}
    except Exception as e:
        logger.error(f"Failed to create task: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    task = workflow_engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "task": task.to_dict()}


@router.post("/tasks/{task_id}/execute")
async def execute_task(task_id: str, background_tasks: BackgroundTasks):
    task = workflow_engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    async def run_task():
        await workflow_engine.execute_task_async(task_id)

    background_tasks.add_task(run_task)
    return {"success": True, "message": "Task execution started", "task_id": task_id}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    result = workflow_engine.cancel_task(task_id)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to cancel task"))
    return result


@router.post("/tasks/{task_id}/human-decision")
async def submit_human_decision(task_id: str, request: HumanDecisionRequest):
    task = workflow_engine.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status.value != "waiting_decision":
        raise HTTPException(status_code=400, detail="Task is not waiting for human decision")

    task.receive_human_decision(commands=request.commands, action=request.action)

    return {
        "success": True,
        "message": "Human decision received",
        "task_id": task_id
    }


def build_report_data(task_id: str, task, task_data):
    findings = []
    findings_file = settings.FINDINGS_DIR / f"findings_{task_id}.json"
    if findings_file.exists():
        try:
            with open(findings_file, 'r', encoding='utf-8') as f:
                findings = json.load(f).get("findings", [])
        except Exception as e:
            logger.error(f"Failed to load findings: {e}")

    if task:
        llm_analysis_raw = task.llm_analysis_raw
        target = task.target
    else:
        llm_analysis_raw = task_data.get("final_report") if task_data else None
        target = task_data.get("target", "") if task_data else ""

    report_data = {
        "target": target,
        "summary": llm_analysis_raw or "渗透测试完成",
        "findings": findings,
        "llm_analysis_raw": llm_analysis_raw
    }
    return report_data


@router.get("/tasks/{task_id}/report")
async def get_task_report(task_id: str):
    task = workflow_engine.get_task(task_id)
    task_record = None
    conversation_id = None

    if not task:
        for conv in storage.list_conversations():
            if task_id in conv.get("tasks", {}):
                task_record = conv["tasks"][task_id]
                conversation_id = conv["id"]
                break

    if not task and not task_record:
        raise HTTPException(status_code=404, detail="Task not found")

    status = task.status.value if task else task_record.get("status", "pending")
    if status not in ["completed", "cancelled", "failed"]:
        raise HTTPException(status_code=400, detail="Task is not completed yet")

    try:
        report_data = build_report_data(task_id, task, task_record)

        from src.report import ReportGenerator
        generator = ReportGenerator()
        output_path, html_content = generator.generate(report_data)
        return {
            "success": True,
            "report_path": output_path,
            "report_html": html_content,
            "task_id": task_id
        }
    except Exception as e:
        logger.error(f"Failed to generate report for task {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
        workflow_engine.register_connection(websocket)

        try:
            while True:
                try:
                    data = await websocket.receive_json()
                    if data.get("type") == "ping":
                        await websocket.send_json({"type": "pong"})
                except WebSocketDisconnect:
                    break
                except Exception:
                    break
        finally:
            workflow_engine.unregister_connection(websocket)
    except Exception as e:
        logger.error(f"WebSocket error: {type(e).__name__}: {e}")
