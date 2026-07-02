import os
import json
import re
from dataclasses import dataclass

# ==========================================
# LLM 接入配置
# ==========================================
# 使用 langchain_openai.ChatOpenAI 对接任意 OpenAI 兼容 API
# 通过 BridgeLLM 包装类适配 browser-use Agent 的接口要求

LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://llm-jsxrc5fxazos0p33.cn-beijing.maas.aliyuncs.com/compatible-mode/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "sk-ws-H.RXLLEMY.ggdo.MEUCIQDRk0rUxl-CHddlOPlMpBsiwVfDrtXdnzadGNiiSMeLsQIgSFbbuso7rboEjrkKLNcPgVHvn4PdkwDsTq3Gj6VJ_3w")
LLM_MODEL = os.getenv("LLM_MODEL", "glm-5.1")


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
    from langchain_openai import ChatOpenAI

    inner = ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=0.7,
        timeout=120,
    )
    return BridgeLLM(inner)


# ==========================================
# Playwright 内置 Chromium 浏览器配置（推荐）
# 不使用本机 Edge，避免 user data 冲突
# ==========================================
EDGE_EXECUTABLE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_USER_DATA_DIR = None
USE_BUILTIN_CHROMIUM = True
