import base64
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult


async def generate_and_insert_svg_image(
    browser_session: BrowserSession,
    article_topic: str
) -> ActionResult:
    """根据文章主题，生成一张SVG格式的配图，并将其插入到当前网页的富文本编辑器中。"""
    page = await browser_session.get_current_page()

    # 1. 根据主题生成 SVG 代码
    svg_code = f"""<svg width="600" height="400" xmlns="http://www.w3.org/2000/svg">
    <rect width="100%" height="100%" fill="#f0f8ff"/>
    <text x="50%" y="50%" font-family="Arial" font-size="32" fill="#333" text-anchor="middle" dominant-baseline="middle">{article_topic}</text>
    <circle cx="300" cy="200" r="100" fill="none" stroke="#007bff" stroke-width="5"/>
    </svg>"""

    # 2. 将 SVG 转为 Base64 Data URL
    b64_svg = base64.b64encode(svg_code.encode('utf-8')).decode('utf-8')
    data_url = f"data:image/svg+xml;base64,{b64_svg}"

    # 3. 通过 Playwright 将图片插入知乎编辑器
    js_script = """
    (imgSrc) => {
        const editor = document.querySelector('.PublicDraftEditor-content[contenteditable="true"]') || document.querySelector('[contenteditable="true"]');
        if (editor) {
            const img = document.createElement('img');
            img.src = imgSrc;
            img.style.maxWidth = '100%';
            editor.appendChild(img);
            editor.dispatchEvent(new Event('input', { bubbles: true }));
            return "图片插入成功";
        }
        return "未找到编辑器";
    }
    """
    result = await page.evaluate(js_script, data_url)
    return ActionResult(extracted_content=f"配图生成并插入结果: {result}")
