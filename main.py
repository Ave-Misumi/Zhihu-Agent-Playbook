import os
import sys
import asyncio

# 禁用 browser-use 自动下载第三方扩展（避免超时）
os.environ["BROWSER_USE_DISABLE_EXTENSIONS"] = "true"

from agent.core import create_zhihu_agent


async def main():
    # 从命令行参数读取任务，或使用默认测试任务
    if len(sys.argv) > 1:
        user_task = " ".join(sys.argv[1:])
    else:
        user_task = (
            "请帮我登录知乎（如果需要扫码，请提示我），"
            "然后写一篇关于「2026年AI Agent发展趋势」的专业文章并配图发表，"
            "最后搜索这篇文章并进行评论和收藏（注意：不能给自己的文章点赞，跳过即可）。"
        )
    
    print("==> 正在启动 Zhihu-Agent-Playbook...")
    agent = await create_zhihu_agent(user_task)
    print("==> 浏览器已启动，Agent 开始执行任务...")
    history = await agent.run()
    print(f"==> 执行完成！共 {len(history)} 步。")
    return history


if __name__ == "__main__":
    asyncio.run(main())
