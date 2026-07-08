import os
import sys
import asyncio
from pathlib import Path

# 禁用 browser-use 自动下载第三方扩展（避免超时）
os.environ["BROWSER_USE_DISABLE_EXTENSIONS"] = "true"

from agent.core import create_zhihu_agent


async def main():
    if len(sys.argv) > 1:
        user_task = " ".join(sys.argv[1:])
    else:
        user_task = (
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
    
    print("==> 正在启动 Zhihu-Agent-Playbook...")
    agent = await create_zhihu_agent(user_task)
    print("==> 浏览器已启动，Agent 开始执行任务...")
    history = await agent.run()
    print(f"==> 执行完成！共 {len(history)} 步。")
    return history


if __name__ == "__main__":
    asyncio.run(main())
