import os
import json
import re
from typing import Any
from dataclasses import dataclass
from pathlib import Path

import json_repair

# 当前 agent 模式（由 agent/core.py 设置，_sanitize_actions 据此过滤无关工具）
_CURRENT_AGENT_MODE = "zhihu"  # "zhihu" | "wps" | "wechat"
def set_agent_mode(mode: str):
    global _CURRENT_AGENT_MODE
    _CURRENT_AGENT_MODE = mode

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
                if len(parts) == 1 and parts[0].get("type") == "text":
                    content = parts[0]["text"]
                else:
                    content = parts
            else:
                content = str(content_raw)

            # 对 system 消息追加 JSON-only 强制指令
            if role == "system" and isinstance(content, str):
                if "CRITICAL OUTPUT RULE" not in content:
                    content += (
                        "\n\n## CRITICAL OUTPUT RULE (OVERRIDES ALL OTHER INSTRUCTIONS)\n"
                        "You MUST respond with ONLY a valid JSON object. No XML, no tool_call tags, "
                        "no markdown wrappers, no thinking blocks outside the JSON.\n\n"
                        "CRITICAL: Put the \"action\" array FIRST in the JSON, before \"thinking\"！\n"
                        "Keep thinking/evaluation_previous_goal/memory/next_goal VERY short (<50 chars each).\n"
                        "Keep plan_update items short (<30 chars each).\n"
                        "The JSON structure MUST be:\n"
                        '{"action":[{"navigate":{"url":"..."}},{"click":{"index":1}}],'
                        '"thinking":"brief","evaluation_previous_goal":"brief",'
                        '"memory":"brief","next_goal":"brief"}\n\n'
                        "Available actions: navigate(url), click(index), input(index,text), done(text,success), "
                        "search(query), extract(query), scroll(amount), wait(seconds), send_keys(keys), "
                        "write_file(file_name,content), read_file(file_name), evaluate(code), "
                        "close, go_back, switch(tab_index), find_elements(query), find_text(text), "
                        "dropdown_options(index), select_dropdown(index,text), search_page(query), "
                        "save_as_pdf(path), upload_file(index,path), replace_file(file_name,content), "
                        "get_playbook_selector(page_name,element_description), "
                        "execute_playwright_action(selector,action,value?), "
                        "zhihu_body_input_with_image(html_content,article_topic), "
                        "zhihu_body_input(html_content), "
                        "generate_and_paste_image(article_topic,article_content?), "
                        "ask_human_for_intervention(reason).\n"
                        "DO NOT use <tool_call>, <arg_key>, <arg_value>, <think>, or any XML/HTML tags. "
                        "Output ONLY the JSON object, with action FIRST."
                    )

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

    # browser-use 内置 action key 白名单
    BUILTIN_KEYS = {
        "navigate", "click", "input", "done", "search", "extract",
        "scroll", "send_keys", "find_elements", "find_text",
        "switch", "close", "go_back", "wait", "upload_file",
        "search_page", "save_as_pdf", "dropdown_options", "select_dropdown",
        "write_file", "replace_file", "read_file", "evaluate",
    }
    # 自定义 tool key 白名单（来自 tools/ 注册）
    CUSTOM_KEYS = {
        "zhihu_body_input",
        "zhihu_body_input_with_image",
        "get_playbook_selector",
        "execute_playwright_action", "generate_and_paste_image",
        "ask_human_for_intervention",
        "wps_create_document_and_export_pdf",
        "get_wps_template",
        "wechat_search_and_follow", "wechat_send_message",
        # 常见 LLM 幻觉名称 → 自动映射到真实工具
        "get_playwright_action",
        "get_playwright_selector",
        # 旧名称兼容 → LLM 可能仍会输出旧名
        "generate_and_insert_svg_image",
    }
    ALL_KNOWN_KEYS = BUILTIN_KEYS | CUSTOM_KEYS

    @staticmethod
    def _parse_tool_call_xml(text: str) -> dict | None:
        """将 GLM-5.1 原生 <tool_call> 格式转换为 browser-use JSON 格式

        GLM-5.1 输出：
          <tool_call>write_file<arg_key>file_name</arg_key><arg_value>todo.md</arg_key>
          <arg_key>content</arg_key><arg_value># Content...</arg_value></tool_call>

        转换为：
          {"thinking":"...","action":[{"write_file":{"file_name":"...","content":"..."}}],...}
        """
        # 查找所有 <tool_call> 块（结束于 </tool_call> 或 </think>）
        tc_pattern = re.compile(r'<tool_call>(.*?)(?:</tool_call>|</think>)', re.DOTALL)
        matches = tc_pattern.findall(text)
        if not matches:
            return None

        actions = []
        for raw_block in matches:
            # 块内容：write_file<arg_key>file_name</arg_key><arg_value>todo.md</arg_key>...
            # 或变体: write_filefile_name: "todo.md"... (无 <arg_key> 标签)
            block = raw_block.strip()
            has_arg_tags = '<arg_key>' in block

            # 提取函数名
            m = re.match(r'([a-z_]+)', block)
            if not m:
                continue
            func_name = m.group(1)

            if has_arg_tags:
                block = block[len(func_name):]  # 去掉函数名

            # 解析 <arg_key>/<arg_value> 对
            # GLM-5.1 格式: <arg_key>K</arg_key><arg_value>V</arg_value>...
            # 兼容缺失 </arg_value>：值以 </arg_value> 或下一个 <arg_key> 或 </tool_call> 结尾
            params = {}
            if has_arg_tags:
                key_re = re.compile(r'<arg_key>(.*?)</arg_key>', re.DOTALL)
                for km in key_re.finditer(block):
                    key = km.group(1).strip()
                    # 值从 </arg_key> 之后开始
                    after_key = block[km.end():]
                    # 以 <arg_value> 开头
                    vm = re.match(r'\s*<arg_value>', after_key)
                    if not vm:
                        continue
                    after_val_start = after_key[vm.end():]
                    # 值到 </arg_value> 或下一个 <arg_key> 或 </tool_call> 或末尾
                    end_m = re.search(r'</arg_value>|<arg_key>|<arg_value>', after_val_start, re.DOTALL)
                    value = after_val_start[:end_m.start()].strip() if end_m else after_val_start.strip()
                    params[key] = value
            else:
                # 无标签变体: write_filefile_name: "todo.md", content: "..."
                # 或: navigateurl: "https://..."
                rest = block[len(func_name):].strip()
                # 尝试解析为 key: value 格式
                # 先尝试作为 {key: value, ...} JSON 解析
                if rest.startswith('{'):
                    try:
                        params = json_repair.loads(rest)
                    except Exception:
                        pass
                else:
                    # 解析 key: "value", key: value 格式
                    # 将 "file_name: "a", content: "b"" 包装为 {"file_name": "a", "content": "b"}
                    wrapped = '{' + rest + '}'
                    try:
                        params = json_repair.loads(wrapped)
                    except Exception:
                        # 手动解析 key: value 对
                        for pair in re.finditer(r'(\w+)\s*:\s*"((?:[^"\\]|\\.)*)"', rest):
                            params[pair.group(1)] = pair.group(2)
                        for pair in re.finditer(r'(\w+)\s*:\s*(\d+)', rest):
                            if pair.group(1) not in params:
                                params[pair.group(1)] = int(pair.group(2))

            if not params and func_name in ("screenshot",):
                actions.append({"screenshot": {}})
                continue

            if not params:
                continue

            # 特殊处理
            if func_name == "wait":
                seconds = params.get("time") or params.get("seconds") or 3
                actions.append({"wait": {"seconds": int(seconds)}})
            elif func_name == "navigate" and "url" in params:
                actions.append({"navigate": {"url": params["url"]}})
            elif func_name == "click" and "index" in params:
                actions.append({"click": {"index": int(params["index"])}})
            elif func_name == "input" and "index" in params and "text" in params:
                actions.append({"input": {"index": int(params["index"]), "text": params["text"]}})
            elif func_name == "write_file" and "file_name" in params and "content" in params:
                actions.append({"write_file": params})
            elif func_name == "extract" and "query" in params:
                actions.append({"extract": {"query": params["query"]}})
            elif func_name == "scroll":
                actions.append({"scroll": {"amount": params.get("amount", 500)}})
            elif func_name == "done":
                actions.append({"done": {"text": params.get("text", "Done"), "success": params.get("success", True)}})
            elif func_name in BridgeLLM.ALL_KNOWN_KEYS:
                actions.append({func_name: params})
            else:
                print(f"[WARN] Unknown function in tool_call: {func_name}")
                continue

        if not actions:
            return None

        # 提取紧接在 <tool_call> 之前的文本作为 thinking
        first_tc = text.index('<tool_call>')
        thinking = text[:first_tc].strip()
        if len(thinking) > 500:
            thinking = thinking[:500]

        return {
            "thinking": thinking or "Executing actions via tool_call",
            "evaluation_previous_goal": "",
            "memory": "",
            "next_goal": "",
            "action": actions,
        }

    @staticmethod
    def _extract_json_candidate(text: str) -> str:
        """从 LLM 回复中提取最可能的 JSON 候选字符串，自动修复截断"""
        # 0) 剥离 GLM 原生标签和 tool_call 前缀
        cleaned = re.sub(r'</?think[^>]*>', '', text, flags=re.DOTALL)
        cleaned = re.sub(r'</?tool_call[^>]*>', '', cleaned, flags=re.DOTALL)
        cleaned = re.sub(r'(?:write_file|read_file|replace_file|screenshot)(?=\{)', '', cleaned)
        if cleaned.strip():
            text = cleaned.strip()

        # 1) 代码块
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            if candidate and candidate.startswith("{"):
                return BridgeLLM._auto_close_json(candidate)

        # 2) 括号计数提取完整 JSON
        start = 0
        while True:
            start = text.find("{", start)
            if start < 0:
                break
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start, len(text)):
                ch = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"' and not escape_next:
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1].strip()
                        if '"action"' in candidate or '"thinking"' in candidate or '"current_state"' in candidate:
                            return BridgeLLM._auto_close_json(candidate)
                        if candidate.count('"') >= 8:
                            return BridgeLLM._auto_close_json(candidate)
                        start = i + 1
                        break
            else:
                break

        # 3) 全文回退
        if text.strip().startswith("{"):
            return BridgeLLM._auto_close_json(text.strip())
        return text.strip()

    @staticmethod
    def _merge_duplicate_action_keys(text: str) -> str:
        """修复 LLM 输出重复 \"action\" key 的问题

        Qwen/GLM 有时会输出:
          {"action":[...], "thinking":"...", ..., "action":[...]}
        Python json.loads 只保留最后一个 action，通常是不完整的（只有部分字段）。
        此处用正则提取所有 \"action\":[...] 数组，合并为一个。
        """
        # 查找所有 "action":[...] （嵌套括号计数）
        action_arrays = []
        for m in re.finditer(r'"action"\s*:\s*', text):
            start = m.end()
            if start >= len(text) or text[start] != '[':
                continue
            # 括号计数找匹配的 ]
            depth = 0
            in_str = False
            esc = False
            end = start
            for i in range(start, len(text)):
                ch = text[i]
                if esc:
                    esc = False
                    continue
                if ch == '\\':
                    esc = True
                    continue
                if ch == '"' and not esc:
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        end = i + 1
                        break
            action_arrays.append(text[start:end])

        if len(action_arrays) <= 1:
            return text  # 没有重复，原样返回

        # 合并所有 action 数组：[[a,b],[c]] → [a,b,c]
        merged_items = []
        for arr_str in action_arrays:
            # 去掉首尾的 []
            inner = arr_str[1:-1].strip()
            if inner:
                merged_items.append(inner)
        merged_action = '[' + ', '.join(merged_items) + ']'

        # 替换所有 "action":[...] 为单一的合并版，删除多余的
        # 策略：找到第一个 "action" 出现位置，保留它 + 合并内容，删除后面所有的 "action" key
        first_action_match = re.search(r'"action"\s*:\s*', text)
        if not first_action_match:
            return text

        prefix = text[:first_action_match.start()]
        # 从第一个 action 的 value 结束位置开始，找到后面的内容
        first_arr_start = first_action_match.end()
        # 找到第一个 [ 的匹配 ]
        depth = 0
        in_str = False
        esc = False
        first_arr_end = first_arr_start
        for i in range(first_arr_start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == '\\':
                esc = True
                continue
            if ch == '"' and not esc:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    first_arr_end = i + 1
                    break

        suffix = text[first_arr_end:]

        # 删除 suffix 中所有 "\"action\"\s*:\s*\[...]:...](?:[^\[\]]|\[[^\]]*\])*\]\s*,?\s*" 段
        # 同时吸收前面可能残留的逗号
        suffix = re.sub(r',?\s*"action"\s*:\s*\[(?:[^\[\]]|\[[^\]]*\])*\]\s*,?\s*', '', suffix)

        # 清理末尾残留标点（trailing comma after last field）
        suffix = re.sub(r',\s*}', '}', suffix)

        result = prefix + '"action":' + merged_action + suffix
        return result

    @staticmethod
    def _pre_repair_json(text: str) -> str:
        """预修复 GLM-5.1 独有的 JSON 语法错误（json_repair 也修不了的）
        
        已知变体：
        - {"write_file","file_name":"x"}  → 逗号当冒号+少了{，修复为 {"write_file":{"file_name":"x"}}
        - {"click","index":370}  → 同上
        - {"navigate","url":"https://..."}  → 同上
        - {"switch","tab_index":0}  → 同上
        """
        # 模式: {"action_name","param_key":value}  — 即第二个条目是 "xxx": 而非 "xxx",
        # 且第一个条目以 ", 结束（而非 ": 开始）
        # 修复: 将 {"act","k":v -> {"act":{"k":v}
        import re
        
        def fix_flat_action(match):
            action_name = match.group(1)
            params_raw = match.group(2)
            # 闭合这个嵌套对象
            return '{"' + action_name + '":{' + params_raw + '}}'
        
        # 匹配: {"key","param" 模式 — action 名为 key，后面跟逗号而非冒号+{，再跟参数字符串键
        # 特征: {" 后面跟 action_name，然后 "," 而非 ":"
        pattern = r'\{"([a-z_]+)",("[a-z_]+":[^}]+)\}(?!\})'
        text = re.sub(pattern, fix_flat_action, text)
        
        # 第二遍：更宽松的模式 — key 后面是逗号且下一个是字符串 key
        # {"switch","tab_index":0} → {"switch":{"tab_index":0}}
        pattern2 = r'\{"([a-z_]+)",("[a-z_]+":[^,}]+(?:,[^}]+)?)\}'
        text = re.sub(pattern2, fix_flat_action, text)
        
        return text

    @staticmethod
    def _auto_close_json(candidate: str) -> str:
        """自动修复被截断的 JSON：
        1) 去掉末尾不完整片段
        2) 闭合未结束的字符串、对象、数组
        """
        # 0) 检测并去掉末尾的截断片段
        # 如果末尾在字符串内部 → 截断
        # 如果末尾是 "key → 移除这个不完整的键
        # 如果末尾是 "key" 后面缺 : → 移除这个键
        c = candidate.rstrip()
        # Remove trailing comma
        if c.endswith(','):
            c = c[:-1].rstrip()
        # Remove incomplete key name like "act or "actio
        # backtracks to last complete element before a bare quote that starts a new key
        # Pattern: ...],"inc → back to ...],
        # Pattern: ...,"inc → back to ...,
        last_comma = c.rfind(',')
        if last_comma > 0:
            after_comma = c[last_comma + 1:].strip()
            if after_comma.startswith('"') and not after_comma.endswith('"'):
                # Incomplete key like "act → remove it
                c = c[:last_comma].rstrip()
                if c.endswith(','):
                    c = c[:-1].rstrip()
            elif after_comma.startswith('{') and not after_comma.endswith('}'):
                # Incomplete nested object → remove it
                c = c[:last_comma].rstrip()
                if c.endswith(','):
                    c = c[:-1].rstrip()
            elif after_comma.startswith('[') and not after_comma.endswith(']'):
                # Incomplete array → remove it
                c = c[:last_comma].rstrip()
                if c.endswith(','):
                    c = c[:-1].rstrip()

        # 1) 计算括号深度，补全未闭合的结构
        depth = 0
        in_string = False
        escape_next = False
        for ch in c:
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                depth += 1
            elif ch in '}]':
                depth -= 1

        # 如果在字符串内，先闭合字符串
        if in_string:
            c += '"'

        # 闭合所有未关闭的 ] 和 }
        # 先检测最后未闭合的括号类型
        # 从后向前扫描，找到需要闭合的 bracket 类型
        stack = []
        in_str = False
        esc = False
        for ch in c:
            if esc:
                esc = False
                continue
            if ch == '\\':
                esc = True
                continue
            if ch == '"' and not esc:
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{':
                stack.append('}')
            elif ch == '}':
                if stack and stack[-1] == '}':
                    stack.pop()
                else:
                    stack.append('}')  # unmatched close
            elif ch == '[':
                stack.append(']')
            elif ch == ']':
                if stack and stack[-1] == ']':
                    stack.pop()
                else:
                    stack.append(']')

        # Only close unmatched opens (reversed: closing brackets)
        while stack:
            c += stack.pop()

        return c

    @staticmethod
    def _pre_repair_json(text: str) -> str:
        """预修复 GLM-5.1 奇葩 JSON 语法

        GLM-5.1 会把 "write_file":{...} 写成 "write_file",...
        即用逗号代替冒号+花括号。

        策略: 逐字符扫描，检测到 { followed by "action_name", 时插入 :{
        然后在匹配的外层 } 前补一个 }。
        """
        _TOOL_SET = {
            'navigate', 'click', 'input', 'done', 'search', 'extract', 'scroll',
            'send_keys', 'find_elements', 'find_text', 'switch', 'close', 'go_back',
            'wait', 'upload_file', 'search_page', 'save_as_pdf', 'dropdown_options',
            'select_dropdown', 'write_file', 'replace_file', 'read_file', 'evaluate',
            'screenshot', 'zhihu_body_input',
            'zhihu_body_input_with_image',
            'get_playbook_selector',
            'execute_playwright_action', 'generate_and_paste_image',
            'generate_and_insert_svg_image',
            'ask_human_for_intervention',
            'wps_create_document_and_export_pdf', 'get_wps_template',
            'wechat_observe', 'wechat_search', 'wechat_click_first_result',
            'wechat_click_button', 'wechat_type_and_send',
        }

        result = []
        i = 0
        n = len(text)
        # 记录注入位置: (原始index_offset, 需插入的 } 的位置偏移)
        pending_close_positions = []  # stack of (original_pos_of_matching_})

        while i < n:
            ch = text[i]
            if ch == '{':
                result.append(ch)
                i += 1
                # 跳过空白
                while i < n and text[i] in ' \t\n\r':
                    result.append(text[i])
                    i += 1
                # 尝试匹配 "tool_name",
                if i < n and text[i] == '"':
                    j = i + 1
                    while j < n and text[j] != '"':
                        j += 1
                    if j < n:
                        tool_name = text[i+1:j]
                        after_quote = text[j+1:j+2] if j+1 < n else ''
                        # 检测："tool_name" 后是逗号(没有冒号)
                        rest_idx = j + 1
                        while rest_idx < n and text[rest_idx] in ' \t\n\r':
                            rest_idx += 1
                        if rest_idx < n and text[rest_idx] == ',' and tool_name in _TOOL_SET:
                            # 这是 GLM 奇葩格式！补 : {
                            result.append(text[i:j+1])  # "tool_name"
                            result.append(':{')
                            i = rest_idx + 1  # 跳过逗号
                            continue
                # 正常 { ，不特殊处理
                continue
            elif ch == '}':
                # 这是原来闭合外层 { 的 }
                # 检查后面是否紧接 ,{ 或 ]
                rest = text[i+1:].lstrip()
                if rest.startswith(',{') or rest.startswith(']'):
                    # 需要在这个 } 前插入一个 } （用于闭合注入的 inner {）
                    result.append('}')  # inner }
                    result.append('}')  # outer }
                    i += 1
                    continue
                result.append(ch)
                i += 1
                continue
            else:
                result.append(ch)
                i += 1

        return ''.join(result)

    @staticmethod
    def _sanitize_actions(data: dict) -> dict:
        """清理 LLM 输出的 action 数组中的常见格式错误

        GLM-5.1 已知问题：
        - 输出 {\"screenshot\": {}} 作为 action（无效，需删除）
        - 输出 {\"wait\": {\"time\": 10}} 而非 {\"wait\": 10}
        - 自定义工具参数格式错误或缺失必填字段
        """
        # browser-use 内置 action key 白名单
        BUILTIN_KEYS = {
            "navigate", "click", "input", "done", "search", "extract",
            "scroll", "send_keys", "find_elements", "find_text",
            "switch", "close", "go_back", "wait", "upload_file",
            "search_page", "save_as_pdf", "dropdown_options", "select_dropdown",
            "write_file", "replace_file", "read_file", "evaluate",
            "screenshot",  # browser-use 内置，GLM 常误调，保留但不真正执行
        }
        # 自定义 tool key 白名单（来自 tools/ 注册）
        CUSTOM_KEYS = {
            "zhihu_body_input",
            "zhihu_body_input_with_image",
            "get_playbook_selector",
            "execute_playwright_action", "generate_and_paste_image",
            "generate_and_insert_svg_image",  # 旧名兼容，_sanitize_actions 会自动映射
            "ask_human_for_intervention",
            "wps_create_document_and_export_pdf",
            "get_wps_template",
            "wechat_search_and_follow", "wechat_send_message",
            # 微信 Agent 工具
            "wechat_observe", "wechat_search", "wechat_click_first_result",
            "wechat_click_button", "wechat_type_and_send",
            # 常见 LLM 幻觉名称 → 自动映射到真实工具
            "get_playwright_action",  # Qwen 常幻觉此名 → 映射为 get_playbook_selector
            "get_playwright_selector",  # Qwen 另一常见幻觉名
        }
        ALL_KNOWN_KEYS = BUILTIN_KEYS | CUSTOM_KEYS

        if not isinstance(data, dict) or "action" not in data:
            return data

        actions = data["action"]
        if not isinstance(actions, list):
            return data

        cleaned = []
        for act in actions:
            # 0) 裸字符串 action（如 LLM 输出 ["wait(3)"] 被截断）→ 尝试转为 dict
            if isinstance(act, str):
                act_str = act.strip()
                # 匹配 wait(N) / wait 3
                wait_m = re.match(r'wait\s*\(?\s*(\d+)\s*\)?', act_str)
                if wait_m:
                    cleaned.append({"wait": {"seconds": int(wait_m.group(1))}})
                    print(f"[WARN] Converted string action '{act_str}' → wait")
                    continue
                # 匹配 done(text=..., success=...)
                if act_str.startswith('done'):
                    cleaned.append({"done": {"text": act_str, "success": True}})
                    print(f"[WARN] Converted string action '{act_str[:40]}' → done")
                    continue
                # 兜底：当 wait(1) 处理
                cleaned.append({"wait": {"seconds": 1}})
                print(f"[WARN] Unrecognized string action '{act_str[:40]}', defaulted to wait(1)")
                continue

            if not isinstance(act, dict):
                cleaned.append(act)
                continue

            # (removed: evaluate→paste_article_body, input→paste_article_body — paste is deprecated)

            # 1) {"screenshot": {}} → 替换为短暂 wait（避免 Agent 提前终止）
            if set(act.keys()) == {"screenshot"}:
                cleaned.append({"wait": 1})
                print("[WARN] Replaced screenshot action with wait(1)")
                continue
            # 2) 删除纯空对象 {}
            if not act:
                continue

            # 3) {"wait": {"time": N}} / {"wait": {"seconds": N}} → 确保是 dict 格式
            # browser-use v0.13.3 期望 {"wait": {"seconds": N}}，不是 {"wait": N}

            # 4) {"click": {"index": N}} → 确保整数
            if "click" in act and isinstance(act["click"], dict) and "index" in act["click"]:
                act = dict(act)
                act["click"] = dict(act["click"])
                act["click"]["index"] = int(act["click"]["index"])

            # 5) 修复 find_elements：GLM 传 query 而非 selector
            if "find_elements" in act and isinstance(act["find_elements"], dict):
                inner = dict(act["find_elements"])
                if "query" in inner and "selector" not in inner:
                    inner["selector"] = inner.pop("query")
                    act = dict(act)
                    act["find_elements"] = inner
                    print("[WARN] Auto-converted find_elements query → selector")

            # 6) 修复 wait：强制为 dict 格式 {"seconds": N}（browser-use v0.13.3 不接受裸整数）
            if "wait" in act:
                if isinstance(act["wait"], dict):
                    inner = act["wait"]
                    seconds = inner.get("time") or inner.get("seconds") or inner.get("duration") or 3
                elif isinstance(act["wait"], (int, float)):
                    seconds = int(act["wait"])
                else:
                    seconds = 3
                act = dict(act)
                act["wait"] = {"seconds": int(seconds)}

            # 6b) 修复 scroll：Qwen 常输出 {"scroll": 300} 裸整数，需转为 {"scroll": {"amount": 300}}
            #     browser-use 只接受 {"scroll": {"amount": N}} 格式
            if "scroll" in act:
                if isinstance(act["scroll"], (int, float)):
                    act = dict(act)
                    act["scroll"] = {"amount": int(act["scroll"])}
                    print(f"[WARN] Auto-wrapped scroll int → {{amount: {act['scroll']['amount']}}}")
                elif isinstance(act["scroll"], dict):
                    inner = act["scroll"]
                    if "pixels" in inner:
                        # Qwen 常见：{"scroll": {"pixels": 500}} → {"scroll": {"amount": 500}}
                        act = dict(act)
                        act["scroll"] = {"amount": int(inner["pixels"])}
                        print(f"[WARN] Auto-converted scroll pixels → amount ({act['scroll']['amount']})")
                    elif "amount" not in inner:
                        act = dict(act)
                        act["scroll"] = {"amount": 300}
                        print("[WARN] scroll missing amount, defaulted to 300")

            # 7) 修复 switch 的 tab_index → tab_id，且强制 tab_id 为字符串
            if "switch" in act and isinstance(act["switch"], dict):
                inner = dict(act["switch"])
                if "tab_index" in inner and "tab_id" not in inner:
                    inner["tab_id"] = str(inner.pop("tab_index"))
                    act = dict(act)
                    act["switch"] = inner
                    print("[WARN] Auto-converted switch tab_index → tab_id (str)")
                elif "tab_id" in inner and not isinstance(inner["tab_id"], str):
                    inner = dict(inner)
                    inner["tab_id"] = str(inner["tab_id"])
                    act = dict(act)
                    act["switch"] = inner
                    print("[WARN] Auto-converted switch tab_id int → str")

            # 8) 修复 evaluate：Qwen 常输出字符串 {"evaluate":"JS"} 而非 {"evaluate":{"code":"JS"}}
            if "evaluate" in act and isinstance(act["evaluate"], str):
                act = dict(act)
                act["evaluate"] = {"code": act["evaluate"]}
                print("[WARN] Auto-wrapped evaluate string → {code:...}")

            # 8b) 修复 find_elements：Qwen 常输出字符串 {"find_elements":"selector"} 而非 dict
            if "find_elements" in act and isinstance(act["find_elements"], str):
                act = dict(act)
                act["find_elements"] = {"selector": act["find_elements"]}
                print("[WARN] Auto-wrapped find_elements string → {selector:...}")

            # 9) 修复 switch tab_id 格式：真实 CDP ID 固定 4 位十六进制，不是 4 位的全替换
            #    int→str 得 "0"（太短）、科学计数法 56E6→"56000000.0"（太长）都无效
            #    改用 wait(1) 兜底，避免 navigate 导致页面重新加载→LLM 再次尝试切 tab 的循环
            if "switch" in act and isinstance(act["switch"], dict):
                inner = act["switch"]
                tab_id_val = inner.get("tab_id", "")
                if isinstance(tab_id_val, str) and len(tab_id_val) != 4:
                    act = {"wait": {"seconds": 1}}
                    print(f"[WARN] Replaced switch(tab_id='{tab_id_val}', len={len(tab_id_val)}) → wait(1)")

            # 10) LLM 幻觉工具名映射：Qwen 常见幻觉 → 真实工具
            # get_playwright_action → get_playbook_selector
            if "get_playwright_action" in act:
                old_val = act.pop("get_playwright_action")
                # 映射参数: element_description 可能在字符串里或对象里
                mapped = {}
                if isinstance(old_val, str):
                    mapped["element_description"] = old_val
                elif isinstance(old_val, dict):
                    mapped["page_name"] = old_val.get("page_name", "")
                    mapped["element_description"] = old_val.get("element_description", "")
                act = dict(act)
                act["get_playbook_selector"] = mapped
                print("[WARN] Mapped hallucinated get_playwright_action → get_playbook_selector")

            # 10b) 幻觉工具名映射：get_playwright_selector → get_playbook_selector
            if "get_playwright_selector" in act:
                old_val = act.pop("get_playwright_selector")
                mapped = {}
                if isinstance(old_val, str):
                    mapped["element_description"] = old_val
                elif isinstance(old_val, dict):
                    mapped["page_name"] = old_val.get("page_name", "")
                    mapped["element_description"] = old_val.get("element_description", "")
                act = dict(act)
                act["get_playbook_selector"] = mapped
                print("[WARN] Mapped hallucinated get_playwright_selector → get_playbook_selector")

            # 10c) 模式感知过滤：移除非当前 agent 的工具
            #   WPS mode → 只保留 wps_* / get_wps_*
            #   wechat mode → 只保留 wechat_*
            #   zhihu mode → 移除 wps_* / wechat_*
            ZHIHU_ONLY_KEYS = {"zhihu_body_input", "zhihu_body_input_with_image", "generate_and_paste_image", "ask_human_for_intervention"}
            WPS_ONLY_KEYS = {"wps_create_document_and_export_pdf", "get_wps_template"}
            WECHAT_ONLY_KEYS = {"wechat_search_and_follow", "wechat_send_message"}
            if _CURRENT_AGENT_MODE == "wps":
                for k in ZHIHU_ONLY_KEYS | WECHAT_ONLY_KEYS:
                    if k in act:
                        act.pop(k)
                        print(f"[WARN] Mode=wps, removed {k} (not registered)")
            elif _CURRENT_AGENT_MODE == "wechat":
                for k in ZHIHU_ONLY_KEYS | WPS_ONLY_KEYS:
                    if k in act:
                        act.pop(k)
                        print(f"[WARN] Mode=wechat, removed {k} (not registered)")
                # wechat 模式下，LLM 可能把 wechat_* 工具包在 evaluate 里当 JS 调用 → 自动解包
                if "evaluate" in act:
                    code = str(act.get("evaluate", ""))
                    for tool_name in ("wechat_search_and_follow", "wechat_send_message"):
                        if tool_name in code:
                            m = re.search(rf'{tool_name}\(\{{(.+?)\}}\)', code, re.DOTALL)
                            if m:
                                try:
                                    params = {}
                                    for kv_match in re.finditer(r"(\w+):\s*['\"]([^'\"]+)['\"]", m.group(1)):
                                        params[kv_match.group(1)] = kv_match.group(2)
                                    if params:
                                        act = dict(act)
                                        act.pop("evaluate", None)
                                        act[tool_name] = params
                                        print(f"[WARN] Mode=wechat, unwrapped evaluate → {tool_name}")
                                        break
                                except Exception:
                                    pass

            # 9) 修复 ask_human_for_intervention: Qwen 常输出字符串而非 {reason:...}
            if "ask_human_for_intervention" in act:
                inner = act["ask_human_for_intervention"]
                if isinstance(inner, str):
                    act = dict(act)
                    act["ask_human_for_intervention"] = {"reason": inner}
                    print("[WARN] Auto-wrapped ask_human_for_intervention string → {reason:...}")
                elif isinstance(inner, dict) and "reason" not in inner:
                    act = dict(act)
                    act["ask_human_for_intervention"] = {"reason": "需要人工处理"}
                elif not inner or inner == {}:
                    act["ask_human_for_intervention"] = {"reason": "需要人工处理"}

            # 10) 检查 action key 是否合法
            act_keys = set(act.keys())
            known_keys = act_keys & ALL_KNOWN_KEYS
            if not known_keys:
                # 没有任何已知 action key → 跳过
                print(f"[WARN] Skipping unknown action: {act}")
                continue
            # 只保留已知 key
            if len(known_keys) < len(act_keys):
                unknown = act_keys - ALL_KNOWN_KEYS
                act = {k: v for k, v in act.items() if k in ALL_KNOWN_KEYS}
                print(f"[WARN] Removed unknown keys: {unknown}")

            # 11) 修复 get_playbook_selector 缺 element_description：Qwen 有时只传 page_name
            if "get_playbook_selector" in act:
                inner = act["get_playbook_selector"]
                if isinstance(inner, dict) and "element_description" not in inner:
                    # 用 page_name 生成一个合理的描述
                    page = inner.get("page_name", "")
                    inner = dict(inner)
                    inner["element_description"] = f"主要操作元素 at {page}" if page else "页面元素"
                    act = dict(act)
                    act["get_playbook_selector"] = inner
                    print(f"[WARN] Auto-filled get_playbook_selector.element_description")

            # 12) 修复 input 缺 text：Qwen 有时只传 index
            if "input" in act and isinstance(act["input"], dict):
                inner = act["input"]
                if "text" not in inner and "index" in inner:
                    inner = dict(inner)
                    inner["text"] = " "  # browser-use 不接受空字符串，用空格兜底
                    act = dict(act)
                    act["input"] = inner
                    print("[WARN] Auto-filled input.text with default")

            # 12b) 修复 generate_and_insert_svg_image 裸字符串：Qwen 常输出字符串而非 dict
            #      同时将旧工具名映射为新工具名 generate_and_paste_image
            if "generate_and_insert_svg_image" in act:
                inner = act.pop("generate_and_insert_svg_image")
                if isinstance(inner, str):
                    inner = {"article_topic": inner}
                    print("[WARN] Auto-wrapped generate_and_insert_svg_image string → dict")
                # 旧名 → 新名映射
                act["generate_and_paste_image"] = inner
                print("[WARN] Mapped old tool name generate_and_insert_svg_image → generate_and_paste_image")

            # 12c) 修复 generate_and_paste_image 裸字符串
            if "generate_and_paste_image" in act:
                inner = act["generate_and_paste_image"]
                if isinstance(inner, str):
                    act = dict(act)
                    act["generate_and_paste_image"] = {"article_topic": inner}
                    print("[WARN] Auto-wrapped generate_and_paste_image string → dict")

            # 12d) 修复 zhihu_body_input_with_image：确保有 html_content 和 article_topic
            if "zhihu_body_input_with_image" in act:
                inner = act["zhihu_body_input_with_image"]
                if isinstance(inner, str):
                    # 裸字符串 → 当作 html_content
                    act = dict(act)
                    act["zhihu_body_input_with_image"] = {"html_content": inner, "article_topic": ""}
                    print("[WARN] Auto-wrapped zhihu_body_input_with_image string → dict")
                elif isinstance(inner, dict):
                    if "html_content" not in inner:
                        # 尝试用 content / text / body 等常见别名
                        for alias in ("content", "text", "body", "body_content"):
                            if alias in inner:
                                inner["html_content"] = inner.pop(alias)
                                break
                        else:
                            inner["html_content"] = "<p>文章内容</p>"
                        print("[WARN] Auto-filled zhihu_body_input_with_image.html_content")
                    if "article_topic" not in inner:
                        inner["article_topic"] = ""
                        print("[WARN] Auto-filled zhihu_body_input_with_image.article_topic")
                elif not inner or inner == {}:
                    act["zhihu_body_input_with_image"] = {"html_content": "<p>文章内容</p>", "article_topic": ""}
                    print("[WARN] Auto-filled empty zhihu_body_input_with_image")

            # 12e) 修复 zhihu_body_input：确保有 html_content
            if "zhihu_body_input" in act:
                inner = act["zhihu_body_input"]
                if isinstance(inner, str):
                    act = dict(act)
                    act["zhihu_body_input"] = {"html_content": inner}
                    print("[WARN] Auto-wrapped zhihu_body_input string → dict")
                elif isinstance(inner, dict) and "html_content" not in inner:
                    for alias in ("content", "text", "body"):
                        if alias in inner:
                            inner["html_content"] = inner.pop(alias)
                            break
                    else:
                        inner["html_content"] = "<p>文章内容</p>"
                    print("[WARN] Auto-filled zhihu_body_input.html_content")
                elif not inner or inner == {}:
                    act["zhihu_body_input"] = {"html_content": "<p>文章内容</p>"}
                    print("[WARN] Auto-filled empty zhihu_body_input")

            # 6) 修复 execute_playwright_action：确保有 selector 字段
            if "execute_playwright_action" in act:
                inner = act["execute_playwright_action"]
                if isinstance(inner, dict):
                    # 去除多余字段（params 不在 schemas 中）
                    inner.pop("params", None)
                    if not inner.get("selector"):
                        inner["selector"] = "body"
                    if "action" not in inner:
                        inner["action"] = "click"
                    act["execute_playwright_action"] = inner

            # 7) 修复 generate_and_paste_image：确保有 article_topic
            if "generate_and_paste_image" in act:
                inner = act["generate_and_paste_image"]
                if isinstance(inner, dict) and "article_topic" not in inner:
                    inner["article_topic"] = "AI Agent Trends"
                elif not inner or inner == {}:
                    act["generate_and_paste_image"] = {"article_topic": "AI Trends"}

            # 8) 修复 ask_human_for_intervention：确保有 reason
            if "ask_human_for_intervention" in act:
                inner = act["ask_human_for_intervention"]
                if isinstance(inner, dict) and "reason" not in inner:
                    inner["reason"] = "需要人工处理"
                elif not inner or inner == {}:
                    act["ask_human_for_intervention"] = {"reason": "需要人工处理"}

            # 9) GLM 常把 write_file 写成 replace_file → 自动修正
            if "replace_file" in act and isinstance(act["replace_file"], dict):
                inner = act["replace_file"]
                # replace_file 需要 old_str/new_str，如果传了 file_name/content → 应该是 write_file
                if ("file_name" in inner or "content" in inner) and "old_str" not in inner:
                    # 保留 file_name + content，转换为 write_file
                    new_inner = {}
                    if "file_name" in inner:
                        new_inner["file_name"] = inner["file_name"]
                    if "content" in inner:
                        new_inner["content"] = inner["content"]
                    act = dict(act)
                    del act["replace_file"]
                    act["write_file"] = new_inner
                    print("[WARN] Auto-converted replace_file → write_file")

            # 12c) 修复 done 缺 text：单引号导致 JSON 解析丢失 text 字段时自动填充
            if "done" in act and isinstance(act["done"], dict):
                done_inner = act["done"]
                if "text" not in done_inner or not done_inner.get("text"):
                    done_inner = dict(done_inner)
                    done_inner["text"] = data.get("memory", data.get("thinking", "任务完成"))
                    act = dict(act)
                    act["done"] = done_inner
                    print("[WARN] Auto-filled missing done.text")

            cleaned.append(act)

        # 10) 清理后如果 action 为空 → 用 wait 兜底（不用 done，避免 Agent 提前终止）
        if not cleaned:
            cleaned = [{"wait": {"seconds": 2}}]

        data["action"] = cleaned
        return data

    @staticmethod
    def _make_default_output(output_format, msg: str = "Waiting as fallback") -> Any:
        """构造默认兜底输出"""
        data = {
            'action': [{'wait': {'seconds': 2}}],
            'thinking': msg,
            'evaluation_previous_goal': '',
            'memory': '',
            'next_goal': 'Wait and observe'
        }
        return output_format.model_validate(data)

    @staticmethod
    def _parse_json_output(text: str, output_format):
        """从 LLM 文本回复中提取 JSON 并用 Pydantic 模型校验"""
        # 处理空响应
        if not text or not text.strip():
            print("[WARN] LLM returned empty response, injecting default wait")
            return BridgeLLM._make_default_output(output_format, "Auto-recovered from empty LLM response")

        # 调试：打印 LLM 原始回复的前 800 字符
        print(f"\n[DEBUG] LLM raw response ({len(text)} chars):\n{text[:800]}\n")

        # 0) 先尝试 GLM-5.1 原生 <tool_call> XML 格式
        if '<tool_call>' in text:
            data = BridgeLLM._parse_tool_call_xml(text)
            if data:
                print(f"[DEBUG] Parsed via tool_call XML: {len(data['action'])} actions")
                data = BridgeLLM._sanitize_actions(data)
                return output_format.model_validate(data)

        candidate = BridgeLLM._extract_json_candidate(text)

        # 1a) 修复重复 "action" key：LLM (Qwen/GLM) 有时输出两个 action 字段
        #      Python json.loads 只保留最后一个，通常是不完整的
        #      检测并合并所有 action 数组
        candidate = BridgeLLM._merge_duplicate_action_keys(candidate)

        # 1) 预修复：修复 GLM-5.1 奇葩 JSON 语法
        candidate = BridgeLLM._pre_repair_json(candidate)

        try:
            data = json_repair.loads(candidate)
        except Exception:
            print(f"[ERROR] json_repair failed on ({len(candidate)} chars): {candidate[:500]}")
            return BridgeLLM._make_default_output(output_format, "Auto-recovered from unparseable JSON")

        if not isinstance(data, dict):
            # 特殊兜底：如果解析结果是 list（如 LLM 输出 ["wait(3)] 截断）
            # 尝试把 list 当作 action 注入到默认 dict
            if isinstance(data, list) and data:
                print(f"[WARN] LLM returned list, attempting to inject as action")
                data = {
                    "action": data,
                    "thinking": "Recovered from list output",
                    "evaluation_previous_goal": "",
                    "memory": "",
                    "next_goal": ""
                }
                data = BridgeLLM._sanitize_actions(data)
                return output_format.model_validate(data)
            print(f"[WARN] LLM returned non-dict ({type(data).__name__}), injecting default wait")
            return BridgeLLM._make_default_output(output_format, "Auto-recovered from non-dict response")

        # 清理 action 格式（修复 GLM-5.1 常见的格式偏差）
        data = BridgeLLM._sanitize_actions(data)

        # 如果没有有效的 action，注入一个合理的默认动作
        if not isinstance(data.get('action'), list) or not data['action']:
            print("[WARN] No valid actions in LLM output, injecting default: wait(2)")
            data['action'] = [{"wait": {"seconds": 2}}]
            if not data.get('thinking'):
                data['thinking'] = 'Waiting to observe page state'
            if not data.get('evaluation_previous_goal'):
                data['evaluation_previous_goal'] = ''
            if not data.get('memory'):
                data['memory'] = ''
            if not data.get('next_goal'):
                data['next_goal'] = 'Wait and observe page state'
        elif not any(isinstance(a, dict) and a for a in data['action']):
            print("[WARN] All actions empty, injecting default: wait(2)")
            data['action'] = [{"wait": {"seconds": 2}}]

        try:
            return output_format.model_validate(data)
        except Exception as e:
            # 如果 model_validate 失败，尝试仅对 action 字段做容错
            errors = e.errors() if hasattr(e, 'errors') else []
            action_field = next((err for err in errors if 'action' in str(err.get('loc', []))), None)
            if action_field and isinstance(data, dict) and 'action' not in data:
                # LLM 没有输出 action 字段 → 填入一个占位 done 动作
                data['action'] = [{"done": {"text": "任务因格式错误终止", "success": False}}]
                data['thinking'] = data.get('thinking', '(auto-repaired)')
                data['evaluation_previous_goal'] = data.get('evaluation_previous_goal', '')
                data['memory'] = data.get('memory', '')
                data['next_goal'] = data.get('next_goal', '')
                return output_format.model_validate(data)

            print(f"[ERROR] Pydantic validation failed. Parsed data: {json.dumps(data, ensure_ascii=False)[:500]}")
            raise


def get_llm():
    """创建并返回配置好的 LLM 实例（browser-use 兼容包装）"""
    from langchain_openai import ChatOpenAI
    
    # 运行时验证配置（导入时不验证，允许预加载模块）
    _validate_config()

    inner = ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=0.3,
        timeout=180,
        max_retries=2,
        max_tokens=8192,
    )
    return BridgeLLM(inner)


def get_raw_llm():
    """返回原生 LangChain ChatOpenAI 实例（用于 ReAct 等非 browser-use 场景）"""
    from langchain_openai import ChatOpenAI
    
    _validate_config()
    
    return ChatOpenAI(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        temperature=0.3,
        timeout=180,
        max_retries=2,
        max_tokens=8192,
        streaming=True,
    )


# ==========================================
# 浏览器配置（使用本机 Edge）
# ==========================================
EDGE_EXECUTABLE_PATH = os.getenv(
    "EDGE_EXECUTABLE_PATH",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
)
# 默认使用项目本地 profile 持久化 cookie，避免每次都是"隐私模式"
_DEFAULT_PROFILE = str(Path(__file__).parent / "browser_profile")
EDGE_USER_DATA_DIR = os.getenv("EDGE_USER_DATA_DIR") or _DEFAULT_PROFILE
