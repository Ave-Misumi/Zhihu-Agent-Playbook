import os
import sys
import asyncio
from pathlib import Path

# 禁用 browser-use 自动下载第三方扩展（避免超时）
os.environ["BROWSER_USE_DISABLE_EXTENSIONS"] = "true"

from agent.core import create_zhihu_agent


async def main():
    # 从命令行参数读取任务，或使用默认测试任务
    if len(sys.argv) > 1:
        user_task = " ".join(sys.argv[1:])
    else:
        user_task = (
            "任务：登录知乎 → 打开写文章 → 标题填「2026年AI Agent发展趋势」→ "
            "正文由LLM自行创作一段100字左右关于AI Agent的短文 → "
            "配图(generate_and_insert_svg_image) → 发布 → "
            "首页搜索「2026年AI Agent发展趋势」→ 找到文章 → 评论+收藏（跳过点赞）。\n"
            "搜索技巧：最多搜2次，搜不到则直接打开已发布文章页面。"
        )
    
    print("==> 正在启动 Zhihu-Agent-Playbook...")
    agent = await create_zhihu_agent(user_task)
    print("==> 浏览器已启动，Agent 开始执行任务...")
    history = await agent.run()
    print(f"==> 执行完成！共 {len(history)} 步。")
    return history


if __name__ == "__main__":
    asyncio.run(main())
