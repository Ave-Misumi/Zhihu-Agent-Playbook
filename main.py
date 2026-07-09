"""Zhihu-Agent-Playbook 主入口

用法:
    python main.py                                                 # 知乎：发文章 + 评论 + 收藏
    python main.py "帮我在知乎上搜一下AI Agent相关的文章"            # 知乎：自定义任务
    python main.py "帮我写篇AI文章排版导出PDF"                       # WPS：自然语言驱动
    python main.py "用WPS写个工作周报，要有本周进展和下周计划"          # WPS：各种说法都可以
    python main.py "帮我搜索微信服务号火眼审阅并关注发私信"             # 微信：自然语言驱动
    python main.py "打开微信搜索服务号火眼审阅，关注后发私信：你好，这是一条测试消息"  # 微信：完整链路+指定私信内容

启动时自动判断：用户自然语言属于哪种意图 → 路由到对应链路（知乎 / WPS / 微信）。
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
    # 微信特征词 → 优先（「搜索微信xxx」不能因为「搜索」被路由到知乎）
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
    print(f"==> 知乎模式 | 你说: {user_task}")
    agent = await create_zhihu_agent(user_task)
    print("==> 浏览器已启动，Agent 开始执行...")
    history = await agent.run()
    print(f"==> 完成！共 {len(history)} 步。")
    return history


async def run_wps(user_task: str):
    from agent.core import create_wps_agent
    print(f"==> WPS 模式 | 你说: {user_task}")
    agent = await create_wps_agent(user_task)
    history = await agent.run()
    print(f"==> 完成！共 {len(history)} 步。")
    return history


async def run_wechat(user_task: str):
    from agent.core import create_wechat_agent
    print(f"==> 微信模式 | 你说: {user_task}")
    agent = await create_wechat_agent(user_task)
    history = await agent.run()
    print(f"==> 完成！共 {len(history)} 步。")
    return history


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
