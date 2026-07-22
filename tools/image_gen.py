"""SVG 配图生成 → PNG → 系统剪贴板 → Ctrl+V 粘贴到知乎编辑器

流程:
1. 根据文章主题/正文内容，动态生成内容相关的 SVG 配图（多种风格模板）
2. 通过浏览器 Canvas API 将 SVG 渲染为 PNG（720x420）
3. 将 PNG 写入 Windows 系统剪贴板（win32clipboard）
4. 在编辑器中触发 Ctrl+V 粘贴图片
"""
import io
import base64
import html
import random
import re
import asyncio
import win32clipboard
from PIL import Image
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult


# ═══════════════════════════════════════════════════════════
# SVG 模板库 — 多种风格，根据内容关键词智能选择
# ═══════════════════════════════════════════════════════════

def _extract_keywords(text: str, max_n: int = 6) -> list[str]:
    """从文章标题/正文中提取关键词用于配图设计"""
    # 中文词组（2-4字）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,4}', text)
    # 英文单词
    en_words = re.findall(r'[A-Za-z]{3,}', text)
    # 合并去重，保留顺序
    seen = set()
    keywords = []
    for w in cn_words + en_words:
        if w not in seen and w not in ('的', '了', '是', '在', '和', '与', '或', '一个', '可以', '我们', '你们', '他们', '这个', '那个', '什么', '怎么', '为什么', '如何'):
            seen.add(w)
            keywords.append(w)
    return keywords[:max_n]


def _pick_palette(topic: str) -> dict:
    """根据主题情感倾向选择配色方案"""
    topic_lower = topic.lower()
    
    # 科技/AI 主题 → 深蓝紫渐变
    if any(kw in topic_lower for kw in ['ai', '人工智能', 'agent', '模型', '算法', '科技', '技术', '智能', '数据', '机器学习', '深度学习', 'gpt', 'llm']):
        return {
            "grad_start": "#1a1a2e", "grad_end": "#16213e", "accent1": "#0f3460",
            "accent2": "#e94560", "accent3": "#533483", "text_color": "#ffffff",
            "sub_color": "#e94560", "bg_decor": "#0f3460"
        }
    # 商业/财经 → 深绿金
    elif any(kw in topic_lower for kw in ['商业', '经济', '金融', '投资', '市场', '创业', '公司', '管理', '战略', '增长']):
        return {
            "grad_start": "#0d1f0d", "grad_end": "#1a3a1a", "accent1": "#2d5a27",
            "accent2": "#d4af37", "accent3": "#8bc34a", "text_color": "#ffffff",
            "sub_color": "#d4af37", "bg_decor": "#2d5a27"
        }
    # 生活/健康 → 暖橙粉
    elif any(kw in topic_lower for kw in ['健康', '生活', '运动', '饮食', '心理', '快乐', '幸福', '旅行', '美食']):
        return {
            "grad_start": "#ff6b6b", "grad_end": "#ffa07a", "accent1": "#ff8c69",
            "accent2": "#ffd700", "accent3": "#ff69b4", "text_color": "#ffffff",
            "sub_color": "#fff0e6", "bg_decor": "#ff8c69"
        }
    # 教育/学习 → 青蓝
    elif any(kw in topic_lower for kw in ['学习', '教育', '知识', '课程', '考试', '学生', '教学', '培训', '成长']):
        return {
            "grad_start": "#006d77", "grad_end": "#83c5be", "accent1": "#00afb9",
            "accent2": "#fdfcdc", "accent3": "#ffddd2", "text_color": "#ffffff",
            "sub_color": "#ffd166", "bg_decor": "#00afb9"
        }
    # 默认 → 紫蓝渐变
    else:
        return {
            "grad_start": "#667eea", "grad_end": "#764ba2", "accent1": "#533483",
            "accent2": "#f093fb", "accent3": "#00e5ff", "text_color": "#ffffff",
            "sub_color": "#f093fb", "bg_decor": "#533483"
        }


def _svg_template_infographic(topic: str, keywords: list[str], p: dict) -> str:
    """信息图风格 — 标题 + 装饰 + 关键词卡片"""
    safe_topic = html.escape(topic)
    # 取最多3个关键词做卡片
    cards = keywords[:3] if keywords else ["核心观点", "深度分析", "实践指南"]
    while len(cards) < 3:
        cards.append(["趋势洞察", "方法论", "案例解析"][len(cards)])
    
    # 关键词卡片颜色
    card_colors = [p["accent2"], p["accent3"], p["sub_color"]]
    
    # 装饰圆环
    decor_circles = f'''
    <circle cx="640" cy="60" r="100" fill="{p['bg_decor']}" opacity="0.15"/>
    <circle cx="640" cy="60" r="70" fill="none" stroke="{p['accent2']}" stroke-width="1" opacity="0.3"/>
    <circle cx="80" cy="380" r="50" fill="{p['bg_decor']}" opacity="0.12"/>
    <circle cx="120" cy="350" r="30" fill="none" stroke="{p['accent3']}" stroke-width="1.5" opacity="0.25"/>
    '''
    
    # 顶部图标 — 几何装饰
    top_icon = f'''
    <g transform="translate(360,70)">
        <rect x="-30" y="-22" width="60" height="44" rx="6" fill="none" stroke="{p['accent2']}" stroke-width="2.5" opacity="0.8"/>
        <rect x="-22" y="-14" width="18" height="11" rx="2" fill="{p['accent2']}" opacity="0.7"/>
        <rect x="4" y="-14" width="18" height="11" rx="2" fill="{p['accent3']}" opacity="0.7"/>
        <rect x="-22" y="3" width="18" height="11" rx="2" fill="{p['accent3']}" opacity="0.5"/>
        <rect x="4" y="3" width="18" height="11" rx="2" fill="{p['accent2']}" opacity="0.5"/>
        <circle cx="0" cy="-32" r="4" fill="{p['accent2']}"/>
        <line x1="-40" y1="-32" x2="-15" y2="-32" stroke="{p['accent2']}" stroke-width="1.5" opacity="0.5"/>
        <line x1="15" y1="-32" x2="40" y2="-32" stroke="{p['accent2']}" stroke-width="1.5" opacity="0.5"/>
    </g>
    '''
    
    # 关键词卡片
    card_x = [80, 280, 480]
    cards_svg = ""
    for i, (kw, color) in enumerate(zip(cards, card_colors)):
        safe_kw = html.escape(kw)
        x = card_x[i]
        cards_svg += f'''
        <g transform="translate({x},270)">
            <rect x="0" y="0" width="160" height="80" rx="12" fill="#ffffff" opacity="0.08"/>
            <rect x="0" y="0" width="4" height="80" rx="2" fill="{color}"/>
            <text x="80" y="32" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="20" font-weight="bold" fill="{color}">{safe_kw}</text>
            <text x="80" y="58" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="12" fill="#ffffffaa">Key Insight {i+1:02d}</text>
        </g>
        '''
    
    # 连接线
    connect_lines = f'''
    <line x1="240" y1="310" x2="280" y2="310" stroke="{p['accent2']}" stroke-width="2" opacity="0.4"/>
    <line x1="440" y1="310" x2="480" y2="310" stroke="{p['accent3']}" stroke-width="2" opacity="0.4"/>
    '''
    
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:{p['grad_start']}"/>
        <stop offset="100%" style="stop-color:{p['grad_end']}"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="2" dy="4" stdDeviation="8" flood-color="#00000044"/></filter>
    <filter id="glow"><feGaussianBlur stdDeviation="3" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
</defs>
<rect width="720" height="420" rx="16" fill="url(#bg)"/>
{decor_circles}
{top_icon}
<text x="360" y="150" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="26" font-weight="bold" fill="{p['text_color']}" filter="url(#shadow)">{safe_topic}</text>
<line x1="180" y1="175" x2="540" y2="175" stroke="{p['accent2']}" stroke-width="1.5" opacity="0.3"/>
<text x="360" y="205" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13" fill="#ffffff88">深度解析 · 核心观点 · 实践指南</text>
{connect_lines}
{cards_svg}
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#ffffff40">ZH記事 · INFOGRAPHIC</text>
</svg>'''


def _svg_template_quote(topic: str, keywords: list[str], p: dict) -> str:
    """引言/金句风格 — 大字标题 + 装饰引号"""
    safe_topic = html.escape(topic)
    keyword = keywords[0] if keywords else "深度好文"
    safe_kw = html.escape(keyword)
    
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="135%" y2="100%">
        <stop offset="0%" style="stop-color:{p['grad_start']}"/>
        <stop offset="100%" style="stop-color:{p['grad_end']}"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="2" dy="4" stdDeviation="8" flood-color="#00000044"/></filter>
</defs>
<rect width="720" height="420" rx="16" fill="url(#bg)"/>
<circle cx="120" cy="80" r="140" fill="{p['bg_decor']}" opacity="0.1"/>
<circle cx="600" cy="360" r="100" fill="{p['bg_decor']}" opacity="0.1"/>
<path d="M 80 120 Q 80 80 120 80 L 160 80 Q 200 80 200 120 L 200 180 Q 200 220 160 220 L 120 220 Q 80 220 80 180 Z" fill="{p['accent2']}" opacity="0.12"/>
<path d="M 520 200 Q 520 160 560 160 L 600 160 Q 640 160 640 200 L 640 260 Q 640 300 600 300 L 560 300 Q 520 300 520 260 Z" fill="{p['accent3']}" opacity="0.12"/>
<text x="100" y="160" font-family="Georgia,serif" font-size="80" fill="{p['accent2']}" opacity="0.4">"</text>
<text x="360" y="190" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="28" font-weight="bold" fill="{p['text_color']}" filter="url(#shadow)">{safe_topic}</text>
<text x="360" y="230" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="16" fill="{p['sub_color']}" opacity="0.8">— {safe_kw}</text>
<rect x="280" y="260" width="160" height="3" rx="1.5" fill="{p['accent2']}" opacity="0.5"/>
<text x="360" y="295" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13" fill="#ffffff60">思考 · 洞察 · 分享</text>
<text x="360" y="395" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#ffffff30">ZH記事 · FEATURED</text>
</svg>'''


def _svg_template_data(topic: str, keywords: list[str], p: dict) -> str:
    """数据/图表风格 — 模拟数据可视化"""
    safe_topic = html.escape(topic)
    
    # 生成模拟数据柱
    bar_data = [random.randint(30, 95) for _ in range(6)]
    bar_y_base = 340
    bar_height_max = 120
    bar_width = 50
    bar_gap = 20
    bar_start_x = 120
    
    bars_svg = ""
    for i, val in enumerate(bar_data):
        h = int(bar_height_max * val / 100)
        x = bar_start_x + i * (bar_width + bar_gap)
        y = bar_y_base - h
        color = [p["accent2"], p["accent3"], p["sub_color"]][i % 3]
        bars_svg += f'<rect x="{x}" y="{y}" width="{bar_width}" height="{h}" rx="4" fill="{color}" opacity="0.75"/>'
        bars_svg += f'<text x="{x + bar_width//2}" y="{bar_y_base + 18}" text-anchor="middle" font-family="Arial,sans-serif" font-size="11" fill="#ffffff60">{chr(65+i)}</text>'
        bars_svg += f'<text x="{x + bar_width//2}" y="{y - 6}" text-anchor="middle" font-family="Arial,sans-serif" font-size="12" font-weight="bold" fill="{color}">{val}%</text>'
    
    # 趋势线
    points = []
    for i, val in enumerate(bar_data):
        x = bar_start_x + i * (bar_width + bar_gap) + bar_width // 2
        y = bar_y_base - int(bar_height_max * val / 100)
        points.append(f"{x},{y}")
    polyline = " ".join(points)
    
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:{p['grad_start']}"/>
        <stop offset="100%" style="stop-color:{p['grad_end']}"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="2" dy="4" stdDeviation="6" flood-color="#00000033"/></filter>
</defs>
<rect width="720" height="420" rx="16" fill="url(#bg)"/>
<circle cx="650" cy="50" r="80" fill="{p['bg_decor']}" opacity="0.1"/>
<text x="360" y="70" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="24" font-weight="bold" fill="{p['text_color']}" filter="url(#shadow)">{safe_topic}</text>
<text x="360" y="98" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="13" fill="{p['sub_color']}" opacity="0.7">数据洞察 · 趋势分析</text>
<line x1="100" y1="340" x2="620" y2="340" stroke="#ffffff20" stroke-width="1.5"/>
<line x1="100" y1="220" x2="620" y2="220" stroke="#ffffff10" stroke-width="1" stroke-dasharray="4,4"/>
<line x1="100" y1="280" x2="620" y2="280" stroke="#ffffff10" stroke-width="1" stroke-dasharray="4,4"/>
<polyline points="{polyline}" fill="none" stroke="{p['accent2']}" stroke-width="2.5" opacity="0.6"/>
{bars_svg}
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#ffffff30">ZH記事 · DATA INSIGHT</text>
</svg>'''


def _svg_template_minimal(topic: str, keywords: list[str], p: dict) -> str:
    """极简几何风格 — 大色块 + 几何图形"""
    safe_topic = html.escape(topic)
    kw1 = html.escape(keywords[0]) if len(keywords) > 0 else "深度"
    kw2 = html.escape(keywords[1]) if len(keywords) > 1 else "洞察"
    
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" style="stop-color:{p['grad_start']}"/>
        <stop offset="100%" style="stop-color:{p['grad_end']}"/>
    </linearGradient>
    <filter id="shadow"><feDropShadow dx="2" dy="4" stdDeviation="10" flood-color="#00000044"/></filter>
</defs>
<rect width="720" height="420" rx="16" fill="url(#bg)"/>
<rect x="0" y="0" width="720" height="210" fill="{p['accent1']}" opacity="0.15" rx="16"/>
<polygon points="0,0 720,0 720,80 0,160" fill="{p['accent2']}" opacity="0.08"/>
<polygon points="0,420 720,420 720,340 0,260" fill="{p['accent3']}" opacity="0.08"/>
<circle cx="580" cy="120" r="60" fill="none" stroke="{p['accent2']}" stroke-width="3" opacity="0.4"/>
<circle cx="580" cy="120" r="35" fill="{p['accent2']}" opacity="0.15"/>
<rect x="120" y="140" width="6" height="140" rx="3" fill="{p['accent2']}"/>
<text x="150" y="180" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="30" font-weight="bold" fill="{p['text_color']}" filter="url(#shadow)">{safe_topic}</text>
<text x="150" y="215" font-family="Microsoft YaHei,sans-serif" font-size="15" fill="{p['sub_color']}">{kw1} · {kw2}</text>
<text x="150" y="250" font-family="Microsoft YaHei,sans-serif" font-size="12" fill="#ffffff50">思考创造价值 · 深度成就专业</text>
<text x="360" y="395" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#ffffff30">ZH記事</text>
</svg>'''


# 模板列表
SVG_TEMPLATES = [
    _svg_template_infographic,
    _svg_template_quote,
    _svg_template_data,
    _svg_template_minimal,
]


def generate_svg(topic: str, content: str = "") -> str:
    """根据文章主题和正文内容生成内容相关的 SVG
    
    topic: 文章标题/主题
    content: 正文内容（用于提取关键词）
    返回: SVG 字符串
    """
    combined = f"{topic} {content}"
    keywords = _extract_keywords(combined)
    palette = _pick_palette(topic)
    
    # 根据内容特征选择模板
    content_lower = (topic + content).lower()
    if any(kw in content_lower for kw in ['数据', '统计', '增长', '趋势', '分析', '对比', '排名', '调研']):
        template = _svg_template_data
    elif any(kw in content_lower for kw in ['金句', '名言', '观点', '总结', '感悟', '思考']):
        template = _svg_template_quote
    elif len(keywords) >= 3:
        template = _svg_template_infographic
    else:
        template = random.choice(SVG_TEMPLATES)
    
    return template(topic, keywords, palette)


# ═══════════════════════════════════════════════════════════
# SVG → PNG 转换（通过浏览器 Canvas）
# ═══════════════════════════════════════════════════════════

SVG_TO_PNG_JS = """
async (svgCode) => {
    const svg_b64 = btoa(unescape(encodeURIComponent(svgCode)));
    const svg_data_url = 'data:image/svg+xml;base64,' + svg_b64;
    
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement('canvas');
            canvas.width = 720;
            canvas.height = 420;
            const ctx = canvas.getContext('2d');
            ctx.drawImage(img, 0, 0, 720, 420);
            const png_data_url = canvas.toDataURL('image/png');
            resolve(png_data_url);
        };
        img.onerror = (e) => reject('SVG load failed: ' + e.toString());
        img.src = svg_data_url;
    });
}
"""


# ═══════════════════════════════════════════════════════════
# PNG → 系统剪贴板
# ═══════════════════════════════════════════════════════════

def _copy_png_to_clipboard(png_data_url: str) -> bool:
    """将 PNG data URL 解码后写入 Windows 系统剪贴板
    
    返回: True 成功 / False 失败
    """
    try:
        # 去掉 data:image/png;base64, 前缀
        b64_data = png_data_url.split(",", 1)[1] if "," in png_data_url else png_data_url
        png_bytes = base64.b64decode(b64_data)
        
        # 用 Pillow 验证图片有效性
        img = Image.open(io.BytesIO(png_bytes))
        img.load()  # 强制加载验证
        
        # 转为 BMP 格式写入剪贴板（Windows 原生方式）
        # CF_DIB 格式 = 去掉 BMP 文件头的 BITMAPINFO
        output = io.BytesIO()
        # 转为 RGB（去掉 alpha 通道，BMP 不支持 alpha）
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        img.save(output, 'BMP')
        bmp_data = output.getvalue()
        
        # BMP 文件头 = 14 字节，后面是 BITMAPINFO
        dib_data = bmp_data[14:]
        
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
        finally:
            win32clipboard.CloseClipboard()
        
        print(f"[image_gen] PNG 已写入剪贴板 ({len(png_bytes)} bytes, {img.size[0]}x{img.size[1]})")
        return True
    except Exception as e:
        print(f"[image_gen] 剪贴板写入失败: {e}")
        return False


# ═══════════════════════════════════════════════════════════
# 完整流程: 生成 SVG → 转 PNG → 剪贴板 → Ctrl+V 粘贴
# ═══════════════════════════════════════════════════════════

async def generate_and_paste_image(
    browser_session: BrowserSession,
    article_topic: str,
    article_content: str = ""
) -> ActionResult:
    """根据文章主题和正文生成配图，转为 PNG 放入剪贴板，并在编辑器中 Ctrl+V 粘贴。
    
    article_topic: 文章标题/主题
    article_content: 正文内容（用于提取关键词，使配图与内容相关）
    """
    page = await browser_session.get_current_page()
    
    # 1. 生成 SVG
    svg_code = generate_svg(article_topic, article_content)
    print(f"[image_gen] SVG 已生成 ({len(svg_code)} chars), 主题: {article_topic}")
    
    # 2. 在浏览器中通过 Canvas 将 SVG 转为 PNG
    try:
        png_data_url = await page.evaluate(SVG_TO_PNG_JS, svg_code)
        if not png_data_url or not png_data_url.startswith("data:image/png"):
            return ActionResult(extracted_content=f"配图失败: SVG→PNG 转换未返回有效数据")
        print(f"[image_gen] SVG→PNG 转换成功 ({len(png_data_url)} chars)")
    except Exception as e:
        return ActionResult(extracted_content=f"配图失败: SVG→PNG 转换异常: {e}")
    
    # 3. PNG 写入系统剪贴板
    ok = _copy_png_to_clipboard(png_data_url)
    if not ok:
        return ActionResult(extracted_content=f"配图失败: 剪贴板写入失败")
    
    # 4. 聚焦编辑器并 Ctrl+V 粘贴
    await page.evaluate("""() => {
        const ed = document.querySelector('[contenteditable="true"]');
        if (ed) { ed.focus(); }
    }""")
    await asyncio.sleep(0.3)
    
    await page.keyboard.press("Control+v")
    await asyncio.sleep(1.0)
    
    # 5. 验证图片是否插入成功
    img_count = await page.evaluate("""() => {
        const ed = document.querySelector('[contenteditable="true"]');
        if (!ed) return 0;
        return ed.querySelectorAll('img').length;
    }""")
    
    if img_count > 0:
        return ActionResult(extracted_content=f"OK:配图已插入 (编辑器内 {img_count} 张图片)")
    else:
        # 再次尝试粘贴
        await asyncio.sleep(0.5)
        await page.keyboard.press("Control+v")
        await asyncio.sleep(1.0)
        img_count2 = await page.evaluate("""() => {
            const ed = document.querySelector('[contenteditable="true"]');
            if (!ed) return 0;
            return ed.querySelectorAll('img').length;
        }""")
        if img_count2 > 0:
            return ActionResult(extracted_content=f"OK:配图已插入 (重试后成功, {img_count2} 张图片)")
        return ActionResult(extracted_content=f"配图警告: 剪贴板已写入但粘贴未检测到图片，可能需要手动 Ctrl+V")
