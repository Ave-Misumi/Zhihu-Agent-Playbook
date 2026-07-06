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
            "严格执行以下7步，必须按顺序逐一完成，完成后立即 done：\n"
            "Step1: 登录知乎\n"
            "Step2: 打开写文章页面\n"
            "Step3: 标题「2026年AI Agent发展趋势」，LLM自创一段100字短文作为正文，input 填入\n"
            "Step4: 调用 generate_and_insert_svg_image 插入配图\n"
            "Step5: 点击发布\n"
            "Step6: 回到知乎首页，搜索框搜索文章标题，找到刚发布的文章，\n"
            "        搜不到则直接用 navigate 跳转到已发布文章URL\n"
            "Step7: 对文章发表一条评论 + 点击收藏（跳过点赞）→ 立即 done(success=true)\n"
            "⚠️ 必须按以上顺序执行！禁止在搜索前做评论/收藏。完成后立即 done，不要继续浏览。"
        )
    
    print("==> 正在启动 Zhihu-Agent-Playbook...")
    agent = await create_zhihu_agent(user_task)
    print("==> 浏览器已启动，Agent 开始执行任务...")
    history = await agent.run()
    print(f"==> 执行完成！共 {len(history)} 步。")
    return history


if __name__ == "__main__":
    asyncio.run(main())
