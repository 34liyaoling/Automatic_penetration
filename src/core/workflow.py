import logging
import threading
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from src.core.task import Task, TaskStatus, TaskCancelledError
from src.core.conversation_storage import storage
from src.tools.terminal_tool import TerminalTool
from src.analysis.llm_engine import LLMEngine
from src.config.settings import settings

logger = logging.getLogger(__name__)


def _check_command_syntax(command: str) -> tuple:
    """检查命令语法，返回 (是否有效, 修正后的命令, 错误信息)"""
    if 'curl' in command or 'wget' in command:
        import re
        if re.search(r'curl\s+[`"]', command) or re.search(r'wget\s+[`"]', command):
            fixed = command.replace('`', "'")
            return False, fixed, "URL引号错误：已自动修正反引号为单引号"
        if re.search(r'curl\s+[^"\']*\?[^"\']*&', command):
            fixed = re.sub(r'(curl\s+)([^"\']*?)(\?[^"\']*?&)', r"\1'\2\3'", command)
            if "'" in fixed:
                return False, fixed, "URL含&符：已用单引号包裹"
    return True, command, ""


class WorkflowEngine:
    def __init__(self):
        self.terminal = TerminalTool()
        self.llm_engine = LLMEngine()
        self.active_tasks: Dict[str, Task] = {}
        self._connections = []
        self._task_locks: Dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def get_terminal_for_task(self, task_id: str) -> TerminalTool:
        task = self.active_tasks.get(task_id)
        if not task:
            return TerminalTool(task_id=task_id)
        if not task.get_terminal():
            terminal = TerminalTool(task_id=task_id)
            task.set_terminal(terminal)
        return task.get_terminal()

    def cleanup_task_terminal(self, task_id: str):
        task = self.active_tasks.get(task_id)
        if task:
            task.cleanup()

    @property
    def conversations(self):
        return storage._conversations

    def get_conversation(self, conversation_id: str):
        return storage.get_conversation(conversation_id)

    def list_conversations(self) -> List[Dict[str, Any]]:
        return storage.list_conversations()

    def add_message_to_conversation(self, conversation_id: str, role: str, content: str, metadata: Dict[str, Any] = None, task_data: Dict[str, Any] = None, task_id: str = None):
        conv = self.get_conversation(conversation_id)
        if conv:
            message = {
                "role": role,
                "content": content,
                "timestamp": datetime.now().isoformat()
            }
            if metadata:
                message["metadata"] = metadata
            if task_data:
                message["task_data"] = task_data
            if task_id:
                message["task_id"] = task_id
            conv["messages"].append(message)
            if len(conv["messages"]) > 100:
                conv["messages"] = conv["messages"][-100:]
            storage._save_conversation(conv)

    def delete_conversation(self, conversation_id: str):
        conv = storage.get_conversation(conversation_id)
        if conv:
            for msg in conv.get("messages", []):
                if msg.get("task_data") and msg["task_data"].get("id"):
                    task_id = msg["task_data"]["id"]
                    try:
                        if task_id in self.active_tasks:
                            del self.active_tasks[task_id]
                    except Exception as e:
                        logger.error(f"Failed to cleanup for task {task_id}: {e}")
        storage.delete_conversation(conversation_id)

    def get_task_lock(self, task_id: str) -> threading.Lock:
        with self._locks_lock:
            if task_id not in self._task_locks:
                self._task_locks[task_id] = threading.Lock()
            return self._task_locks[task_id]

    def is_task_cancelled(self, task_id: str) -> bool:
        task = self.active_tasks.get(task_id)
        if task:
            return task.is_cancelled()
        return False

    def _release_task_lock(self, task_id: str):
        with self._locks_lock:
            if task_id in self._task_locks:
                del self._task_locks[task_id]

    def _check_task_cancelled(self, task_id: str, task: Task) -> bool:
        task_obj = self.get_task(task_id)
        if not task_obj or task_obj.is_cancelled():
            logger.info(f"Task {task_id} was cancelled")
            task.cancel()
            raise TaskCancelledError(f"Task {task_id} was cancelled")
        return False

    def _cleanup_task_resources(self, task: Task):
        task_id = task.id
        if task.results:
            for tool_name, result in task.results.items():
                if isinstance(result, dict) and result.get("resource_ids"):
                    pass
        self.cleanup_task_terminal(task_id)
        self._release_task_lock(task_id)
        logger.info(f"Cleaned up resources for task {task_id}")

    def register_connection(self, websocket):
        if websocket not in self._connections:
            self._connections.append(websocket)

    def unregister_connection(self, websocket):
        if websocket in self._connections:
            self._connections.remove(websocket)

    async def broadcast_progress(self, task: Task):
        if not self._connections:
            logger.debug("No WebSocket connections available for broadcast")
            return

        message = {
            "type": "task_progress",
            "task_id": task.id,
            "task": task.to_dict()
        }

        logger.debug(f"Broadcasting progress for task {task.id}, status={task.status.value}, connections={len(self._connections)}")

        disconnected = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
                logger.debug(f"Sent progress to websocket connection")
            except Exception as e:
                logger.error(f"Failed to send to websocket: {e}")
                disconnected.append(ws)

        for ws in disconnected:
            self.unregister_connection(ws)

    async def broadcast_stream(self, task_id: str, step: str, content: str):
        if not self._connections:
            return

        message = {
            "type": "llm_stream",
            "task_id": task_id,
            "step": step,
            "content": content
        }

        disconnected = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send stream to websocket: {e}")
                disconnected.append(ws)

        for ws in disconnected:
            self.unregister_connection(ws)

    async def broadcast_custom_message(self, task_id: str, message_type: str, data: Dict[str, Any]):
        if not self._connections:
            return

        message = {
            "type": message_type,
            "task_id": task_id,
            **data
        }

        disconnected = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.error(f"Failed to send custom message to websocket: {e}")
                disconnected.append(ws)

        for ws in disconnected:
            self.unregister_connection(ws)

    def create_task(self, user_message: str, options: Optional[Dict[str, Any]] = None, conversation_id: str = None) -> Task:
        if conversation_id:
            self._cancel_conversation_tasks(conversation_id)

        task = Task(target=user_message, options=options, conversation_id=conversation_id)

        if conversation_id:
            conv = self.get_conversation(conversation_id)
            if conv:
                conv["task_ids"].append(task.id)
                self.add_message_to_conversation(
                    conversation_id,
                    "user",
                    user_message,
                    task_id=task.id
                )

        async def progress_callback(task_obj: Task):
            await self.broadcast_progress(task_obj)

        task.set_progress_callback(progress_callback)
        self.active_tasks[task.id] = task
        self.get_task_lock(task.id)
        logger.info(f"Created task {task.id}")
        return task

    def _cancel_conversation_tasks(self, conversation_id: str):
        conv = self.conversations.get(conversation_id)
        if not conv:
            return
        for task_id in conv.get("task_ids", []):
            task = self.get_task(task_id)
            if task and task.status == TaskStatus.RUNNING:
                logger.info(f"Cancelling previous task {task_id} for conversation {conversation_id}")
                task.cancel()
                self._cleanup_task_resources(task)

    def get_task(self, task_id: str) -> Optional[Task]:
        return self.active_tasks.get(task_id)

    def list_tasks(self) -> List[Task]:
        return list(self.active_tasks.values())

    async def execute_task_async(self, task_id: str) -> Dict[str, Any]:
        task = self.get_task(task_id)
        if not task:
            return {"success": False, "error": "Task not found"}
        
        # 防止任务被重复执行
        if task.status != TaskStatus.PENDING:
            logger.warning(f"Task {task_id} is already {task.status.value}, skipping execution")
            return {"success": False, "error": "Task already started or completed"}

        try:
            task.start()
            await self.broadcast_progress(task)

            storage.create_task(task.conversation_id, task.id, task.target)

            max_iterations = 50
            
            # 如果用户在options中指定了max_iterations，则使用用户指定的值
            if task.options and "max_iterations" in task.options:
                max_iterations = task.options["max_iterations"]

            hybrid_mode = task.options.get("hybrid_mode", True) if task.options else True

            target_info = {
                "user_message": task.target,
                "options": task.options,
                "hybrid_mode": hybrid_mode
            }
            context = {
                "findings": [],
                "last_result": None,
                "executed_commands": [],
                "shell_state": None
            }
            iteration = 0

            terminal = self.get_terminal_for_task(task.id)
            shell_state = await terminal.get_shell_state()
            context["shell_state"] = shell_state

            # 混合模式：AI 主导探索，但保留核心功能
            vulnerabilities = []
            risk_assessment = None

            task.update_progress(1, "开始测试", f"AI正在分析目标... (混合模式: {hybrid_mode})")
            await self.broadcast_progress(task)

            # 检查是否启用人机协作模式
            human_mode = task.human_interaction_mode
            if human_mode:
                task.add_progress_log("人机协作模式已启用：侦查完成后将等待人工决策")
                await self.broadcast_progress(task)

            while iteration < max_iterations:

                logger.info(f"[DEBUG] Iteration {iteration} start, max_iterations={max_iterations}")

                iteration += 1

                logger.info(f"[DEBUG] End of iteration {iteration - 1}, new iteration={iteration}")

                # 人机协作模式：检查是否有待执行的人类命令
                if human_mode and task.human_commands:
                    human_cmd = task.human_commands.pop(0)
                    task.add_progress_log(f"执行人工指令: {human_cmd}")
                    await self.broadcast_progress(task)

                    result = await terminal.scan("", {"command": human_cmd})
                    context["last_result"] = {"command": human_cmd, "result": result}
                    context["executed_commands"].append(human_cmd)

                    task.add_progress_log(f"人工指令执行{'成功' if result.get('success') else '失败'}")

                    # 继续循环，AI可以继续决策
                    continue

                self._check_task_cancelled(task_id, task)

                logger.info(f"Iteration {iteration}: AI deciding next action")

                self._check_task_cancelled(task_id, task)

                decision_str = ""
                try:
                    async for chunk in self.llm_engine.decide_next_action_stream(target_info, context):
                        self._check_task_cancelled(task_id, task)
                        
                        decision_str += chunk
                        await self.broadcast_stream(task.id, "ai_decision", chunk)

                    self._check_task_cancelled(task_id, task)
                except TaskCancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error during LLM stream: {e}")
                    self._check_task_cancelled(task_id, task)
                    continue

                decision = self.llm_engine._parse_decision_response(decision_str)
                logger.info(f"[AI_RESPONSE] Raw decision: {decision_str[:500]}")
                logger.info(f"[AI_RESPONSE] Raw decision:\n{json.dumps(decision, indent=2, ensure_ascii=False)}")

                storage.add_task_message(
                    conversation_id=task.conversation_id,
                    task_id=task.id,
                    msg_type="ai_decision",
                    content=json.dumps(decision, ensure_ascii=False)
                )

                action = decision.get("action", "done")
                reason = decision.get("reason", "")
                new_findings = decision.get("findings", "")
                attack_results = decision.get("attack_results", [])
                next_suggestions = decision.get("next_suggestions", [])

                if action == "done":
                    await self.broadcast_stream(task.id, "ai_decision", "[DONE]")

                if new_findings:
                    finding_list = [f.strip() for f in new_findings.split(";") if f.strip()]
                    for finding in finding_list:
                        if finding and finding not in context["findings"]:
                            context["findings"].append(finding)

                task.add_progress_log(f"AI决策 ({iteration}): {action} - {reason}")
                task.update_progress(1 + iteration, f"执行{action}", reason)
                await self.broadcast_progress(task)

                if next_suggestions:
                    task.add_progress_log(f"【建议】: {'; '.join(next_suggestions[:5])}")

                # 混合模式：AI 专注于探索和决策
                if action == "done":
                    logger.info(f"=== 任务终止: AI主动停止 === reason: {reason}")
                    task.add_progress_log(f"🤖 AI主动停止: {reason}")
                    task.update_progress(4, "探索完成", f"AI主动停止: {reason}")
                    task.attack_results = attack_results
                    task.next_suggestions = next_suggestions
                    storage.update_task_status(task.conversation_id, task.id, "completed")

                    await self.broadcast_progress(task)
                    break

                elif action == "terminal":
                    commands = decision.get("commands", [])
                    single_command = decision.get("command", "").strip()

                    if not commands and single_command:
                        commands = [single_command]

                    if not commands:
                        task.add_progress_log("⚠️ AI返回了空命令列表，跳过")
                        continue

                    fixed_commands = []
                    for cmd in commands:
                        is_valid, fixed_cmd, fix_msg = _check_command_syntax(cmd)
                        if not is_valid:
                            task.add_progress_log(f"⚠️ {fix_msg} | 原: {cmd[:60]}...")
                            context["findings"].append(fix_msg)
                            fixed_commands.append(fixed_cmd)
                        else:
                            fixed_commands.append(cmd)
                    commands = fixed_commands

                    is_parallel = len(commands) > 1

                    if is_parallel:
                        task.add_progress_log(f"顺序执行 {len(commands)} 个命令：{commands[:3]}...")
                        logger.info(f"[WORKFLOW] Executing {len(commands)} commands sequentially: {commands}")
                        await self.broadcast_progress(task)

                        try:
                            logger.info(f"[WORKFLOW] Calling scan_parallel_sync...")
                            results = await terminal.scan_parallel(commands, timeout=600)
                            logger.info(f"[WORKFLOW] scan_parallel_sync returned {len(results)} results")
                        except Exception as e:
                            logger.error(f"[WORKFLOW] scan_parallel_sync failed: {e}")
                            import traceback
                            logger.error(f"[WORKFLOW] Traceback: {traceback.format_exc()}")
                            raise

                        context["last_results"] = [
                            {"command": cmd, "result": res}
                            for cmd, res in zip(commands, results)
                        ]
                        context["executed_commands"].extend(commands)

                        if "terminal" not in task.results:
                            task.results["terminal"] = {}
                        for cmd, result in zip(commands, results):
                            task.results["terminal"][cmd] = result
                            storage.add_task_message(
                                conversation_id=task.conversation_id,
                                task_id=task.id,
                                msg_type="command_output",
                                content=result.get("stdout", "") or result.get("stderr", ""),
                                command=cmd,
                                success=result.get("success", False)
                            )

                        shell_state = await terminal.get_shell_state()
                        context["shell_state"] = shell_state
                        storage.set_task_shell_state(task.conversation_id, task.id, shell_state)

                        success_count = sum(1 for r in results if r.get("success"))
                        task.add_progress_log(f"顺序命令执行完成：{success_count}/{len(commands)} 成功")
                        logger.info(f"[WORKFLOW] Sequential execution completed: {success_count}/{len(commands)} successful")

                        # 人机协作模式：等待人类决策
                        if human_mode:
                            task.pending_findings = context["findings"][:]
                            task.status = TaskStatus.WAITING_DECISION
                            task.decision_requested = True

                            from asyncio import Event as AsyncEvent
                            decision_event = AsyncEvent()
                            task._decision_event = decision_event

                            task.add_progress_log("=" * 50)
                            task.add_progress_log("侦查完成，等待人工决策")
                            task.add_progress_log(f"发现摘要: {'; '.join(context['findings'][-10:])}")
                            task.add_progress_log("请通过 API 提交决策:")
                            task.add_progress_log("POST /api/v1/tasks/{task_id}/human-decision")
                            task.add_progress_log('Body: {"commands": ["sqlmap -u ..."], "action": "continue"}')
                            task.add_progress_log("或: POST /api/v1/tasks/{task_id}/human-decision")
                            task.add_progress_log('Body: {"action": "done"}')
                            task.add_progress_log("=" * 50)
                            await self.broadcast_progress(task)

                            # 通知前端等待决策
                            await self.broadcast_custom_message(task.id, "waiting_decision", {
                                "findings": context["findings"][-10:],
                                "message": "侦查完成，等待人工决策"
                            })

                            try:
                                await asyncio.wait_for(decision_event.wait(), timeout=3600)
                            except asyncio.TimeoutError:
                                task.add_progress_log("等待决策超时，自动继续")
                                task.decision_requested = False
                                task.status = TaskStatus.RUNNING

                            await self.broadcast_progress(task)
                    else:
                        command = commands[0]
                        task.add_progress_log(f"执行终端命令：{command}")
                        await self.broadcast_progress(task)

                        self._check_task_cancelled(task_id, task)

                        # Windows 上使用同步执行方式，更可靠
                        if terminal.is_windows:
                            logger.info(f"[WORKFLOW] Using synchronous execution for Windows")
                            result = await terminal.scan("", {"command": command})
                            logger.info(f"[DEBUG] Command execution done, result keys: {result.keys()}")
                            logger.info(
                                f"[DEBUG] stdout length: {len(result.get('stdout', ''))}, stderr length: {len(result.get('stderr', ''))}")
                            logger.info(f"[DEBUG] scan returned, stdout length: {len(result.get('stdout', ''))}")

                        else:
                            # Linux/Kali 使用流式执行
                            async def output_stream_callback(chunk: str, is_final: bool):
                                await self.broadcast_stream(task.id, "command_output", chunk)

                            result = await terminal.run_command_streaming(
                                command, task.id,
                                output_callback=output_stream_callback,
                                cancel_check_callback=lambda: self.is_task_cancelled(task_id)
                            )
                        
                        self._check_task_cancelled(task_id, task)

                        if result.get("cancelled"):
                            task.add_progress_log("命令已被终止")
                            raise TaskCancelledError(f"Task {task_id} was cancelled")

                        context["last_results"] = [
                            {"command": command, "result": result}
                        ]
                        context["executed_commands"].append(command)

                        if "terminal" not in task.results:
                            task.results["terminal"] = {}
                        task.results["terminal"][command] = result

                        logger.info("[DEBUG] Before add_task_message")
                        storage.add_task_message(
                            conversation_id=task.conversation_id,
                            task_id=task.id,
                            msg_type="command_output",
                            content=result.get("stdout", "") or result.get("stderr", ""),
                            command=command,
                            success=result.get("success", False)
                        )
                        logger.info("[DEBUG] After add_task_message")

                        logger.info("[DEBUG] Before get_shell_state")
                        shell_state = await terminal.get_shell_state()
                        logger.info("[DEBUG] After get_shell_state")

                        context["shell_state"] = shell_state

                        logger.info("[DEBUG] Before set_task_shell_state")
                        storage.set_task_shell_state(task.conversation_id, task.id, shell_state)
                        logger.info("[DEBUG] After set_task_shell_state")

                        stdout_preview = result.get("stdout", "")[:200].replace("\n", "\\n")
                        stderr_preview = result.get("stderr", "")[:200].replace("\n", "\\n")
                        task.add_progress_log(f"命令执行{'成功' if result.get('success') else '失败'}")
                        task.add_progress_log(f"[DEBUG] stdout: {stdout_preview}")
                        if result.get("stderr"):
                            task.add_progress_log(f"[DEBUG] stderr: {stderr_preview}")

                    logger.info("[DEBUG] Before broadcast_progress")
                    await self.broadcast_progress(task)
                    logger.info("[DEBUG] After broadcast_progress")

                elif action == "continue":
                    # AI 决定继续探索，不做特殊处理
                    task.add_progress_log("继续探索...")

                    continue

                else:
                    logger.warning(f"Unknown action type: {action}")
                    if not hybrid_mode:
                        logger.info("=== 任务终止: 代码强制停止 === reason: 未知action类型且非混合模式")
                        task.add_progress_log(f"🔒 代码强制停止: 未知action '{action}'")
                        break

            task_completed_normally = iteration >= max_iterations

            self._check_task_cancelled(task_id, task)

            # 自动生成报告
            if not task.llm_analysis_raw:
                task.update_progress(7, "生成报告", "正在生成最终分析...")
                await self.broadcast_progress(task)
                logger.info("Auto-triggering report generation")

                combined_results = {
                    "target": task.target,
                    "executed_commands": context["executed_commands"],
                    "findings": context["findings"]
                }

                llm_analysis_str = ""
                async for chunk in self.llm_engine.analyze_results_stream(combined_results):
                    self._check_task_cancelled(task_id, task)
                    
                    llm_analysis_str += chunk
                    await self.broadcast_stream(task.id, "analyze_results", chunk)
                
                await self.broadcast_stream(task.id, "analyze_results", "[DONE]")
                
                self._check_task_cancelled(task_id, task)
                task.llm_analysis_raw = llm_analysis_str
                storage.set_task_final_report(task.conversation_id, task.id, llm_analysis_str)

            task.complete()
            storage.update_task_status(task.conversation_id, task.id, "completed")
            self._cleanup_task_resources(task)
            await self.broadcast_progress(task)

            if task_completed_normally:
                logger.info(f"=== 任务终止: 代码强制停止 === reason: 达到最大迭代次数 {max_iterations}")
                task.add_progress_log(f"🔒 代码强制停止: 达到最大迭代次数 {max_iterations}")

            return {
                "success": True,
                "task_id": task.id,
                "target": task.target,
                "vulnerabilities": vulnerabilities,
                "risk_assessment": risk_assessment,
                "llm_analysis_raw": task.llm_analysis_raw,
                "attack_results": getattr(task, 'attack_results', []),
                "next_suggestions": getattr(task, 'next_suggestions', []),
                "duration": task.get_duration(),
                "hybrid_mode": hybrid_mode
            }

        except TaskCancelledError as e:
            logger.info(f"Task {task_id} was cancelled: {e}")
            task.cancel()
            storage.update_task_status(task.conversation_id, task.id, "cancelled")
            self._cleanup_task_resources(task)
            await self.broadcast_progress(task)
            return {"success": False, "error": "Task cancelled", "task_id": task_id}
        except Exception as e:
            task.fail(str(e))
            storage.update_task_status(task.conversation_id, task.id, "failed")
            self._cleanup_task_resources(task)
            await self.broadcast_progress(task)
            logger.error(f"Task execution failed: {e}")
            return {"success": False, "error": str(e), "task_id": task_id}

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        lock = self.get_task_lock(task_id)
        with lock:
            task = self.get_task(task_id)
            if not task:
                return {"success": False, "error": "Task not found"}

            if task.status == TaskStatus.RUNNING:
                task.cancel()
                storage.update_task_status(task.conversation_id, task.id, "cancelled")
                terminal = self.get_terminal_for_task(task_id)
                terminal.terminate_process(task_id)
                self._cleanup_task_resources(task)
                return {"success": True, "message": "Task cancelled"}

            return {"success": False, "error": "Task is not running"}
