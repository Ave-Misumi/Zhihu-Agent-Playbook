"""Zhihu-Agent-Playbook 主入口

知乎链路使用 browser-use Agent；WPS / 微信链路使用 LangChain ReAct Agent。

用法:
    python main.py                                                 # 知乎默认任务
    python main.py "帮我在知乎上搜一下AI Agent相关的文章"            # 知乎
    python main.py "帮我写篇AI文章排版导出PDF"                       # WPS
    python main.py "用WPS写个工作周报，要有本周进展和下周计划"          # WPS
    python main.py "帮我搜索微信服务号火眼审阅并关注发私信"             # 微信
    python main.py "打开微信搜索服务号火眼审阅，关注后发私信：你好，这是一条测试消息"  # 微信
"""
import os
import sys
import asyncio

os.environ["BROWSER_USE_DISABLE_EXTENSIONS"] = "true"

# 意图关键词
WPS_KEYWORDS = [
    "wps", "WPS", "word", "Word", "WORD",
    "文档", "排版", "导出pdf", "导出PDF", "导出 pdf", "导出 PDF",
    "pdf", "PDF", "docx", ".docx", "保存为",
    "写篇", "写一篇文章", "生成一篇", "生成文章",
    "txt"
]
WECHAT_KEYWORDS = [
    "微信", "服务号", "公众号", "发私信", "关注",
]

def _route_intent(user_task: str) -> str:
    """根据用户输入关键词路由到对应链路"""
    # 微信优先（关键词更具体）
    if any(kw in user_task for kw in WECHAT_KEYWORDS):
        return "wechat"
    if any(kw in user_task for kw in WPS_KEYWORDS):
        return "wps"
    return "zhihu"


async def run_zhihu(user_task: str):
    from agent.core import create_zhihu_agent
    agent = await create_zhihu_agent(user_task)
    history = await agent.run()
    
    # Token 使用统计
    print(f"\n{'='*60}")
    print(f"📊 Token 使用统计")
    print(f"{'='*60}")
    usage = None
    if history and history.usage:
        usage = history.usage
    else:
        # Fallback: 直接从 token_cost_service 获取
        try:
            usage = await agent.token_cost_service.get_usage_summary()
        except Exception as e:
            print(f"  无法获取 token 统计: {e}")
    
    if usage:
        print(f"  输入 tokens:  {usage.total_prompt_tokens:,}")
        if usage.total_prompt_cached_tokens > 0:
            print(f"  缓存 tokens:  {usage.total_prompt_cached_tokens:,}")
        print(f"  输出 tokens:  {usage.total_completion_tokens:,}")
        print(f"  总 tokens:    {usage.total_tokens:,}")
        if usage.total_cost and usage.total_cost > 0:
            print(f"  总费用:       ${usage.total_cost:.4f}")
        # Per-model breakdown
        if usage.by_model:
            for model, stats in usage.by_model.items():
                print(f"  --- {model} ---")
                print(f"    输入: {stats.prompt_tokens:,} | 输出: {stats.completion_tokens:,} | 总计: {stats.total_tokens:,} | 调用次数: {stats.invocations}")
    print(f"{'='*60}\n")


async def run_wps(user_task: str):
    from agent.core import create_wps_agent
    agent_graph, final_task = await create_wps_agent(user_task)
    result = await agent_graph.ainvoke({"messages": [{"role": "user", "content": final_task}]})
    msgs = result.get("messages", [])
    if msgs:
        last = msgs[-1]
        print(f"\n{'='*60}")
        print(f"==> WPS Agent 执行完毕")
        if hasattr(last, "content"):
            print(f"==> {last.content[:300]}")
        print(f"{'='*60}\n")


async def run_wechat(user_task: str):
    from agent.core import create_wechat_agent
    print(f"\n{'='*60}")
    print(f"==> 微信模式 | LangChain ReAct Agent")
    print(f"==> 你说: {user_task}")
    print(f"{'='*60}")

    agent_graph, task = await create_wechat_agent(user_task)
    print("\n[Agent] 启动中，等待 LLM 首次响应...")

    step = 0
    last_was_tool: bool = False
    async for chunk in agent_graph.astream(
        {"messages": [{"role": "user", "content": task}]},
        stream_mode="updates",
    ):
        step += 1
        for node_name, node_output in chunk.items():
            msgs = node_output.get("messages", [])
            for msg in msgs:
                if hasattr(msg, "content") and msg.content:
                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                        # LLM 决定调用工具
                        for tc in msg.tool_calls:
                            print(f"\n{'─'*50}")
                            print(f"[Step {step}] LLM => {tc['name']}")
                            args_str = ", ".join(
                                f"{k}={repr(v)[:80]}"
                                for k, v in tc.get("args", {}).items()
                            )
                            print(f"        参数: {args_str}")
                            print(f"{'─'*50}")
                            print(f"[Agent] 执行工具中...", end="", flush=True)
                            last_was_tool = True
                    elif hasattr(msg, "type") and msg.type == "tool":
                        # 工具返回结果
                        content = str(msg.content)
                        if len(content) > 500:
                            content = content[:500] + "\n... (截断)"
                        if last_was_tool:
                            print(f"\r{' '*30}")  # 清除 "执行工具中..."
                            last_was_tool = False
                        print(f"        返回: {content}")
                        print(f"[Agent] LLM 思考下一步...", end="", flush=True)
                    else:
                        # LLM 纯文本（如计划、反思）
                        text = str(msg.content)
                        if len(text) > 200:
                            text = text[:200] + "..."
                        if last_was_tool:
                            print(f"\r{' '*30}")
                            last_was_tool = False
                        print(f"[LLM] {text}")

    print(f"\n{'='*60}")
    print(f"==> Agent 执行完毕 (共 {step} 步)")
    print(f"{'='*60}\n")


async def main():
    args = sys.argv[1:]

    if not args:
        user_task = (
            "帮我登录知乎，写一篇关于2026年AI Agent发展趋势的文章（配图），"
            "发布后在知乎首页搜索并找到这篇文章，给文章评论+收藏。注意不要给自己点赞。"
        )
        await run_zhihu(user_task)
        return

    user_task = " ".join(args)
    route = _route_intent(user_task)

    if route == "wechat":
        await run_wechat(user_task)
    elif route == "wps":
        await run_wps(user_task)
    else:
        await run_zhihu(user_task)


if __name__ == "__main__":
    asyncio.run(main())
