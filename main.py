"""Zhihu-Agent-Playbook 主入口 — 支持知乎自动化与 WPS 文档生成两条链路

用法:
    python main.py                    # 知乎完整流程（默认）
    python main.py 帮我写篇AI Agent的文章   # WPS 链路：自然语言驱动
    python main.py --zhihu            # 知乎（显式指定）

WPS 链路示例（自然语言，跟平时说话一样）：
    python main.py 帮我写一篇关于2026年AI发展趋势的文章排版导出PDF
    python main.py 用WPS写个工作周报，要包含本周进展和下周计划
    python main.py 写文章 主题是深度学习入门 加标题字体排版 导出pdf
    python main.py 生成一篇个人年终总结，要有目录结构，存成word和pdf
"""
import os
import sys
import asyncio

os.environ["BROWSER_USE_DISABLE_EXTENSIONS"] = "true"


async def run_zhihu(custom_task: str | None = None):
    from agent.core import create_zhihu_agent

    task = custom_task or (
        "按顺序执行：\n"
        "1. 登录知乎\n"
        "2. 打开写文章 → 关弹窗 → 标题「2026年AI Agent发展趋势」→ LLM自创100字短文(input填入) → "
        "调用 generate_and_insert_svg_image → 点发布\n"
        "3. 关发布成功弹窗 → ⚠️ navigate 到 zhihu.com 首页 → 搜索「2026年AI Agent发展趋势」→ 找到文章\n"
        "   （搜不到就直接 navigate 到文章URL）\n"
        "4. 评论 + 收藏（跳过点赞）\n"
        "5. done(success=true)\n"
        "⚠️ 第3步必须先 navigate 回首页！关闭发布弹窗后你还在编辑页，不是文章页。严禁在 navigate 之前评论。"
    )
    print("==> 正在启动 Zhihu-Agent-Playbook (知乎模式)...")
    agent = await create_zhihu_agent(task)
    print("==> 浏览器已启动，Agent 开始执行任务...")
    history = await agent.run()
    print(f"==> 执行完成！共 {len(history)} 步。")
    return history


async def run_wps(user_task: str):
    """WPS Agent 链路：自然语言 → Agent 理解意图 → 调用 wps_create_document_and_export_pdf"""
    from agent.core import create_wps_agent

    print(f"==> 正在启动 WPS 文档助手...")
    print(f"==> 用户说: {user_task}")
    agent = await create_wps_agent(user_task)
    history = await agent.run()
    print(f"==> 执行完成！共 {len(history)} 步。")
    return history


def _is_wps_intent(args: list[str]) -> bool:
    """判断是否为 WPS 链路：非 --zhihu 且参数看起来像自然语言"""
    if "--zhihu" in args:
        return False
    # 用户给了非 flag 参数 → 按自然语言意图处理，走 WPS
    return any(a for a in args if not a.startswith("--"))


async def main():
    args = sys.argv[1:]

    if "--zhihu" in args:
        extra_args = [a for a in args if a != "--zhihu"]
        task = " ".join(extra_args) if extra_args else None
        await run_zhihu(task)
    elif _is_wps_intent(args):
        user_task = " ".join(args)
        await run_wps(user_task)
    else:
        await run_zhihu()


if __name__ == "__main__":
    asyncio.run(main())
