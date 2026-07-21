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
    "txt",
]

WECHAT_KEYWORDS = [
    "微信", "wechat", "WeChat",
    "服务号", "公众号", "关注",
    "私信", "发消息", "发微信",
]


def _route_intent(text: str) -> str:
    """返回 "zhihu" | "wps" | "wechat" """
    # 微信特征词优先
    if any(kw in text for kw in WECHAT_KEYWORDS) and any(kw in text for kw in ["微信", "wechat", "WeChat"]):
        return "wechat"
    # WPS 特征词
    if any(kw in text for kw in WPS_KEYWORDS):
        return "wps"
    # 知乎特征词
    if any(kw in text for kw in ["知乎", "zhihu", "浏览", "搜索", "评论", "收藏", "点赞"]):
        return "zhihu"
    # 默认知乎
    return "zhihu"


async def run_zhihu(user_task: str):
    from agent.core import create_zhihu_agent
    print(f"==> 知乎模式 | browser-use Agent | 你说: {user_task}")
    agent = await create_zhihu_agent(user_task)
    print("==> 浏览器已启动，Agent 开始执行...")
    history = await agent.run()
    print(f"==> 完成！共 {len(history)} 步。")
    return history


async def run_wps(user_task: str):
    from agent.core import create_wps_agent
    print(f"==> WPS 模式 | LangChain ReAct Agent | 你说: {user_task}")
    agent_graph, task = await create_wps_agent(user_task)
    result = await agent_graph.ainvoke({"messages": [{"role": "user", "content": task}]})
    # 提取最后一条 AI 消息作为结果
    output = "无输出"
    if "messages" in result:
        for msg in reversed(result["messages"]):
            if hasattr(msg, "content") and msg.type == "ai" and msg.content:
                output = msg.content
                break
    print(f"==> 完成！\n{output}")
    return result


async def run_wechat(user_task: str):
    from agent.core import create_wechat_agent
    print(f"\n{'='*60}")
    print(f"==> 微信模式 | LangChain ReAct Agent")
    print(f"==> 你说: {user_task}")
    print(f"{'='*60}\n")

    agent_graph, task = await create_wechat_agent(user_task)

    step = 0
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
                            print(f"\\n{'─'*50}")
                            print(f"[Step {step}] LLM → {tc['name']}")
                            args_str = ", ".join(f"{k}={repr(v)[:80]}" for k, v in tc.get("args", {}).items())
                            print(f"        参数: {args_str}")
                            print(f"{'─'*50}") 
                    elif hasattr(msg, "type") and msg.type == "tool":
                        # 工具返回结果
                        content = str(msg.content)
                        # 截断过长的 OCR dump
                        if len(content) > 600:
                            content = content[:600] + "\n... (截断)"
                        print(f"        返回: {content}")
                    else:
                        # LLM 文本输出
                        print(f"\\n{'─'*50}")
                        print(f"[Step {step}] LLM 思考:")
                        print(f"{msg.content}")
                        print(f"{'─'*50}")

    print(f"\\n{'='*60}")
    print(f"==> Agent 执行完毕")
    print(f"==> 共 {step} 步")
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
