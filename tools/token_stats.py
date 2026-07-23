"""Token 消耗统计模块

统一统计三条链路（知乎/WPS/微信）的 LLM token 消耗：
- 知乎链路（browser-use）：从 Agent history.usage 获取
- WPS/微信链路（LangChain ReAct）：从 AIMessage.usage_metadata 累加

用法：
    from tools.token_stats import TokenStatsCollector, print_token_stats

    # 方式1：LangChain ReAct（WPS/微信）
    collector = TokenStatsCollector()
    # 在 astream/ainvoke 中收集
    for chunk in agent_graph.astream(...):
        collector.collect_from_chunk(chunk)
    print_token_stats(collector.summary)

    # 方式2：browser-use（知乎）
    # 直接从 history.usage 构建 summary
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ModelUsage:
    """单个模型的 token 用量"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    invocations: int = 0
    cached_tokens: int = 0


@dataclass
class TokenSummary:
    """整体 token 消耗汇总"""
    total_prompt: int = 0
    total_completion: int = 0
    total_tokens: int = 0
    total_cached: int = 0
    total_cost: float = 0.0
    by_model: dict[str, ModelUsage] = field(default_factory=dict)
    source: str = ""  # "browser-use" | "langchain"

    def add_model_usage(self, model: str, prompt: int, completion: int,
                        total: int = 0, cached: int = 0):
        if model not in self.by_model:
            self.by_model[model] = ModelUsage()
        m = self.by_model[model]
        m.prompt_tokens += prompt
        m.completion_tokens += completion
        m.total_tokens += total or (prompt + completion)
        m.cached_tokens += cached
        m.invocations += 1

        self.total_prompt += prompt
        self.total_completion += completion
        self.total_tokens += total or (prompt + completion)
        self.total_cached += cached


class TokenStatsCollector:
    """LangChain ReAct Agent 流式/非流式 token 收集器

    从 agent_graph.astream(stream_mode="updates") 的 chunk 或
    agent_graph.ainvoke() 的 result 中提取 AIMessage.usage_metadata
    累加统计。
    """

    def __init__(self, model_name: str = ""):
        self.model_name = model_name
        self.summary = TokenSummary(source="langchain")

    def _extract_from_message(self, msg) -> bool:
        """从单条 AIMessage 提取 usage_metadata，返回是否成功提取"""
        # LangChain AIMessage.usage_metadata 格式：
        # {"input_tokens": N, "output_tokens": N, "total_tokens": N,
        #  "input_token_details": {"cache_read": N}}
        um = getattr(msg, "usage_metadata", None)
        if not um:
            # 尝试 response_metadata → token_usage
            rm = getattr(msg, "response_metadata", {})
            if isinstance(rm, dict):
                tu = rm.get("token_usage", {})
                if tu and isinstance(tu, dict):
                    model = tu.get("model", self.model_name or "unknown")
                    self.summary.add_model_usage(
                        model=model,
                        prompt=tu.get("prompt_tokens", 0),
                        completion=tu.get("completion_tokens", 0),
                        total=tu.get("total_tokens", 0),
                    )
                    return True
            return False

        model = self.model_name or "unknown"
        # 尝试从 response_metadata 获取 model 名
        rm = getattr(msg, "response_metadata", {})
        if isinstance(rm, dict):
            model = rm.get("model_name", rm.get("model", model))

        cached = 0
        itd = um.get("input_token_details")
        if isinstance(itd, dict):
            cached = itd.get("cache_read", 0)

        self.summary.add_model_usage(
            model=model,
            prompt=um.get("input_tokens", 0),
            completion=um.get("output_tokens", 0),
            total=um.get("total_tokens", 0),
            cached=cached,
        )
        return True

    def collect_from_chunk(self, chunk: dict) -> int:
        """从 astream(stream_mode='updates') 的单个 chunk 中提取 token

        chunk 格式：{node_name: {"messages": [AIMessage, ToolMessage, ...]}}
        返回本次提取到的消息数
        """
        count = 0
        for node_name, node_output in chunk.items():
            if not isinstance(node_output, dict):
                continue
            msgs = node_output.get("messages", [])
            if not isinstance(msgs, list):
                continue
            for msg in msgs:
                # 只从 AIMessage 提取（ToolMessage 没有 usage）
                msg_type = getattr(msg, "type", "")
                if msg_type != "ai":
                    continue
                if self._extract_from_message(msg):
                    count += 1
        return count

    def collect_from_result(self, result: dict) -> int:
        """从 ainvoke() 的返回结果中提取 token

        result 格式：{"messages": [AIMessage, ToolMessage, ...]}
        返回本次提取到的消息数
        """
        count = 0
        msgs = result.get("messages", []) if isinstance(result, dict) else []
        for msg in msgs:
            msg_type = getattr(msg, "type", "")
            if msg_type != "ai":
                continue
            if self._extract_from_message(msg):
                count += 1
        return count


def print_token_stats(summary: TokenSummary):
    """打印 token 统计信息"""
    print(f"\n{'='*60}")
    print(f"📊 Token 使用统计 ({summary.source})")
    print(f"{'='*60}")

    if summary.total_tokens == 0:
        print("  ⚠️ 未收集到 token 数据（可能 LLM 未返回 usage 信息）")
        print(f"{'='*60}\n")
        return

    print(f"  输入 tokens:  {summary.total_prompt:,}")
    if summary.total_cached > 0:
        print(f"  缓存 tokens:  {summary.total_cached:,}")
    print(f"  输出 tokens:  {summary.total_completion:,}")
    print(f"  总 tokens:    {summary.total_tokens:,}")

    if summary.by_model:
        for model, stats in summary.by_model.items():
            print(f"  --- {model} ---")
            print(f"    输入: {stats.prompt_tokens:,} | 输出: {stats.completion_tokens:,} | "
                  f"总计: {stats.total_tokens:,} | 调用次数: {stats.invocations}")
            if stats.cached_tokens > 0:
                print(f"    缓存命中: {stats.cached_tokens:,}")

    print(f"{'='*60}\n")
