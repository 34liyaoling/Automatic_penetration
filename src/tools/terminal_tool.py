import subprocess
import logging
import platform
import re
import asyncio
import os
import sys
import uuid
import shlex
from typing import Dict, Any, Optional, List, Tuple, AsyncGenerator
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class ShellState:
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    last_output: str = ""
    history: List[str] = field(default_factory=list)

class PersistentShell:
    def __init__(self, task_id: str = None, is_windows: bool = False):
        self.task_id = task_id
        self.is_windows = is_windows
        self.process = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.state = ShellState()
        self._lock = asyncio.Lock()
        self._output_buffer = ""
        self._read_task = None
        self._closed = False
        self.default_idle_timeout = 30

        base_dir = "/tmp/pent_workspace" if not is_windows else os.path.join(os.environ.get("TEMP", ""), "D:/pent_workspace")
        self.workspace_dir = os.path.join(base_dir, f"task_{task_id}" if task_id else f"default_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.workspace_dir, exist_ok=True)
        self.state.cwd = self.workspace_dir

        env = os.environ.copy()
        env["WORKSPACE"] = self.workspace_dir
        self.state.env = env

    def _clean_output(self, output: str) -> str:
        """清理输出，移除 Windows 欢迎信息和命令提示符，但保留有效内容"""
        lines = output.split('\n')
        cleaned = []
        skip_next_prompt = False
        
        for line in lines:
            line_stripped = line.strip()
            
            # 跳过 Windows 欢迎信息
            if line_stripped.startswith("Microsoft Windows") or line_stripped.startswith("(c) Microsoft"):
                skip_next_prompt = True
                continue
            
            # 跳过欢迎信息后的第一个提示符
            if skip_next_prompt and (line_stripped.startswith("C:\\") or line_stripped.endswith(">")):
                skip_next_prompt = False
                continue
            
            # 跳过纯命令提示符行（如 C:\Users\test>）
            if re.match(r'^[A-Z]:\\[^>]*>$', line_stripped):
                continue
            
            # 保留所有其他行（包括空行，如果前后都有内容）
            cleaned.append(line)
        
        # 只移除首尾的空白行，保留中间的格式
        result = '\n'.join(cleaned)
        return result.strip('\n') if result else ''

    async def _kill_subprocess(self):
        if self.process and self.process.poll() is None:
            logger.info(f"Killing subprocess PID {self.process.pid}")
            try:
                self.process.terminate()
                await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, self.process.wait), timeout=2)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass

    async def start(self):
        if self.is_windows:
            await self._start_windows_shell()
        else:
            await self._start_unix_shell()

    async def _start_unix_shell(self):
        try:
            import pty
            master, slave = pty.openpty()

            self.process = subprocess.Popen(
                ["bash", "--norc", "--noprofile"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=self.workspace_dir,
                env=self.state.env,
                preexec_fn=os.setsid
            )

            os.close(slave)
            self.stdin = os.fdopen(master, 'w')
            self.stdout = os.fdopen(master, 'r')

            self._read_task = asyncio.create_task(self._read_output())

            logger.info(f"Unix persistent shell started for task {self.task_id}, workspace: {self.workspace_dir}")
        except Exception as e:
            logger.error(f"Failed to start Unix shell: {e}")
            raise

    async def _start_windows_shell(self):
        try:
            # 使用标准 cmd.exe 参数，确保兼容性
            # /Q: 关闭回显
            # /V:OFF: 禁用延迟变量展开
            # /F:OFF: 禁用文件名完成
            self.process = subprocess.Popen(
                ["cmd.exe", "/Q", "/V:ON", "/F:OFF"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.workspace_dir,
                env=self.state.env,
                text=False,
            )

            self.stdin = self.process.stdin
            self.stdout = self.process.stdout
            self.stderr = self.process.stderr
            self._read_task = asyncio.create_task(self._read_output_windows())
            
            # 短暂等待 shell 启动
            await asyncio.sleep(0.1)
            
            # 写入一个回车符，确保 shell 准备好
            try:
                self.stdin.write(b'\r\n')
                self.stdin.flush()
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.debug(f"[_SHELL_INIT] Initialization write failed: {e}")

            logger.info(f"Windows persistent shell started for task {self.task_id}, workspace: {self.workspace_dir}")
        except Exception as e:
            logger.error(f"Failed to start Windows shell: {e}")
            raise

    async def _read_output(self):
        try:
            loop = asyncio.get_event_loop()
            while not self._closed:
                try:
                    chunk = await loop.run_in_executor(None, self.stdout.read, 8192)
                    if not chunk:
                        await asyncio.sleep(0.01)
                        continue
                    # Windows cmd.exe 输出使用 GBK 编码（中文系统），其他系统使用 UTF-8
                    if self.is_windows:
                        try:
                            decoded = chunk.decode('gbk')
                        except UnicodeDecodeError:
                            decoded = chunk.decode('utf-8', errors='replace')
                    else:
                        decoded = chunk.decode('utf-8', errors='replace')
                    self._output_buffer += decoded
                except Exception as e:
                    if not self._closed:
                        logger.error(f"Shell read error: {e}")
                    break
        except Exception as e:
            if not self._closed:
                logger.error(f"Shell read task error: {e}")

    async def _read_output_windows(self):
        loop = asyncio.get_event_loop()
        while not self._closed and self.process and self.process.poll() is None:
            try:
                line_bytes = await loop.run_in_executor(None, self.stdout.readline)
                if not line_bytes:
                    break
                # 手动解码：优先 GBK，失败则 UTF-8 并替换错误字符
                try:
                    line = line_bytes.decode('gbk', errors='replace')
                except UnicodeDecodeError:
                    line = line_bytes.decode('utf-8', errors='replace')
                self._output_buffer += line
                logger.info(f"[RAW] {line.rstrip()}")
            except Exception as e:
                if not self._closed:
                    logger.error(f"Read error: {e}")
                break

    async def execute(self, command: str, timeout: int = 600) -> Dict[str, Any]:

        if not self.stdin or (self.process and self.process.poll() is not None):
            logger.info(
                f"[EXECUTE] Shell not ready, starting... stdin={self.stdin is not None}, process_poll={self.process.poll() if self.process else 'None'}")
            await self.start()
            logger.info(f"[EXECUTE] Shell started successfully")

        async with self._lock:
            self._output_buffer = ""

            if self.is_windows:
                # ========== Windows 分支：使用临时文件分离 stdout/stderr ==========
                escaped_command = command
                stdout_file = os.path.join(self.workspace_dir, f"stdout_{uuid.uuid4().hex[:8]}.txt")
                stderr_file = os.path.join(self.workspace_dir, f"stderr_{uuid.uuid4().hex[:8]}.txt")
                exitcode_file = os.path.join(self.workspace_dir, f"exitcode_{uuid.uuid4().hex[:8]}.txt")
                prompt = (
                    f'cd /d {self.state.cwd} & '
                    f'( {escaped_command} > "{stdout_file}" 2> "{stderr_file}" ) & '
                    f'echo !ERRORLEVEL! > "{exitcode_file}" & '
                    f'echo __END__\r\n'
                )

                logger.info(f"[EXECUTE] Windows prompt: {prompt}")
                logger.info(f"[EXECUTE] Writing command to shell: {command[:80]}...")
                try:
                    self.stdin.write(prompt.encode('gbk', errors='replace'))
                    self.stdin.flush()
                    logger.info(f"[EXECUTE] Command written successfully, waiting for output...")
                except Exception as e:
                    logger.error(f"[EXECUTE] Failed to write command: {e}")
                    raise

                logger.info(f"[EXECUTE] Waiting for output with timeout={timeout}s")
                _, success = await self._wait_for_output(timeout)  # 只需要 success，不需要 output

                # 读取临时文件内容
                stdout_content = ""
                stderr_content = ""
                # 读取 stdout（GBK优先）
                try:
                    if os.path.exists(stdout_file):
                        with open(stdout_file, 'rb') as f:
                            raw = f.read()
                            try:
                                stdout_content = raw.decode('utf-8', errors='strict')  # 先尝试 UTF-8
                            except UnicodeDecodeError:
                                stdout_content = raw.decode('gbk', errors='replace')
                        os.remove(stdout_file)
                except Exception as e:
                    logger.error(f"Failed to read stdout file: {e}")

                exitcode = 1
                try:
                    if os.path.exists(exitcode_file):
                        with open(exitcode_file, 'r', encoding='utf-8') as f:
                            exitcode = int(f.read().strip())
                        os.remove(exitcode_file)
                        logger.info(f"[DEBUG] Exitcode read: {exitcode}")  # 添加这行
                except Exception as e:
                    logger.error(f"Failed to read exitcode: {e}")

                # 读取 stderr（GBK优先）
                try:
                    if os.path.exists(stderr_file):
                        with open(stderr_file, 'rb') as f:
                            raw = f.read()
                            try:
                                stderr_content = raw.decode('utf-8', errors='strict')
                            except UnicodeDecodeError:
                                stderr_content = raw.decode('gbk', errors='replace')
                        os.remove(stderr_file)
                except Exception as e:
                    logger.error(f"Failed to read stderr file: {e}")

                # 处理 cd 命令（更新工作目录）
                if "cd " in command:
                    cd_match = re.search(r'cd\s+(?:/d\s+)?(.+)', command, re.IGNORECASE)
                    if cd_match:
                        new_cwd = cd_match.group(1).strip()
                        if new_cwd.startswith('"') and new_cwd.endswith('"'):
                            new_cwd = new_cwd[1:-1]
                        if not os.path.isabs(new_cwd):
                            new_cwd = os.path.normpath(os.path.join(self.state.cwd, new_cwd))
                        if os.path.isdir(new_cwd):
                            self.state.cwd = new_cwd
                            logger.info(f"Updated cwd to: {self.state.cwd}")
                        else:
                            logger.warning(f"cd target does not exist: {new_cwd}")

                self.state.last_output = stdout_content  # 记录输出内容
                self.state.history.append(command)

                final_success = success and (exitcode == 0)

                return {
                    "success": final_success,
                    "stdout": stdout_content.strip(),
                    "stderr": stderr_content.strip(),
                    "cwd": self.state.cwd,
                    "command": command,
                    "workspace": self.workspace_dir
                }

            else:
                # ========== Linux 分支：保持原有逻辑（从管道读取） ==========
                prompt = f"cd {shlex.quote(self.state.cwd)} & {command} & echo __END__\n"
                logger.info(f"[EXECUTE] Writing command to shell: {command[:80]}...")
                try:
                    self.stdin.write(prompt)
                    self.stdin.flush()
                    logger.info(f"[EXECUTE] Command written successfully, waiting for output...")
                except Exception as e:
                    logger.error(f"[EXECUTE] Failed to write command: {e}")
                    raise

                logger.info(f"[EXECUTE] Waiting for output with timeout={timeout}s")
                output, success = await self._wait_for_output(timeout)

                # 处理 cd 命令（更新工作目录）
                if "cd " in command:
                    cd_match = re.search(r'cd\s+(?:/d\s+)?(.+)', command, re.IGNORECASE)
                    if cd_match:
                        new_cwd = cd_match.group(1).strip()
                        if new_cwd.startswith('"') and new_cwd.endswith('"'):
                            new_cwd = new_cwd[1:-1]
                        if not os.path.isabs(new_cwd):
                            new_cwd = os.path.normpath(os.path.join(self.state.cwd, new_cwd))
                        if os.path.isdir(new_cwd):
                            self.state.cwd = new_cwd
                            logger.info(f"Updated cwd to: {self.state.cwd}")
                        else:
                            logger.warning(f"cd target does not exist: {new_cwd}")

                self.state.last_output = output
                self.state.history.append(command)

                return {
                    "success": success,
                    "stdout": output.strip(),
                    "stderr": "",  # Linux 暂不分离 stderr
                    "cwd": self.state.cwd,
                    "command": command,
                    "workspace": self.workspace_dir
                }

    async def _wait_for_output(self, timeout: int) -> Tuple[str, bool]:
        loop = asyncio.get_event_loop()
        start = loop.time()
        output = ""
        last_buffer_len = 0
        
        logger.debug(f"[_WAIT_FOR_OUTPUT] Starting wait, timeout={timeout}s")
        
        while (loop.time() - start) < timeout:
            if "__END__" in self._output_buffer:
                idx = self._output_buffer.find("__END__")
                output = self._output_buffer[:idx]
                self._output_buffer = self._output_buffer[idx + 7:]
                logger.debug(f"[_WAIT_FOR_OUTPUT] Found __END__, got {len(output)} bytes")
                return output, True
            
            # 记录输出缓冲区的变化
            current_buffer_len = len(self._output_buffer)
            if current_buffer_len != last_buffer_len:
                logger.debug(f"[_WAIT_FOR_OUTPUT] Buffer changed: {last_buffer_len} -> {current_buffer_len} bytes")
                last_buffer_len = current_buffer_len
            
            await asyncio.sleep(0.05)
        
        # 总超时，记录详细原因
        logger.warning(f"[_WAIT_FOR_OUTPUT] Timeout after {timeout}s, buffer has {len(self._output_buffer)} bytes")
        output = self._output_buffer
        self._output_buffer = ""
        return output, False

    async def execute_streaming(self, command: str, output_callback, timeout: int = 600, idle_timeout: Optional[int] = None):
        if idle_timeout is None:
            idle_timeout = self.default_idle_timeout

        if not self.stdin or (self.process and self.process.poll() is not None):
            logger.info(f"[EXECUTE_STREAM] Shell not ready, starting...")
            await self.start()
            logger.info(f"[EXECUTE_STREAM] Shell started successfully")

        async with self._lock:
            self._output_buffer = ""

            if self.is_windows:
                # Windows cmd.exe 需要 \r\n 换行符，且不需要 shlex.quote
                prompt = f"cd /d {self.state.cwd} & {command} & echo __END__\r\n"
            else:
                prompt = f"cd {shlex.quote(self.state.cwd)} & {command} & echo __END__\n"
            
            logger.info(f"[EXECUTE_STREAM] Writing command: {command[:80]}...")
            self.stdin.write(prompt.encode('gbk', errors='replace'))
            self.stdin.flush()
            logger.info(f"[EXECUTE_STREAM] Command written, waiting for output...")

            loop = asyncio.get_event_loop()
            start = loop.time()
            last_output_len = 0
            last_change_time = start
            output = ""

            while (loop.time() - start) < timeout:
                if self.process and self.process.poll() is not None:
                    logger.warning("[DEBUG-STREAM] Subprocess died")
                    output = self._output_buffer
                    self._output_buffer = ""
                    # 进程意外死亡，详细记录原因
                    yield f"[PROCESS_DIED] 子进程意外终止，PID={self.process.pid}。"
                    break

                current_len = len(self._output_buffer)
                if current_len != last_output_len:
                    logger.debug(f"[EXECUTE_STREAM] Buffer changed: {last_output_len} -> {current_len} bytes")
                    last_output_len = current_len
                    last_change_time = loop.time()
                else:
                    if idle_timeout and (loop.time() - last_change_time) > idle_timeout:
                        logger.warning(f"[DEBUG-STREAM] No output for {idle_timeout}s, killing process")
                        await self._kill_subprocess()
                        # 详细记录超时原因，帮助 AI 判断
                        yield f"[IDLE_TIMEOUT] 命令执行超时：{idle_timeout}秒内无任何输出。"
                        break

                if "__END__" in self._output_buffer:
                    idx = self._output_buffer.find("__END__")
                    output = self._output_buffer[:idx]
                    self._output_buffer = self._output_buffer[idx + 7:]
                    logger.info(f"[DEBUG-STREAM] Got response, length={len(output)}")
                    logger.info(f"[DEBUG-STREAM] Output preview: {output[:100] if output else '(empty)'}")
                    yield self._clean_output(output)
                    break

                # 只在有持续输出时才 incremental yield，避免清空缓冲区导致__END__检测失败
                # 如果缓冲区有内容且持续增长，先不 yield，等待__END__标记
                current_time = loop.time()
                if self._output_buffer and (current_time - last_change_time) > 0.3:
                    # 超过 0.3 秒没有新输出，可能是单行输出，先 yield
                    output_chunk = self._output_buffer
                    self._output_buffer = ""
                    logger.debug(f"[DEBUG-STREAM] Incremental output: {len(output_chunk)} bytes")
                    yield self._clean_output(output_chunk)

                await asyncio.sleep(0.05)  # 更短的等待间隔
            
            # 如果还有剩余输出，yield 出来
            if self._output_buffer:
                logger.debug(f"[DEBUG-STREAM] Final output: {len(self._output_buffer)} bytes")
                yield self._clean_output(self._output_buffer)
                self._output_buffer = ""
            
            logger.info(f"[EXECUTE_STREAM] Streaming completed, output_length={len(output)}")

            if "cd " in command:
                cd_match = re.search(r'cd\s+(?:/d\s+)?(.+)', command, re.IGNORECASE)
                if cd_match:
                    new_cwd = cd_match.group(1).strip("'\"")
                    if os.path.isabs(new_cwd):
                        self.state.cwd = new_cwd

            self.state.last_output = output if 'output' in dir() else ""
            self.state.history.append(command)

    def get_state(self) -> ShellState:
        return self.state

    def set_env(self, key: str, value: str):
        self.state.env[key] = value
        if self.process and self.process.poll() is None:
            try:
                self.stdin.write(f"export {key}={shlex.quote(value)}\n")
                self.stdin.flush()
            except Exception as e:
                logger.error(f"Failed to set env: {e}")

    async def close(self):
        self._closed = True
        if self._read_task:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass

        if self.process:
            try:
                self.process.terminate()
                await asyncio.wait_for(asyncio.get_event_loop().run_in_executor(None, self.process.wait), timeout=5)
            except Exception as e:
                logger.error(f"Failed to terminate shell: {e}")

        if self.stdin:
            try:
                self.stdin.close()
            except Exception:
                pass

        logger.info(f"Persistent shell closed for task {self.task_id}")


class TerminalTool:
    def __init__(self, task_id: str = None):
        self.is_windows = platform.system().lower() == "windows"
        self.is_linux = platform.system().lower() == "linux"
        self.is_kali = self._check_kali()
        self._running_processes: Dict[str, Any] = {}
        self._process_lock = asyncio.Lock()

        self.task_id = task_id
        cookie_name = f"cookies_{task_id}.txt" if task_id else "cookies.txt"
        self.cookie_dir = "/tmp/pent_cookies" if not self.is_windows else os.path.join(os.environ.get("TEMP", ""), "pent_cookies")
        os.makedirs(self.cookie_dir, exist_ok=True)
        self.cookie_file = os.path.join(self.cookie_dir, cookie_name)

        base_workspace = "/tmp/pent_workspace" if not self.is_windows else os.path.join(os.environ.get("TEMP", ""), "pent_workspace")
        self.workspace_dir = os.path.join(base_workspace, f"task_{task_id}" if task_id else f"default_{uuid.uuid4().hex[:8]}")
        os.makedirs(self.workspace_dir, exist_ok=True)

        self._persistent_shell: Optional[PersistentShell] = None
        self._shell_lock = asyncio.Lock()

        self.WHITELIST_COMMANDS = {
            "windows": [],
            "linux": [],
            "kali": []
        }

        self.DANGEROUS_PATTERNS = []

    def _check_kali(self) -> bool:
        try:
            with open("/etc/os-release", "r") as f:
                content = f.read().lower()
                return "kali" in content
        except:
            return False

    async def _get_persistent_shell(self) -> PersistentShell:
        async with self._shell_lock:
            if self._persistent_shell is None:
                self._persistent_shell = PersistentShell(
                    task_id=self.task_id,
                    is_windows=self.is_windows
                )
                await self._persistent_shell.start()
            return self._persistent_shell

    def _ensure_cookie_file(self):
        if not os.path.exists(self.cookie_file):
            open(self.cookie_file, 'w').close()

    def cleanup_cookies(self):
        if os.path.exists(self.cookie_file):
            os.remove(self.cookie_file)

    def cleanup_workspace(self):
        if os.path.exists(self.workspace_dir):
            import shutil
            try:
                shutil.rmtree(self.workspace_dir)
                logger.info(f"Cleaned up workspace: {self.workspace_dir}")
            except Exception as e:
                logger.error(f"Failed to cleanup workspace: {e}")

    def _run_powershell_script(self, command: str, timeout: int) -> Dict[str, Any]:
        """同步执行 PowerShell 命令（通过临时脚本文件），返回与 execute 兼容的字典"""
        # 提取 -Command 后面的真正命令
        if command.lower().startswith('powershell'):
            # 去掉 "powershell -Command " 前缀
            inner = command[len('powershell -Command '):].strip()
            # 去掉可能的外层双引号
            if inner.startswith('"') and inner.endswith('"'):
                inner = inner[1:-1]
        else:
            inner = command

        workspace = self.workspace_dir
        os.makedirs(workspace, exist_ok=True)

        script_path = os.path.join(workspace, f"tmp_{uuid.uuid4().hex}.ps1")
        try:
            with open(script_path, 'w', encoding='utf-8') as f:
                f.write(inner)

            proc = subprocess.run(
                ['powershell', '-ExecutionPolicy', 'Bypass', '-File', script_path],
                capture_output=True, text=False, timeout=timeout,
                cwd=workspace
            )

            # 智能解码 stdout
            stdout = ""
            if proc.stdout:
                logger.info(f"[PS_DEBUG] stdout bytes (first 20): {proc.stdout[:20]!r}")

                # 优先尝试 UTF-8
                try:
                    stdout = proc.stdout.decode('utf-8', errors='strict')
                except UnicodeDecodeError:
                    # 尝试 UTF-16 LE (PowerShell 默认)
                    try:
                        stdout = proc.stdout.decode('utf-16-le', errors='strict')
                    except UnicodeDecodeError:
                        # 最后回退 GBK
                        stdout = proc.stdout.decode('gbk', errors='replace')

            # 智能解码 stderr
            stderr = ""
            if proc.stderr:
                try:
                    stderr = proc.stderr.decode('utf-8', errors='strict')
                except UnicodeDecodeError:
                    try:
                        stderr = proc.stderr.decode('utf-16-le', errors='strict')
                    except UnicodeDecodeError:
                        stderr = proc.stderr.decode('gbk', errors='replace')

            return {
                'success': proc.returncode == 0,
                'stdout': stdout,
                'stderr': stderr,
                'returncode': proc.returncode,
                'command': command,
                'cwd': workspace,
                'workspace': workspace,
                'platform': 'Windows'
            }
        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'stdout': '',
                'stderr': f'Timeout after {timeout}s',
                'returncode': -1,
                'command': command,
                'cwd': workspace,
                'workspace': workspace,
                'platform': 'Windows'
            }
        finally:
            if os.path.exists(script_path):
                os.remove(script_path)

    async def cleanup_shell(self):
        if self._persistent_shell:
            await self._persistent_shell.close()
            self._persistent_shell = None


    def is_command_safe(self, command: str) -> bool:
        if not command or not command.strip():
            logger.warning("Empty command rejected")
            return False
        return True

    def _preprocess_command(self, command: str) -> str:
        """预处理命令，修复常见的格式问题"""
        if not command:
            return command
        
        original = command
        
        # Windows: 智能处理反引号
        if self.is_windows:
            # 只处理 curl/wget 命令中的反引号
            if 'curl' in command or 'wget' in command:
                # 情况 1: 反引号在引号内 - 直接移除反引号
                # 例如：" `http://...` " -> "http://..."
                command = re.sub(r'"\s*`([^`]+)`\s*"', r'"\1"', command)
                # 情况 2: 反引号单独出现 - 替换为双引号
                command = command.replace('`', '"')
            
            # 其他命令中的反引号保持不变（可能是故意使用的）
        
        if original != command:
            logger.info(f"[命令预处理] 原始：{original[:100]}...")
            logger.info(f"[命令预处理] 修正后：{command[:100]}...")
        
        return command

    async def _execute_in_shell(self, command: str, timeout: int = 600) -> Dict[str, Any]:
        # 预处理命令
        command = self._preprocess_command(command)
        logger.info(f"[执行命令] {command[:150]}...")

        if command.lower().strip().startswith('powershell'):
            # 在线程池中运行同步的 _run_powershell_script
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._run_powershell_script, command, timeout)
            return result

        shell = await self._get_persistent_shell()
        return await shell.execute(command, timeout)

    async def _execute_in_shell_streaming(self, command: str, output_callback, timeout: int = 600):
        # 预处理命令
        command = self._preprocess_command(command)
        logger.info(f"[流式执行] {command[:150]}...")
        shell = await self._get_persistent_shell()
        async for chunk in shell.execute_streaming(command, output_callback, timeout):
            yield chunk

    async def scan(self, target: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        command = options.get("command", "") if options else ""
        if not command:
            return {"success": False, "error": "No command specified"}

        if not self.is_command_safe(command):
            logger.warning(f"Rejected empty command")
            return {
                "success": False,
                "error": "Command rejected",
                "command": command,
                "stdout": "",
                "stderr": "Empty command is not allowed"
            }

        # 预处理命令（修复反引号等格式问题）
        command = self._preprocess_command(command)

        # 直接使用持久化 Shell 异步执行
        result = await self._execute_in_shell(command, 600)
        return {
            "success": result.get("success", False),
            "command": result.get("command", command),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "returncode": 0 if result.get("success") else -1,
            "platform": "Kali Linux" if self.is_kali else platform.system(),
            "workspace": self.workspace_dir,
            "cwd": result.get("cwd", self.workspace_dir)
        }

    def is_available(self) -> bool:
        return True

    def get_allowed_commands(self) -> List[str]:
        return ["ALL_COMMANDS_ALLOWED"]

    async def _run_command_async(self, command: str, timeout: int = 600) -> Dict[str, Any]:
        return await self._execute_in_shell(command, timeout)

    async def scan_parallel(self, commands: List[str], timeout: int = 600) -> List[Dict[str, Any]]:
        if not commands:
            return []

        logger.info(f"[SCAN_PARALLEL] Starting sequential execution of {len(commands)} commands: {commands}")

        results = []
        for i, cmd in enumerate(commands):
            try:
                logger.info(f"[SCAN_PARALLEL] Executing command {i + 1}/{len(commands)}: {cmd[:100]}...")
                # 直接使用原始命令，通过持久化 Shell 执行（不做任何 CSRF 预处理）
                result = await self._execute_in_shell(cmd, timeout)
                results.append(result)
                logger.info(
                    f"[SCAN_PARALLEL] Command {i + 1}/{len(commands)} completed: success={result.get('success')}")
            except Exception as e:
                logger.error(f"[SCAN_PARALLEL] Command {commands[i]} raised exception: {e}")
                results.append({
                    "success": False,
                    "error": str(e),
                    "command": commands[i],
                    "stdout": "",
                    "stderr": str(e),
                    "returncode": -1,
                    "platform": "Kali Linux" if self.is_kali else platform.system(),
                    "workspace": self.workspace_dir
                })

        logger.info(f"[SCAN_PARALLEL] Sequential execution completed: {len(results)} results")
        return results

    def scan_parallel_sync(self, commands: List[str], timeout: int = 600) -> List[Dict[str, Any]]:
        import concurrent.futures
        logger.info(f"[SCAN_PARALLEL_SYNC] Starting sync execution of {len(commands)} commands: {commands}")
        try:
            # 始终在线程池中执行，避免与主事件循环冲突
            logger.info(f"[SCAN_PARALLEL_SYNC] Submitting to thread pool with timeout={timeout * len(commands) + 30}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, self.scan_parallel(commands, timeout))
                logger.info(f"[SCAN_PARALLEL_SYNC] Waiting for result...")
                result = future.result(timeout=timeout * len(commands) + 30)
                logger.info(f"[SCAN_PARALLEL_SYNC] Got result successfully")
                return result
        except concurrent.futures.TimeoutError:
            logger.error(f"[SCAN_PARALLEL_SYNC] Execution timed out after {timeout * len(commands) + 30} seconds")
            return [{
                "success": False,
                "error": "Execution timeout",
                "command": commands[0] if commands else "",
                "stdout": "",
                "stderr": "命令执行超时",
                "returncode": -1,
                "platform": "Kali Linux" if self.is_kali else platform.system(),
                "workspace": self.workspace_dir
            }]
        except Exception as e:
            logger.error(f"[SCAN_PARALLEL_SYNC] Execution failed: {e}")
            import traceback
            logger.error(f"[SCAN_PARALLEL_SYNC] Traceback: {traceback.format_exc()}")
            return [{
                "success": False,
                "error": str(e),
                "command": commands[0] if commands else "",
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "platform": "Kali Linux" if self.is_kali else platform.system(),
                "workspace": self.workspace_dir
            }]

    def terminate_process(self, task_id: str):
        if task_id in self._running_processes:
            proc = self._running_processes[task_id]
            try:
                if self.is_windows:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                  capture_output=True, timeout=5)
                else:
                    proc.terminate()
                    proc.wait(timeout=5)
                logger.info(f"Terminated process for task {task_id}")
            except Exception as e:
                logger.error(f"Failed to terminate process {task_id}: {e}")
            finally:
                del self._running_processes[task_id]

    async def run_command_interruptible(self, command: str, task_id: str, timeout: int = 600,
                                        cancel_check_callback=None) -> Dict[str, Any]:
        shell = await self._get_persistent_shell()

        cancelled = False
        result = None

        try:
            async with self._process_lock:
                self._running_processes[task_id] = shell

            async def check_cancellation():
                nonlocal cancelled
                while not cancelled:
                    if cancel_check_callback and cancel_check_callback():
                        cancelled = True
                        await self.cleanup_shell()
                        return True
                    await asyncio.sleep(0.5)
                return False

            cancel_task = asyncio.create_task(check_cancellation())

            try:
                result = await asyncio.wait_for(
                    shell.execute(command, timeout),
                    timeout=timeout
                )
                result["cancelled"] = False
            except asyncio.TimeoutError:
                cancelled = True
                result = {
                    "success": False,
                    "error": "Command timed out",
                    "command": command,
                    "stdout": "",
                    "stderr": f"Timeout after {timeout} seconds",
                    "returncode": -1,
                    "platform": "Kali Linux" if self.is_kali else platform.system(),
                    "workspace": self.workspace_dir,
                    "cancelled": True
                }
            finally:
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass

                async with self._process_lock:
                    if task_id in self._running_processes:
                        del self._running_processes[task_id]

            return result

        except Exception as e:
            logger.error(f"Interruptible command execution failed: {e}")
            async with self._process_lock:
                if task_id in self._running_processes:
                    del self._running_processes[task_id]
            return {
                "success": False,
                "error": str(e),
                "command": command,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "platform": "Kali Linux" if self.is_kali else platform.system(),
                "workspace": self.workspace_dir
            }

    def run_command_interruptible_sync(self, command: str, task_id: str, timeout: int = 600,
                                     cancel_check_callback=None) -> Dict[str, Any]:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.run_command_interruptible(command, task_id, timeout, cancel_check_callback)
                    )
                    return future.result()
            else:
                return asyncio.run(self.run_command_interruptible(command, task_id, timeout, cancel_check_callback))
        except Exception as e:
            logger.error(f"Sync interruptible command failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "command": command,
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "platform": "Kali Linux" if self.is_kali else platform.system(),
                "workspace": self.workspace_dir
            }

    async def run_command_streaming(self, command: str, task_id: str, output_callback,
                                   timeout: int = 600, cancel_check_callback=None) -> Dict[str, Any]:
        shell = await self._get_persistent_shell()

        cancelled = False
        full_output = ""

        async def check_cancellation():
            nonlocal cancelled
            while not cancelled:
                if cancel_check_callback and cancel_check_callback():
                    cancelled = True
                    await self.cleanup_shell()
                    return True
                await asyncio.sleep(0.5)
            return False

        cancel_task = asyncio.create_task(check_cancellation())

        try:
            async for chunk in shell.execute_streaming(command, output_callback, timeout):
                if isinstance(chunk, bytes):
                    chunk = chunk.decode('utf-8', errors='replace')
                full_output += chunk
                if output_callback:
                    try:
                        result = output_callback(chunk, False)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        logger.error(f"Output callback error: {e}")

            cancel_task.cancel()

            success = True
            stderr = ""
            if "命令执行超时" in full_output or "GLOBAL_TIMEOUT" in full_output or "[IDLE_TIMEOUT]" in full_output:
                success = False
                stderr = full_output

            return {
                "success": success,
                "command": command,
                "stdout": full_output,
                "stderr": stderr,
                "returncode": 0 if success else -1,
                "platform": "Kali Linux" if self.is_kali else platform.system(),
                "workspace": self.workspace_dir,
                "cancelled": False
            }
        except Exception as e:
            import traceback
            logger.error(f"Streaming command execution failed: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": str(e),
                "command": command,
                "stdout": full_output,
                "stderr": str(e),
                "returncode": -1,
                "platform": "Kali Linux" if self.is_kali else platform.system(),
                "workspace": self.workspace_dir
            }
        finally:
            try:
                await cancel_task
            except asyncio.CancelledError:
                pass

    async def scan_parallel_streaming(self, commands: List[str], result_callback=None, timeout: int = 600) -> List[Dict[str, Any]]:
        if not commands:
            return []

        logger.info(f"Starting streaming parallel execution of {len(commands)} commands")

        tasks = [self._execute_in_shell(cmd, timeout) for cmd in commands]

        completed_results = []
        pending_commands = list(commands)

        for coro in asyncio.as_completed(tasks):
            try:
                result = await coro
                completed_results.append(result)

                cmd_index = commands.index(result["command"])
                pending_commands.remove(result["command"])

                logger.info(f"Command completed: {result['command']} (remaining: {len(pending_commands)})")

                if result_callback:
                    try:
                        result_callback(result, len(pending_commands))
                    except Exception as e:
                        logger.error(f"Callback error: {e}")

            except Exception as e:
                logger.error(f"Streaming execution error: {e}")

        logger.info(f"Streaming parallel execution completed: {len(completed_results)} results")
        return completed_results

    def scan_parallel_streaming_sync(self, commands: List[str], result_callback=None, timeout: int = 600) -> List[Dict[str, Any]]:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(
                        asyncio.run,
                        self.scan_parallel_streaming(commands, result_callback, timeout)
                    )
                    return future.result()
            else:
                return asyncio.run(self.scan_parallel_streaming(commands, result_callback, timeout))
        except Exception as e:
            logger.error(f"Streaming parallel execution failed: {e}")
            return [{
                "success": False,
                "error": str(e),
                "command": commands[0] if commands else "",
                "stdout": "",
                "stderr": str(e),
                "returncode": -1,
                "platform": "Kali Linux" if self.is_kali else platform.system(),
                "workspace": self.workspace_dir
            }]

    def get_workspace_dir(self) -> str:
        return self.workspace_dir

    async def get_shell_state(self) -> Optional[Dict[str, Any]]:
        if self._persistent_shell:
            state = self._persistent_shell.get_state()
            return {
                "cwd": state.cwd,
                "env": state.env,
                "history": state.history,
                "workspace": self.workspace_dir
            }
        return None

    def cleanup(self):
        if self._persistent_shell:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.cleanup_shell())
                else:
                    loop.run_until_complete(self.cleanup_shell())
            except Exception as e:
                logger.error(f"Failed to cleanup shell: {e}")
        self.cleanup_cookies()
        self.cleanup_workspace()
