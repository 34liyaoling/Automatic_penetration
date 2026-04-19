import json
import logging
import re
import platform
import asyncio
import os
from datetime import datetime
from typing import Dict, List, Any, AsyncGenerator

from src.config.settings import settings

logger = logging.getLogger(__name__)

LLM_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "llm_logs")
os.makedirs(LLM_LOG_DIR, exist_ok=True)

_llm_call_counter = 0


class LLMEngine:
    def __init__(self, provider=None):
        self.provider = provider or settings.DEFAULT_LLM_PROVIDER
        self.config = settings.get_llm_config(self.provider) or {}
        self.client = self._init_client()

    def switch_provider(self, provider):
        self.provider = provider
        self.config = settings.get_llm_config(self.provider) or {}
        self.client = self._init_client()
        logger.info(f"Switched to LLM provider: {self.provider}")

    def get_available_providers(self):
        return list(settings.LLM_PROVIDERS.keys())

    def get_provider_models(self, provider=None):
        provider = provider or self.provider
        if provider not in settings.LLM_PROVIDERS:
            return []
        return settings.LLM_PROVIDERS[provider]["models"]

    def _init_client(self):
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(
                api_key=self.config.get("api_key"),
                base_url=self.config.get("base_url"),
                timeout=30
            )
            return client
        except Exception as e:
            logger.warning(f"LLM client initialization failed: {e}, using mock mode")
            return None

    def _build_decision_prompt(self, target_info: Dict[str, Any], context: Dict[str, Any]) -> tuple:
        """返回 (system_prompt, user_prompt) 元组"""
        is_windows = platform.system().lower() == "windows"
        env = "Windows" if is_windows else "Kali Linux"

        system_prompt = f"""你是一个高级渗透测试引擎{env}。你的核心任务是：根据用户提供的核心任务、历史命令和状态追踪（findings），自主规划并输出下一步操作。"""+"""

请遵循以下**核心策略**：

1. **状态追踪**：你必须完全依赖findings中的信息来决策。建议每条 findings 以 success=true/false 开头，以便快速判断操作结果；其余内容自由描述。如果省略 success，AI 需从描述中推断成功与否，但可能影响准确性。

2. **避免重复**：在执行任何操作前，检查`findings`。如果操作的目标（如URL）已成功（已有`success=true`且文件存在），则不得重复请求相同URL。对于静态页面，一旦下载成功，后续应直接分析本地文件（使用`Get-Content`或`Select-String`），而不是再次下载。

3. **处理失败**：对于关键数据（如`token`）提取，若连续2次`findings`中仍无具体值（或明确写“未找到”），必须切换策略（如改用不同正则、直接使用已知默认值、或放弃登录），禁止无限重试。

4. **遵守命令规范**：Windows下复杂命令必须使用`powershell -Command "..."`。Web登录场景中，必须遵循“GET页面 -> 提取token -> POST登录”的流程。登录后必须验证会话是否有效（如访问需要认证的页面，检查是否包含登录成功特征）。

5. **终止条件**：当目标不可达、连续3次无有效进展（如`findings`中只有“未找到”或“无输出”）、用户核心任务已完成或无法完成时，输出`{"action": "done", "reason": "原因", "findings": "最终状态"}`。

**输出要求**：
你**必须且只能**输出一个合法的JSON对象，格式如下：
- **继续执行**：`{"action": "terminal", "commands": ["具体命令"], "reason": "简洁的行动理由", "findings": "success=true; 其他自由描述"}`
- **任务终止**：`{"action": "done", "reason": "终止原因", "findings": "最终状态总结"}`

**注意**：`findings`中的`success`是必须的，其余内容你可以自由组织，确保对下一步决策有帮助即可。"""

        # ========== USER PROMPT ==========
        findings = context.get("findings", [])
        executed = context.get("executed_commands", [])
        last_result = context.get("last_result")
        last_results = context.get("last_results", [])

        context_parts = []
        if findings:
            numbered_findings = "\n".join(f"{i+1}. {f}" for i, f in enumerate(findings))
            context_parts.append(numbered_findings)

        all_results = []
        if last_result:
            all_results.append(last_result)
        if last_results:
            all_results.extend(last_results)

        raw_outputs_list = []
        for res in all_results:
            cmd = res.get("command", "")[:80]
            result_data = res.get("result", {})
            stdout = result_data.get("stdout", "")
            stderr = result_data.get("stderr", "")
            cwd = result_data.get("cwd", "")

            error_part = stderr.strip() if stderr.strip() else ""
            output_part = stdout.strip() if stdout.strip() else ""
            if error_part:
                # 有错误信息，优先显示错误
                result_preview = f"[错误输出]\n{error_part}"
                if output_part:
                    result_preview += f"\n[标准输出]\n{output_part}"
            elif output_part:
                result_preview = f"[标准输出]\n{output_part}"
            else:
                result_preview = "[无输出]"

            cwd_info = f" [目录: {cwd}]" if cwd else ""

            raw_outputs_list.append(f"命令: {cmd}{cwd_info}\n输出:\n{result_preview}")

        if raw_outputs_list:
            raw_outputs = "\n\n".join(raw_outputs_list)
        else:
            raw_outputs = "无"

        executed_str = "\n".join([f"{i + 1}. {cmd}" for i, cmd in enumerate(executed)]) if executed else "无"

        user_message = target_info.get('user_message') or target_info.get('target')

        user_prompt = f"""【用户任务】
{user_message}

【本次命令输出结果】

{raw_outputs}

【历史命令】
{executed_str}

【历史完成的事件】
{"".join(context_parts)}

"""

        return system_prompt, user_prompt


    def _build_result_analysis_prompt(self, scan_results: Dict[str, Any]) -> str:
        return f"""
首先分析以下数据，用自然语言告诉用户发现了什么：
{json.dumps(scan_results, indent=2, ensure_ascii=False)},

"""

    async def analyze_results_stream(self, scan_results: Dict[str, Any]) -> AsyncGenerator[str, None]:
        prompt = self._build_result_analysis_prompt(scan_results)
        async for chunk in self._call_llm_stream(None, prompt, context={"scan_results": scan_results}):
            yield chunk

    async def _call_llm_stream(self, system_prompt: str, user_prompt: str = None, context: Dict[str, Any] = None, target_info: Dict[str, Any] = None) -> AsyncGenerator[str, None]:
        global _llm_call_counter
        _llm_call_counter += 1
        
        if user_prompt is None:
            user_prompt = system_prompt
            system_prompt = None

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})
        
        log_file = os.path.join(LLM_LOG_DIR, f"sk.txt")
        
        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"=== LLM Call #{_llm_call_counter} ===\n")
                f.write(f"Timestamp: {datetime.now().isoformat()}\n\n")
                if target_info:
                    f.write("=== TARGET INFO ===\n")
                    f.write(json.dumps(target_info, indent=2, ensure_ascii=False))
                    f.write("\n\n")
                if context:
                    f.write("=== FULL CONTEXT ===\n")
                    f.write(json.dumps(context, indent=2, ensure_ascii=False))
                    f.write("\n\n")
                f.write("=== SYSTEM PROMPT ===\n")
                f.write(system_prompt if system_prompt else "None")
                f.write("\n\n=== USER PROMPT ===\n")
                f.write(user_prompt)
                f.write("\n\n=== FULL MESSAGES ===\n")
                f.write(json.dumps(messages, indent=2, ensure_ascii=False))
                f.write("\n")
            logger.info(f"LLM input saved to: {log_file}")
        except Exception as e:
            logger.error(f"Failed to save LLM log: {e}")

        if self.client is None:
            response = self._mock_response(user_prompt)
            for char in response:
                yield char
                await asyncio.sleep(0.02)
            return

        full_reasoning = []  # 累积思考内容
        full_content = []

        try:
            stream = await self.client.chat.completions.create(
                model=self.config.get("model"),
                messages=messages,
                temperature=self.config.get("temperature", 0.7),
                max_tokens=self.config.get("max_tokens", 16000),
                stream=True,
                timeout=180
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    reasoning_piece = delta.reasoning_content
                    full_reasoning.append(reasoning_piece)
                    logger.debug(f"Reasoning piece: {reasoning_piece[:50]}...")
                if delta.content:
                    full_content.append(delta.content)
                    yield delta.content
            # 循环结束后，保存完整的思考过程
            if full_reasoning:
                reasoning_text = ''.join(full_reasoning)
                logger.info(f"Full reasoning for call #{_llm_call_counter}:\n{reasoning_text}")
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"\n\n=== REASONING CONTENT ===\n{reasoning_text}\n")
        except Exception as e:
            logger.error(f"LLM stream call failed: {e}")
            for char in self._mock_response(user_prompt):
                yield char

    async def decide_next_action_stream(self, target_info: Dict[str, Any], context: Dict[str, Any]) -> AsyncGenerator[str, None]:
        system_prompt, user_prompt = self._build_decision_prompt(target_info, context)
        async for chunk in self._call_llm_stream(system_prompt, user_prompt, context=context, target_info=target_info):
            yield chunk

    def _parse_decision_response(self, response: str) -> Dict[str, Any]:
        try:
            txt = response.strip()
            logger.info(f"[PARSE_JSON] Raw response length: {len(txt)}")
            logger.info(f"[PARSE_JSON] Raw response preview: {txt[:200]}...")
            
            # 移除 markdown 代码块标记
            txt = re.sub(r'```json\s*', '', txt)
            txt = re.sub(r'```\s*', '', txt)
            
            # 提取 JSON 对象
            start = txt.find('{')
            end = txt.rfind('}')
            if start != -1 and end != -1 and end > start:
                txt = txt[start:end + 1]
            else:
                logger.error(f"[PARSE_JSON] Cannot find JSON object in response")
                return {"action": "done", "reason": "模型输出格式错误，无法找到 JSON 对象"}
            
            # 规范化空白字符
            txt = re.sub(r'\n', ' ', txt)
            txt = re.sub(r'\s+', ' ', txt)

            txt = re.sub(r'\\(?![\\"/bfnrtu])', r'\\\\', txt)

            logger.info(f"[PARSE_JSON] Cleaned JSON: {txt[:200]}...")
            
            # 解析 JSON
            result = json.loads(txt)
            
            # 验证 commands 字段
            if result.get("action") == "terminal":
                commands = result.get("commands", [])
                if not commands and not result.get("command"):
                    logger.warning(f"[PARSE_JSON] AI returned terminal action but no commands!")
                else:
                    logger.info(f"[PARSE_JSON] Parsed {len(commands)} commands: {commands[:3]}...")
            
            return result
        except json.JSONDecodeError as e:
            logger.error(f"[PARSE_JSON] JSON decode error: {str(e)}")
            # 新增修复尝试
            try:
                from json_repair import repair_json
                repaired = repair_json(txt)
                result = json.loads(repaired)
                logger.info("[PARSE_JSON] Successfully repaired and parsed JSON")
                return result
            except Exception as repair_err:
                logger.error(f"[PARSE_JSON] Repair failed: {repair_err}")
                return {"action": "done", "reason": f"JSON 解析失败: {str(e)}"}
        except Exception as e:
            logger.error(f"解析失败：{str(e)} | 原始内容：{response[:200]}")
            return {"action": "done", "reason": "模型输出解析失败，已停止"}

    def _mock_response(self, prompt: str) -> str:
        is_windows = platform.system().lower() == "windows"
        cmd = "ping -n 2 127.0.0.1" if is_windows else "ping -c 2 127.0.0.1"

        if "【已执行】" in prompt and len(prompt) > 150:
            return json.dumps({
                "action": "done",
                "commands": [],
                "reason": "已收集足够信息，结束测试",
                "findings": "目标基础信息收集完成"
            }, ensure_ascii=False)

        return json.dumps({
            "action": "terminal",
            "commands": [cmd],
            "reason": "测试网络连通性",
            "findings": "本地网络连通正常"
        }, ensure_ascii=False)
