import os

# ==========================================
# LLM 接入配置 (使用 browser-use 自带的 ChatBrowserUse)
# ==========================================
# 方案A：接入本地开源模型 (如 Ollama 运行的 Hermes-3, Qwen2.5)
# OLLAMA_BASE_URL = "http://localhost:11434/v1"
# OLLAMA_MODEL = "hermes3"

# 方案B：接入云端 API (如 阿里云百炼、SiliconFlow、OpenAI)
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.siliconflow.cn")
LLM_API_KEY = os.getenv("LLM_API_KEY", "your-api-key-here")
LLM_MODEL = os.getenv("LLM_MODEL", "Qwen/Qwen2.5-72B-Instruct")


def get_llm():
    from browser_use import ChatBrowserUse
    
    return ChatBrowserUse(
        model=LLM_MODEL,
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        timeout=120.0,
    )


# ==========================================
# Playwright 内置 Chromium 浏览器配置（推荐）
# 不使用本机 Edge，避免 user data 冲突
# ==========================================
# 如果想用本机 Edge，可改为：
EDGE_EXECUTABLE_PATH = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
EDGE_USER_DATA_DIR = None  # 不复用用户数据，每次独立 profile（无扫码）
USE_BUILTIN_CHROMIUM = True  # True=使用 Playwright 内置 Chromium；False=使用本机 Edge
