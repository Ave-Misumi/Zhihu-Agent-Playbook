import base64
import html
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult


async def generate_and_insert_svg_image(
    browser_session: BrowserSession,
    article_topic: str
) -> ActionResult:
    """根据文章主题生成一张信息图风格的 SVG 配图，注入知乎编辑器"""
    page = await browser_session.get_current_page()
    safe_topic = html.escape(article_topic)

    svg_code = f"""<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#667eea"/>
      <stop offset="100%" style="stop-color:#764ba2"/>
    </linearGradient>
    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:#f093fb"/>
      <stop offset="100%" style="stop-color:#f5576c"/>
    </linearGradient>
    <filter id="shadow" x="-5%" y="-5%" width="110%" height="110%">
      <feDropShadow dx="2" dy="4" stdDeviation="6" flood-color="#00000033"/>
    </filter>
  </defs>

  <!-- 背景 -->
  <rect width="720" height="420" rx="16" fill="url(#bg)"/>

  <!-- 装饰圆 -->
  <circle cx="620" cy="80" r="120" fill="#ffffff10"/>
  <circle cx="100" cy="350" r="60" fill="#ffffff10"/>
  <circle cx="660" cy="360" r="40" fill="#ffffff08"/>

  <!-- 装饰线 -->
  <line x1="40" y1="180" x2="680" y2="180" stroke="#ffffff25" stroke-width="1"/>
  <line x1="40" y1="260" x2="680" y2="260" stroke="#ffffff15" stroke-width="1"/>

  <!-- Icon: 芯片/AI 图标 -->
  <g transform="translate(360,75)">
    <rect x="-28" y="-20" width="56" height="40" rx="4" fill="none" stroke="#ffffff90" stroke-width="2.5"/>
    <rect x="-20" y="-12" width="16" height="10" rx="1" fill="#f5576c" opacity="0.9"/>
    <rect x="4" y="-12" width="16" height="10" rx="1" fill="#f093fb" opacity="0.9"/>
    <rect x="-20" y="2" width="16" height="10" rx="1" fill="#f093fb" opacity="0.9"/>
    <rect x="4" y="2" width="16" height="10" rx="1" fill="#f5576c" opacity="0.9"/>
    <!-- 连接线 -->
    <line x1="-12" y1="-6" x2="12" y2="-6" stroke="#ffffff50" stroke-width="1"/>
    <line x1="-12" y1="8" x2="12" y2="8" stroke="#ffffff50" stroke-width="1"/>
  </g>

  <!-- 主标题 -->
  <text x="360" y="145" text-anchor="middle" font-family="'Microsoft YaHei','PingFang SC',sans-serif"
        font-size="28" font-weight="bold" fill="#ffffff" filter="url(#shadow)">{safe_topic}</text>

  <!-- 副标题 -->
  <text x="360" y="195" text-anchor="middle" font-family="'Microsoft YaHei','PingFang SC',sans-serif"
        font-size="16" fill="#ffffffcc">从工具到伙伴 —— 2026 AI Agent 范式革命</text>

  <!-- 底部三个信息块 -->
  <g transform="translate(80, 285)">
    <rect x="0" y="0" width="160" height="70" rx="10" fill="#ffffff15"/>
    <text x="80" y="22" text-anchor="middle" font-family="Arial,sans-serif" font-size="22"
          font-weight="bold" fill="#f093fb">多Agent协作</text>
    <text x="80" y="48" text-anchor="middle" font-family="'Microsoft YaHei',sans-serif" font-size="13"
          fill="#ffffffcc">专业化分工 × 自主协商</text>
  </g>

  <g transform="translate(280, 285)">
    <rect x="0" y="0" width="160" height="70" rx="10" fill="#ffffff15"/>
    <text x="80" y="22" text-anchor="middle" font-family="Arial,sans-serif" font-size="22"
          font-weight="bold" fill="#ffd700">人机融合</text>
    <text x="80" y="48" text-anchor="middle" font-family="'Microsoft YaHei',sans-serif" font-size="13"
          fill="#ffffffcc">意图理解 × 主动建议</text>
  </g>

  <g transform="translate(480, 285)">
    <rect x="0" y="0" width="160" height="70" rx="10" fill="#ffffff15"/>
    <text x="80" y="22" text-anchor="middle" font-family="Arial,sans-serif" font-size="22"
          font-weight="bold" fill="#00e5ff">安全治理</text>
    <text x="80" y="48" text-anchor="middle" font-family="'Microsoft YaHei',sans-serif" font-size="13"
          fill="#ffffffcc">权限控制 × 行为审计</text>
  </g>

  <!-- 底部标签 -->
  <text x="360" y="395" text-anchor="middle" font-family="'Microsoft YaHei',sans-serif"
        font-size="11" fill="#ffffff60">2026 AI AGENT TRENDS · INFOGRAPHIC</text>
</svg>"""

    b64_svg = base64.b64encode(svg_code.encode('utf-8')).decode('utf-8')
    data_url = f"data:image/svg+xml;base64,{b64_svg}"

    # 尝试知乎编辑器插入；失败则回退到通用 contenteditable
    js_script = """
    (imgSrc) => {
        const selectors = [
            '.PublicDraftEditor-content[contenteditable="true"]',
            '.DraftEditor-root [contenteditable="true"]',
            '[contenteditable="true"]'
        ];
        for (const sel of selectors) {
            const editor = document.querySelector(sel);
            if (editor) {
                const img = document.createElement('img');
                img.src = imgSrc;
                img.style.maxWidth = '100%';
                img.style.marginTop = '16px';
                img.style.marginBottom = '16px';
                img.style.borderRadius = '8px';
                // 确保光标在末尾
                const lastChild = editor.lastChild;
                if (lastChild) {
                    lastChild.after(img);
                } else {
                    editor.appendChild(img);
                }
                editor.dispatchEvent(new Event('input', { bubbles: true }));
                return "图片插入成功 => " + sel;
            }
        }
        return "未找到编辑器，已跳过配图插入";
    }
    """
    result = await page.evaluate(js_script, data_url)
    return ActionResult(extracted_content=f"配图插入结果: {result}")
