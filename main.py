"""Zhihu-Agent-Playbook 主入口

用法:
    python main.py                                          # 知乎：发文章 + 评论 + 收藏
    python main.py "帮我在知乎上搜一下AI Agent相关的文章"      # 知乎：自定义任务
    python main.py "帮我写篇AI文章排版导出PDF"                # WPS：自然语言驱动
    python main.py "用WPS写个工作周报，要有本周进展和下周计划"   # WPS：各种说法都可以

启动时自动判断：如果用户说了带「WPS」「Word」「文档」「导出 PDF」「排版」「写篇」等
关键词的自然语言请求 → 走 WPS 链路；否则走知乎链路。
"""
import os
import sys
import asyncio

os.environ["BROWSER_USE_DISABLE_EXTENSIONS"] = "true"

# 判断 WPS 意图的关键词
WPS_KEYWORDS = [
    "wps", "WPS", "word", "Word", "WORD",
    "文档", "排版", "导出pdf", "导出PDF", "导出 pdf", "导出 PDF",
    "pdf", "PDF", "docx", ".docx", "保存为",
    "写篇", "写一篇文章", "生成一篇", "生成文章",
    "txt",
]


def _is_wps_intent(text: str) -> bool:
    """启发式判断用户意图是否为 WPS（文档生成）"""
    # 显式提到知乎 → 不走 WPS
    if any(kw in text for kw in ["知乎", "zhihu", "浏览", "搜索", "评论", "收藏", "点赞"]):
        return False
    return any(kw in text for kw in WPS_KEYWORDS)


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


async def main():
    args = sys.argv[1:]

    if not args:
        # 默认：知乎发文章全流程
        user_task = (
            "帮我登录知乎，写一篇关于2026年AI Agent发展趋势的文章（配图），"
            "发布后在知乎首页搜索并找到这篇文章，给文章评论+收藏。注意不要给自己点赞。"
        )
        await run_zhihu(user_task)
        return

    user_task = " ".join(args)

    if _is_wps_intent(user_task):
        await run_wps(user_task)
    else:
        await run_zhihu(user_task)


if __name__ == "__main__":
    asyncio.run(main())
