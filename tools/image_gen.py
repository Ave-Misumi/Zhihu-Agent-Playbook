"""生成 SVG 配图，通过构造 ClipboardEvent + dispatchEvent 注入 Draft.js，无需剪贴板权限"""
import base64
import html
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult

SVG_TEMPLATE = """<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg"><defs><linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%"><stop offset="0%" style="stop-color:#667eea"/><stop offset="100%" style="stop-color:#764ba2"/></linearGradient><filter id="shadow"><feDropShadow dx="2" dy="4" stdDeviation="6" flood-color="#00000033"/></filter></defs><rect width="720" height="420" rx="16" fill="url(#bg)"/><circle cx="620" cy="80" r="120" fill="#ffffff10"/><circle cx="100" cy="350" r="60" fill="#ffffff10"/><g transform="translate(360,75)"><rect x="-28" y="-20" width="56" height="40" rx="4" fill="none" stroke="#ffffff90" stroke-width="2.5"/><rect x="-20" y="-12" width="16" height="10" rx="1" fill="#f5576c" opacity="0.9"/><rect x="4" y="-12" width="16" height="10" rx="1" fill="#f093fb" opacity="0.9"/><rect x="-20" y="2" width="16" height="10" rx="1" fill="#f093fb" opacity="0.9"/><rect x="4" y="2" width="16" height="10" rx="1" fill="#f5576c" opacity="0.9"/></g><text x="360" y="145" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="28" font-weight="bold" fill="#ffffff" filter="url(#shadow)">{topic}</text><text x="360" y="195" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="16" fill="#ffffffcc">从工具到伙伴 —— 2026 AI Agent 范式革命</text><line x1="40" y1="180" x2="680" y2="180" stroke="#ffffff25" stroke-width="1"/><g transform="translate(80,285)"><rect x="0" y="0" width="160" height="70" rx="10" fill="#ffffff15"/><text x="80" y="22" text-anchor="middle" font-family="Arial,sans-serif" font-size="22" font-weight="bold" fill="#f093fb">多Agent协作</text><text x="80" y="48" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13" fill="#ffffffcc">专业化分工 × 自主协商</text></g><g transform="translate(280,285)"><rect x="0" y="0" width="160" height="70" rx="10" fill="#ffffff15"/><text x="80" y="22" text-anchor="middle" font-family="Arial,sans-serif" font-size="22" font-weight="bold" fill="#ffd700">人机融合</text><text x="80" y="48" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13" fill="#ffffffcc">意图理解 × 主动建议</text></g><g transform="translate(480,285)"><rect x="0" y="0" width="160" height="70" rx="10" fill="#ffffff15"/><text x="80" y="22" text-anchor="middle" font-family="Arial,sans-serif" font-size="22" font-weight="bold" fill="#00e5ff">安全治理</text><text x="80" y="48" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13" fill="#ffffffcc">权限控制 × 行为审计</text></g><text x="360" y="395" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#ffffff60">2026 AI AGENT TRENDS · INFOGRAPHIC</text></svg>"""


async def generate_and_insert_svg_image(
    browser_session: BrowserSession,
    article_topic: str
) -> ActionResult:
    """构造 ClipboardEvent + dispatchEvent 注入 SVG，不碰系统剪贴板。"""
    page = await browser_session.get_current_page()
    svg_code = SVG_TEMPLATE.replace("{topic}", html.escape(article_topic))
    svg_b64 = base64.b64encode(svg_code.encode("utf-8")).decode("utf-8")
    svg_data_url = f"data:image/svg+xml;base64,{svg_b64}"

    # browser-use page.evaluate 要求 JS 必须是 (...args) => 箭头函数格式
    paste_js = """(svgUrl) => {
        const ed = document.querySelector('[contenteditable="true"]');
        if (!ed) return 'editor_not_found';
        ed.focus();
        ed.scrollTop = ed.scrollHeight;
        const imgHtml = '<img src="' + svgUrl + '" style="max-width:100%;margin:16px 0;border-radius:8px;"/>';
        const dt = new DataTransfer();
        dt.setData('text/html', imgHtml);
        dt.setData('text/plain', '');
        const ev = new ClipboardEvent('paste', {bubbles:true,cancelable:true,clipboardData:dt});
        ed.dispatchEvent(ev);
        return new Promise(resolve => {
            setTimeout(() => {
                const imgs = ed.querySelectorAll('img');
                resolve(imgs.length>0 ? 'image_found:'+imgs.length : 'no_image:'+ed.children.length);
            }, 1200);
        });
    }"""

    result = await page.evaluate(paste_js, svg_data_url)
    return ActionResult(extracted_content=f"配图插入结果: {result}")
