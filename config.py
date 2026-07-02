import os
import json
import re
from dataclasses import dataclass
from pathlib import Path

# ==========================================
# 加载 .env 文件（如果存在）
# ==========================================
def _load_env_file():
    """从项目根目录的 .env 文件加载环境变量"""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    # 只在未设置时才写入，命令行传入的优先
                    if key not in os.environ:
                        os.environ[key] = value

_load_env_file()


# ==========================================
# LLM 接入配置（从环境变量读取，无默认值）
# ==========================================
# 使用方式：
#   1. 设置系统环境变量：export LLM_API_KEY=sk-xxx
#   2. 或在项目根目录创建 .env 文件（见 .env.example）
#   3. 或在运行前设置：$env:LLM_API_KEY="sk-xxx"

LLM_BASE_URL = os.getenv("LLM_BASE_URL")
LLM_API_KEY = os.getenv("LLM_API_KEY")
LLM_MODEL = os.getenv("LLM_MODEL")


def _validate_config():
    """验证必需的配置项是否已设置"""
    missing = []
    if not LLM_BASE_URL:
        missing.append("LLM_BASE_URL")
    if not LLM_API_KEY:
        missing.append("LLM_API_KEY")
    if not LLM_MODEL:
        missing.append("LLM_MODEL")
    
    if missing:
        raise RuntimeError(
            f"\n缺少必需的配置项: {', '.join(missing)}\n\n"
            f"请通过以下方式之一设置:\n"
            f"  1. 系统环境变量: export LLM_API_KEY=sk-xxx\n"
            f"  2. .env 文件: 在项目根目录创建 .env 文件（参考 .env.example）\n"
            f"  3. PowerShell: $env:LLM_API_KEY='sk-xxx'\n"
        )


@dataclass
class _Completion:
    """兼容 browser-use 的 ChatBrowserUse.ainvoke 返回值"""
    completion: object = None
    usage: object = None


class BridgeLLM:
    """包装 langchain ChatOpenAI，适配 browser-use Agent 接口

    browser-use 内部调用 llm.ainvoke(messages, output_format=ModelClass, session_id=...)
    并期望返回带 .completion 属性的结构化响应对象。

    本包装类负责：
    1. 将 browser-use 自有消息类型转成 langchain 可接受的纯 dict
    2. 用 ChatOpenAI 发标准 OpenAI 请求
    3. 从文本回复中提取 JSON 并用 output_format 解析
    """

    def __init__(self, llm):
        self._llm = llm

    # -- browser-use 要求的属性 --
    @property
    def provider(self):
        return "openai"

    @property
    def model(self):
        return self._llm.model_name

    @property
    def model_name(self):
        return self._llm.model_name

    @property
    def name(self):
        return self._llm.model_name

    # -- 透明转发其他属性 --
    def __getattr__(self, name):
        return getattr(self._llm, name)

    # -- 消息类型转换 --
    @staticmethod
    def _convert_content_part(part) -> dict:
        """将单个内容片段（可能是 Pydantic model 或 dict）转为 dict"""
        if isinstance(part, dict):
            return part
        if hasattr(part, "model_dump"):
            return part.model_dump()
        return {"type": "text", "text": str(part)}

    def _convert_messages(self, bu_messages: list):
        """将 browser-use 消息列表转成 langchain 可接受的纯 dict 列表"""
        converted = []
        for msg in bu_messages:
            role = getattr(msg, "role", "user")
            content_raw = getattr(msg, "content", str(msg))

            if isinstance(content_raw, str):
                content = content_raw
            elif isinstance(content_raw, list):
                parts = [self._convert_content_part(p) for p in content_raw]
                # 仅单一文本 → 简化为字符串
                if len(parts) == 1 and parts[0].get("type") == "text":
                    content = parts[0]["text"]
                else:
                    content = parts
            else:
                content = str(content_raw)

            converted.append({"role": role, "content": content})

        return converted

    # -- 兼容 browser-use 的 ainvoke --
    async def ainvoke(self, messages, output_format=None, session_id=None, **kwargs):
        """发起 LLM 调用并解析结构化输出"""
        # 删掉 browser-use 专用参数
        kwargs.pop("request_type", None)
        kwargs.pop("anonymized_telemetry", None)

        converted = self._convert_messages(messages)

        response = await self._llm.ainvoke(converted, **kwargs)
        text = response.content if hasattr(response, "content") else str(response)

        if output_format is not None:
            parsed = self._parse_json_output(text, output_format)
            return _Completion(completion=parsed)
        else:
            return _Completion(completion=text)

    @staticmethod
    def _parse_json_output(text: str, output_format):
        """从 LLM 文本回复中提取 JSON 并用 Pydantic 模型校验"""
        # 优先匹配 ```json ... ``` 代码块
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
        else:
            # 尝试匹配最外层 { ... }
            m = re.search(r"(\{.*\})", text, re.DOTALL)
            if m:
                candidate = m.group(1).strip()
            else:
                candidate = text.strip()

        data = json.loads(candidate)
        return output_format.model_validate(data)


def get_llm():
    """创建并返回配置好的 LLM 实例"""
    from langchain_openai import ChatOpenAI
    
    # 运行时验证配置（导入时不验证，允许预加载模块）
    _validate_config()

    inner = ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=0.7,
        timeout=120,
    )
    return BridgeLLM(inner)


# ==========================================
# 浏览器配置（使用本机 Edge）
# ==========================================
EDGE_EXECUTABLE_PATH = os.getenv(
    "EDGE_EXECUTABLE_PATH",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)
EDGE_USER_DATA_DIR = os.getenv("EDGE_USER_DATA_DIR", "") or None
