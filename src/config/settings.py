import os
from pathlib import Path
from typing import Dict, List, Any

# 基础目录配置（在类外部定义，便于引用）
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
FINDINGS_DIR = DATA_DIR / "findings"
TASKS_DIR = DATA_DIR / "tasks"

# 确保目录存在
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
FINDINGS_DIR.mkdir(exist_ok=True)
TASKS_DIR.mkdir(exist_ok=True)

class Settings:
    APP_NAME = "智能渗透测试系统"
    VERSION = "1.0.0"

    # 目录配置（通过类属性访问）
    BASE_DIR = BASE_DIR
    DATA_DIR = DATA_DIR
    LOGS_DIR = LOGS_DIR
    FINDINGS_DIR = FINDINGS_DIR
    TASKS_DIR = TASKS_DIR

    # 支持的模型提供商配置
    LLM_PROVIDERS = {
        "openai": {
            "api_key": os.getenv("OPENAI_API_KEY", ""),
            "base_url": os.getenv("OPENAI_BASE_URL", "https://api.laozhang.ai/v1"),
            "models": ["gpt-4o", "gpt-4o-mini", "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"],
            "default_model": "gpt-4o"
        },
        "qwen": {
            "api_key": os.getenv("QWEN_API_KEY", ""),
            "base_url": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            "models": ["qwen-turbo", "qwen-plus", "qwen-max"],
            "default_model": "qwen-plus"
        },
        "deepseek": {
            "api_key": os.getenv("DEEPSEEK_API_KEY", ""),
            "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "models": ["deepseek-chat", "deepseek-reasoner", "deepseek-coder"],
            "default_model": "deepseek-reasoner"
        },
        "doubao": {
            "api_key": os.getenv("DOUBAO_API_KEY", ""),
            "base_url": os.getenv("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            "models": ["ep-20241203173318-5j5wv", "ep-20241203173318-5j5wv-pro"],
            "default_model": "ep-20241203173318-5j5wv"
        },
        "zhipu": {
            "api_key": os.getenv("ZHIPU_API_KEY", ""),
            "base_url": os.getenv("ZHIPU_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            "models": ["glm-5.1", "glm-5", "glm-5-turbo", "glm-4-plus", "glm-4-air-250414"],
            "default_model": "glm-4.6v"
        }
    }

    # 默认使用的模型提供商
    DEFAULT_LLM_PROVIDER = os.getenv("DEFAULT_LLM_PROVIDER", "qwen")

    # 获取当前配置的模型
    @classmethod
    def get_llm_config(cls, provider=None):
        provider = provider or cls.DEFAULT_LLM_PROVIDER
        if provider not in cls.LLM_PROVIDERS:
            raise ValueError(f"不支持的模型提供商: {provider}")

        provider_config = cls.LLM_PROVIDERS[provider]
        return {
            "provider": provider,
            "api_key": provider_config["api_key"],
            "base_url": provider_config["base_url"],
            "model": os.getenv(f"{provider.upper()}_MODEL", provider_config["default_model"]),
            "temperature": 0.7,
            "max_tokens": 8000
        }

    VULN_DB_PATH = DATA_DIR / "vuln_db.json"

    WEB_CONFIG = {
        "host": "0.0.0.0",
        "port": 8000,
        "debug": False
    }

settings = Settings()
