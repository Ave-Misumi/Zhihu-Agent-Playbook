"""SVG 配图生成 → PNG → 系统剪贴板 → Ctrl+V 粘贴到知乎编辑器

流程:
1. 根据文章主题/正文内容，生成场景插画式 SVG 配图（非数据图表）
2. 通过浏览器 Canvas API 将 SVG 渲染为 PNG（720x420）
3. 将 PNG 写入 Windows 系统剪贴板（win32clipboard）
4. 在编辑器中触发 Ctrl+V 粘贴图片
"""
import io
import base64
import html
import json
import random
import re
import asyncio
import win32clipboard
from PIL import Image
from browser_use.browser.session import BrowserSession
from browser_use.tools.service import ActionResult


# ═══════════════════════════════════════════════════════════
# 主题识别 → 选择对应场景插画
# ═══════════════════════════════════════════════════════════

def _detect_scene(topic: str, content: str = "") -> str:
    """根据文章主题和内容关键词，判断最匹配的场景类型
    
    策略：标题优先匹配，标题无法判断时再用正文辅助。
    避免正文中出现其他领域关键词（如科技文里的"教育辅导"）导致误判。
    """
    topic_lower = topic.lower()
    full_text = (topic + " " + content).lower()

    # ── 第1轮：标题匹配（高置信度）──
    # 科技/AI/智能
    if any(kw in topic_lower for kw in ['ai', '人工智能', 'agent', '模型', '算法', '科技',
                                         '技术', '智能', '机器学习', '深度学习', 'gpt', 'llm',
                                         '大模型', '自动化', '机器人', '芯片', '半导体', '编程',
                                         '代码', '软件', '互联网', '数字化', '云计算', '区块链',
                                         '数据', '程序', '开发']):
        return "tech"

    # 教育/学习
    if any(kw in topic_lower for kw in ['学习', '教育', '知识', '课程', '考试', '学生',
                                         '教学', '培训', '方法论', '费曼', '记忆', '笔记']):
        return "education"

    # 商业/金融
    if any(kw in topic_lower for kw in ['商业', '经济', '金融', '投资', '市场', '创业',
                                         '公司', '管理', '战略', '增长', '股票', '基金',
                                         '理财', '财富', '贸易', '消费', '品牌', '营销']):
        return "business"

    # 生活/健康
    if any(kw in topic_lower for kw in ['健康', '生活', '运动', '饮食', '心理', '快乐',
                                         '幸福', '旅行', '美食', '睡眠', '冥想', '情绪',
                                         '压力', '养生']):
        return "lifestyle"

    # 自然/环境
    if any(kw in topic_lower for kw in ['自然', '环境', '气候', '环保', '生态', '动物',
                                         '植物', '海洋', '森林', '能源', '可持续', '碳中和']):
        return "nature"

    # 城市/建筑
    if any(kw in topic_lower for kw in ['城市', '建筑', '房地产', '规划', '交通', '社区',
                                         '居住', '房子', '装修']):
        return "city"

    # 文化/艺术
    if any(kw in topic_lower for kw in ['文化', '艺术', '设计', '音乐', '电影', '文学',
                                         '历史', '传统', '审美', '创意', '摄影']):
        return "culture"

    # ── 第2轮：标题+正文匹配（标题不够明确时）──
    if any(kw in full_text for kw in ['ai agent', '人工智能', '大模型', 'llm', 'gpt',
                                      '编程', '算法', '机器学习', '深度学习', '云计算',
                                      '区块链', '半导体', '芯片', '数字化转型']):
        return "tech"

    if any(kw in full_text for kw in ['学习方法', '高效学习', '备考', '知识体系',
                                      '费曼技巧', '间隔重复', '记忆法']):
        return "education"

    if any(kw in full_text for kw in ['现金流', '商业模式', '投资策略', '市场营销',
                                      '财务管理', '创业公司', '股票分析']):
        return "business"

    # 默认：抽象概念
    return "abstract"


# ═══════════════════════════════════════════════════════════
# 场景插画模板 — 每个模板是一个有画面感的插图
# ═══════════════════════════════════════════════════════════

def _scene_tech(topic: str, p: dict) -> str:
    """科技场景：亮色调 AI 核心 + 神经网络 + 几何科技感"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#eef2ff"/><stop offset="0.4" stop-color="#dbeafe"/><stop offset="0.7" stop-color="#e0e7ff"/><stop offset="1" stop-color="#f0f9ff"/>
</linearGradient>
<radialGradient id="coreGlow" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#3b82f6" stop-opacity="0.5"/><stop offset="0.5" stop-color="#6366f1" stop-opacity="0.2"/><stop offset="1" stop-color="#eef2ff" stop-opacity="0"/>
</radialGradient>
<radialGradient id="coreBall" cx="0.35" cy="0.3" r="0.7">
<stop offset="0" stop-color="#93c5fd"/><stop offset="0.4" stop-color="#3b82f6"/><stop offset="1" stop-color="#1e40af"/>
</radialGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#60a5fa"/><stop offset="1" stop-color="#3b82f6"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#a78bfa"/><stop offset="1" stop-color="#7c3aed"/>
</linearGradient>
<linearGradient id="card3" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#22d3ee"/><stop offset="1" stop-color="#0891b2"/>
</linearGradient>
<linearGradient id="card4" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#fb923c"/><stop offset="1" stop-color="#ea580c"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="6" stdDeviation="10" flood-color="#1e40af" flood-opacity="0.15"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="5" flood-color="#1e40af" flood-opacity="0.1"/></filter>
</defs>
<!-- 亮色背景 -->
<rect width="720" height="420" fill="url(#bg)"/>
<!-- 背景装饰大圆 -->
<circle cx="120" cy="80" r="180" fill="#c7d2fe" opacity="0.35"/>
<circle cx="620" cy="340" r="200" fill="#bfdbfe" opacity="0.3"/>
<circle cx="360" cy="210" r="280" fill="url(#coreGlow)"/>
<!-- 背景网格 -->
<g stroke="#a5b4fc" stroke-width="0.5" opacity="0.2" fill="none">
<line x1="0" y1="60" x2="720" y2="60"/><line x1="0" y1="120" x2="720" y2="120"/><line x1="0" y1="180" x2="720" y2="180"/><line x1="0" y1="240" x2="720" y2="240"/><line x1="0" y1="300" x2="720" y2="300"/><line x1="0" y1="360" x2="720" y2="360"/>
<line x1="60" y1="0" x2="60" y2="420"/><line x1="120" y1="0" x2="120" y2="420"/><line x1="180" y1="0" x2="180" y2="420"/><line x1="240" y1="0" x2="240" y2="420"/><line x1="300" y1="0" x2="300" y2="420"/><line x1="360" y1="0" x2="360" y2="420"/><line x1="420" y1="0" x2="420" y2="420"/><line x1="480" y1="0" x2="480" y2="420"/><line x1="540" y1="0" x2="540" y2="420"/><line x1="600" y1="0" x2="600" y2="420"/><line x1="660" y1="0" x2="660" y2="420"/>
</g>
<!-- 左上角：神经网络节点 -->
<g opacity="0.7">
<line x1="60" y1="50" x2="110" y2="80" stroke="#6366f1" stroke-width="1.5" opacity="0.4"/>
<line x1="110" y1="80" x2="160" y2="55" stroke="#6366f1" stroke-width="1.5" opacity="0.4"/>
<line x1="160" y1="55" x2="210" y2="85" stroke="#6366f1" stroke-width="1.5" opacity="0.4"/>
<line x1="60" y1="50" x2="100" y2="120" stroke="#6366f1" stroke-width="1" opacity="0.3"/>
<line x1="110" y1="80" x2="100" y2="120" stroke="#6366f1" stroke-width="1" opacity="0.3"/>
<line x1="160" y1="55" x2="170" y2="125" stroke="#6366f1" stroke-width="1" opacity="0.3"/>
<line x1="210" y1="85" x2="170" y2="125" stroke="#6366f1" stroke-width="1" opacity="0.3"/>
<circle cx="60" cy="50" r="5" fill="#6366f1"/>
<circle cx="110" cy="80" r="4" fill="#8b5cf6"/>
<circle cx="160" cy="55" r="5" fill="#6366f1"/>
<circle cx="210" cy="85" r="4" fill="#8b5cf6"/>
<circle cx="100" cy="120" r="3.5" fill="#a78bfa"/>
<circle cx="170" cy="125" r="3.5" fill="#a78bfa"/>
</g>
<!-- 右上角：上升数据图表 -->
<g filter="url(#shadowSm)">
<polyline points="480,130 510,115 540,100 570,85 600,65 630,50 660,35" fill="none" stroke="#3b82f6" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>
<polyline points="480,130 510,115 540,100 570,85 600,65 630,50 660,35 660,130 480,130" fill="#3b82f6" opacity="0.12"/>
<circle cx="480" cy="130" r="4" fill="#3b82f6"/>
<circle cx="510" cy="115" r="4" fill="#3b82f6"/>
<circle cx="540" cy="100" r="4" fill="#3b82f6"/>
<circle cx="570" cy="85" r="4" fill="#3b82f6"/>
<circle cx="600" cy="65" r="4" fill="#3b82f6"/>
<circle cx="630" cy="50" r="4" fill="#3b82f6"/>
<circle cx="660" cy="35" r="5" fill="#1e40af"/>
<rect x="488" y="140" width="10" height="20" rx="2" fill="#60a5fa" opacity="0.7"/>
<rect x="518" y="125" width="10" height="35" rx="2" fill="#60a5fa" opacity="0.7"/>
<rect x="548" y="110" width="10" height="50" rx="2" fill="#60a5fa" opacity="0.7"/>
<rect x="578" y="95" width="10" height="65" rx="2" fill="#3b82f6" opacity="0.8"/>
<rect x="608" y="75" width="10" height="85" rx="2" fill="#3b82f6" opacity="0.8"/>
<rect x="638" y="60" width="10" height="100" rx="2" fill="#1e40af" opacity="0.9"/>
</g>
<!-- 左下角：代码窗口 -->
<g filter="url(#shadow)">
<rect x="40" y="240" width="220" height="130" rx="10" fill="#1e293b"/>
<rect x="40" y="240" width="220" height="28" rx="10" fill="#334155"/>
<rect x="40" y="258" width="220" height="10" fill="#334155"/>
<circle cx="56" cy="254" r="4" fill="#ef4444"/>
<circle cx="70" cy="254" r="4" fill="#f59e0b"/>
<circle cx="84" cy="254" r="4" fill="#22c55e"/>
<text x="150" y="258" text-anchor="middle" font-family="monospace" font-size="9" fill="#94a3b8">agent.py</text>
<text x="55" y="285" font-family="monospace" font-size="9" fill="#22c55e">def</text>
<text x="80" y="285" font-family="monospace" font-size="9" fill="#e2e8f0">run_agent(task):</text>
<text x="65" y="300" font-family="monospace" font-size="9" fill="#22c55e">  plan</text>
<text x="105" y="300" font-family="monospace" font-size="9" fill="#e2e8f0">= llm.plan(task)</text>
<text x="65" y="315" font-family="monospace" font-size="9" fill="#22c55e">  for</text>
<text x="95" y="315" font-family="monospace" font-size="9" fill="#e2e8f0">step in plan:</text>
<text x="75" y="330" font-family="monospace" font-size="9" fill="#f59e0b">    result</text>
<text x="125" y="330" font-family="monospace" font-size="9" fill="#e2e8f0">= step.run()</text>
<text x="75" y="345" font-family="monospace" font-size="9" fill="#f59e0b">    memory</text>
<text x="135" y="345" font-family="monospace" font-size="9" fill="#e2e8f0">.save(result)</text>
<text x="65" y="360" font-family="monospace" font-size="9" fill="#22c55e">  return</text>
<text x="115" y="360" font-family="monospace" font-size="9" fill="#e2e8f0">result</text>
</g>
<!-- 右下角：功能卡片网格 -->
<g filter="url(#shadow)">
<rect x="470" y="240" width="100" height="55" rx="8" fill="url(#card1)"/>
<text x="520" y="265" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">多模态</text>
<text x="520" y="280" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#dbeafe">Multi-Modal</text>
</g>
<g filter="url(#shadow)">
<rect x="585" y="240" width="100" height="55" rx="8" fill="url(#card2)"/>
<text x="635" y="265" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">记忆增强</text>
<text x="635" y="280" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#ede9fe">Memory</text>
</g>
<g filter="url(#shadow)">
<rect x="470" y="310" width="100" height="55" rx="8" fill="url(#card3)"/>
<text x="520" y="335" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">自主规划</text>
<text x="520" y="350" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#cffafe">Planning</text>
</g>
<g filter="url(#shadow)">
<rect x="585" y="310" width="100" height="55" rx="8" fill="url(#card4)"/>
<text x="635" y="335" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">安全协作</text>
<text x="635" y="350" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#fed7aa">Safety</text>
</g>
<!-- 中央 AI 核心 -->
<g transform="translate(360,175)">
<circle r="85" fill="url(#coreGlow)" opacity="0.6"/>
<circle r="60" fill="none" stroke="#3b82f6" stroke-width="1.5" opacity="0.3" stroke-dasharray="4,4"/>
<circle r="48" fill="none" stroke="#6366f1" stroke-width="1" opacity="0.4"/>
<circle r="36" fill="url(#coreBall)" filter="url(#shadow)"/>
<circle r="36" fill="none" stroke="#1e40af" stroke-width="1.5" opacity="0.5"/>
<circle cx="-10" cy="-12" r="14" fill="#fff" opacity="0.25"/>
<text y="5" text-anchor="middle" font-family="Arial,sans-serif" font-size="16" fill="#fff" font-weight="bold">AI</text>
</g>
<!-- 核心周围连接线 -->
<g stroke="#6366f1" stroke-width="1.5" fill="none" opacity="0.35">
<line x1="360" y1="175" x2="470" y2="265"/>
<line x1="360" y1="175" x2="635" y2="265"/>
<line x1="360" y1="175" x2="260" y2="300"/>
<line x1="360" y1="175" x2="520" y2="335"/>
<line x1="360" y1="175" x2="635" y2="335"/>
<line x1="360" y1="175" x2="210" y2="85"/>
<line x1="360" y1="175" x2="600" y2="65"/>
</g>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#1e293b" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#60a5fa">{safe}</text>
</svg>'''



def _scene_business(topic: str, p: dict) -> str:
    """商业场景：亮色城市 + 增长图表 + 商务卡片"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#fef3c7"/><stop offset="0.4" stop-color="#fde68a"/><stop offset="0.7" stop-color="#fcd34d"/><stop offset="1" stop-color="#fbbf24"/>
</linearGradient>
<radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#fef3c7" stop-opacity="0.8"/><stop offset="1" stop-color="#fbbf24" stop-opacity="0"/>
</radialGradient>
<linearGradient id="bldg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#78716c"/><stop offset="1" stop-color="#44403c"/>
</linearGradient>
<linearGradient id="bldg2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#57534e"/><stop offset="1" stop-color="#292524"/>
</linearGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#f59e0b"/><stop offset="1" stop-color="#d97706"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#10b981"/><stop offset="1" stop-color="#059669"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="6" stdDeviation="8" flood-color="#92400e" flood-opacity="0.2"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="4" flood-color="#92400e" flood-opacity="0.15"/></filter>
</defs>
<!-- 亮色背景 -->
<rect width="720" height="420" fill="url(#bg)"/>
<!-- 装饰圆 -->
<circle cx="580" cy="100" r="140" fill="url(#sun)"/>
<circle cx="120" cy="350" r="100" fill="#fde68a" opacity="0.4"/>
<!-- 背景网格 -->
<g stroke="#d4a574" stroke-width="0.5" opacity="0.15" fill="none">
<line x1="0" y1="80" x2="720" y2="80"/><line x1="0" y1="160" x2="720" y2="160"/><line x1="0" y1="240" x2="720" y2="240"/>
<line x1="120" y1="0" x2="120" y2="420"/><line x1="240" y1="0" x2="240" y2="420"/><line x1="360" y1="0" x2="360" y2="420"/><line x1="480" y1="0" x2="480" y2="420"/><line x1="600" y1="0" x2="600" y2="420"/>
</g>
<!-- 太阳 -->
<circle cx="580" cy="100" r="35" fill="#fef9c3" opacity="0.8"/>
<circle cx="580" cy="100" r="22" fill="#fde047"/>
<!-- 远处建筑 -->
<g opacity="0.3">
<rect x="40" y="200" width="50" height="120" fill="#a8a29e"/>
<rect x="100" y="180" width="40" height="140" fill="#a8a29e"/>
<rect x="150" y="220" width="35" height="100" fill="#a8a29e"/>
</g>
<!-- 近处建筑 -->
<g>
<rect x="30" y="160" width="55" height="160" rx="2" fill="url(#bldg)"/>
<rect x="95" y="140" width="45" height="180" rx="2" fill="url(#bldg2)"/>
<rect x="150" y="175" width="50" height="145" rx="2" fill="url(#bldg)"/>
<rect x="560" y="165" width="50" height="155" rx="2" fill="url(#bldg)"/>
<rect x="620" y="145" width="55" height="175" rx="2" fill="url(#bldg2)"/>
<rect x="685" y="180" width="30" height="140" rx="2" fill="url(#bldg)"/>
</g>
<!-- 窗户灯光 -->
<g fill="#fcd34d" opacity="0.7">
<rect x="40" y="175" width="5" height="7"/><rect x="52" y="175" width="5" height="7"/><rect x="64" y="190" width="5" height="7"/><rect x="40" y="200" width="5" height="7"/><rect x="52" y="215" width="5" height="7"/><rect x="64" y="230" width="5" height="7"/><rect x="40" y="245" width="5" height="7"/>
<rect x="105" y="155" width="5" height="7"/><rect x="117" y="155" width="5" height="7"/><rect x="129" y="170" width="5" height="7"/><rect x="105" y="185" width="5" height="7"/><rect x="117" y="200" width="5" height="7"/><rect x="129" y="215" width="5" height="7"/><rect x="105" y="230" width="5" height="7"/><rect x="117" y="245" width="5" height="7"/>
<rect x="160" y="190" width="5" height="7"/><rect x="172" y="190" width="5" height="7"/><rect x="184" y="205" width="5" height="7"/><rect x="160" y="220" width="5" height="7"/><rect x="172" y="235" width="5" height="7"/>
<rect x="570" y="180" width="5" height="7"/><rect x="582" y="180" width="5" height="7"/><rect x="594" y="195" width="5" height="7"/><rect x="570" y="210" width="5" height="7"/><rect x="582" y="225" width="5" height="7"/>
<rect x="630" y="160" width="5" height="7"/><rect x="642" y="160" width="5" height="7"/><rect x="654" y="175" width="5" height="7"/><rect x="630" y="190" width="5" height="7"/><rect x="642" y="205" width="5" height="7"/><rect x="654" y="220" width="5" height="7"/><rect x="630" y="235" width="5" height="7"/>
</g>
<!-- 中央增长图表 -->
<g filter="url(#shadow)">
<rect x="200" y="100" width="320" height="180" rx="12" fill="#fff" opacity="0.95"/>
<text x="360" y="125" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#92400e" font-weight="bold">\u589e\u957f\u8d8b\u52bf</text>
<!-- 柱状图 -->
<rect x="230" y="220" width="22" height="40" rx="3" fill="#fcd34d"/>
<rect x="265" y="200" width="22" height="60" rx="3" fill="#fbbf24"/>
<rect x="300" y="175" width="22" height="85" rx="3" fill="#f59e0b"/>
<rect x="335" y="150" width="22" height="110" rx="3" fill="#d97706"/>
<rect x="370" y="130" width="22" height="130" rx="3" fill="#b45309"/>
<rect x="405" y="110" width="22" height="150" rx="3" fill="#92400e"/>
<!-- 折线 -->
<polyline points="241,220 276,200 311,175 346,150 381,130 416,110" fill="none" stroke="#059669" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="241" cy="220" r="3.5" fill="#059669"/>
<circle cx="276" cy="200" r="3.5" fill="#059669"/>
<circle cx="311" cy="175" r="3.5" fill="#059669"/>
<circle cx="346" cy="150" r="3.5" fill="#059669"/>
<circle cx="381" cy="130" r="3.5" fill="#059669"/>
<circle cx="416" cy="110" r="4" fill="#047857"/>
<!-- 箭头 -->
<polygon points="416,100 424,110 408,110" fill="#047857"/>
<!-- Y轴 -->
<line x1="220" y1="105" x2="220" y2="260" stroke="#d6d3d1" stroke-width="1"/>
<line x1="220" y1="260" x2="440" y2="260" stroke="#d6d3d1" stroke-width="1"/>
</g>
<!-- 左下：商务卡片1 -->
<g filter="url(#shadow)">
<rect x="30" y="285" width="150" height="60" rx="8" fill="url(#card1)"/>
<text x="105" y="310" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#fff" font-weight="bold">\u8d22\u52a1\u7ba1\u7406</text>
<text x="105" y="328" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#fef3c7">Finance</text>
</g>
<!-- 右下：商务卡片2 -->
<g filter="url(#shadow)">
<rect x="540" y="285" width="150" height="60" rx="8" fill="url(#card2)"/>
<text x="615" y="310" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#fff" font-weight="bold">\u6218\u7565\u89c4\u5212</text>
<text x="615" y="328" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#d1fae5">Strategy</text>
</g>
<!-- 金币装饰 -->
<g opacity="0.6">
<circle cx="490" cy="80" r="8" fill="#fbbf24" stroke="#f59e0b" stroke-width="1"/>
<text x="490" y="84" text-anchor="middle" font-family="Arial" font-size="9" fill="#92400e" font-weight="bold">$</text>
<circle cx="180" cy="95" r="6" fill="#fbbf24" stroke="#f59e0b" stroke-width="1"/>
<text x="180" y="99" text-anchor="middle" font-family="Arial" font-size="7" fill="#92400e" font-weight="bold">$</text>
</g>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#44403c" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#fde047">{safe}</text>
</svg>'''


def _scene_education(topic: str, p: dict) -> str:
    """教育场景：亮色书本 + 知识网络 + 学习卡片"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#ecfeff"/><stop offset="0.4" stop-color="#cffafe"/><stop offset="0.7" stop-color="#a5f3fc"/><stop offset="1" stop-color="#67e8f9"/>
</linearGradient>
<radialGradient id="glow" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#06b6d4" stop-opacity="0.3"/><stop offset="1" stop-color="#ecfeff" stop-opacity="0"/>
</radialGradient>
<linearGradient id="bookL" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#e0f2fe"/>
</linearGradient>
<linearGradient id="bookR" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#e0f2fe"/><stop offset="1" stop-color="#fff"/>
</linearGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#0ea5e9"/><stop offset="1" stop-color="#0284c7"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#8b5cf6"/><stop offset="1" stop-color="#7c3aed"/>
</linearGradient>
<linearGradient id="card3" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#f59e0b"/><stop offset="1" stop-color="#d97706"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="6" stdDeviation="8" flood-color="#0c4a6e" flood-opacity="0.15"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="4" flood-color="#0c4a6e" flood-opacity="0.1"/></filter>
</defs>
<!-- 亮色背景 -->
<rect width="720" height="420" fill="url(#bg)"/>
<!-- 装饰圆 -->
<circle cx="360" cy="180" r="250" fill="url(#glow)"/>
<circle cx="100" cy="80" r="80" fill="#a5f3fc" opacity="0.3"/>
<circle cx="640" cy="340" r="90" fill="#a5f3fc" opacity="0.25"/>
<!-- 背景网格 -->
<g stroke="#67e8f9" stroke-width="0.5" opacity="0.15" fill="none">
<line x1="0" y1="70" x2="720" y2="70"/><line x1="0" y1="140" x2="720" y2="140"/><line x1="0" y1="210" x2="720" y2="210"/><line x1="0" y1="280" x2="720" y2="280"/>
<line x1="90" y1="0" x2="90" y2="420"/><line x1="180" y1="0" x2="180" y2="420"/><line x1="270" y1="0" x2="270" y2="420"/><line x1="360" y1="0" x2="360" y2="420"/><line x1="450" y1="0" x2="450" y2="420"/><line x1="540" y1="0" x2="540" y2="420"/><line x1="630" y1="0" x2="630" y2="420"/>
</g>
<!-- 左上：知识网络节点 -->
<g opacity="0.7">
<line x1="50" y1="50" x2="100" y2="80" stroke="#0ea5e9" stroke-width="1.5" opacity="0.4"/>
<line x1="100" y1="80" x2="150" y2="50" stroke="#0ea5e9" stroke-width="1.5" opacity="0.4"/>
<line x1="150" y1="50" x2="200" y2="85" stroke="#0ea5e9" stroke-width="1.5" opacity="0.4"/>
<line x1="100" y1="80" x2="130" y2="130" stroke="#0ea5e9" stroke-width="1" opacity="0.3"/>
<line x1="200" y1="85" x2="170" y2="140" stroke="#0ea5e9" stroke-width="1" opacity="0.3"/>
<circle cx="50" cy="50" r="5" fill="#0ea5e9"/>
<circle cx="100" cy="80" r="4" fill="#8b5cf6"/>
<circle cx="150" cy="50" r="5" fill="#0ea5e9"/>
<circle cx="200" cy="85" r="4" fill="#8b5cf6"/>
<circle cx="130" cy="130" r="3.5" fill="#06b6d4"/>
<circle cx="170" cy="140" r="3.5" fill="#06b6d4"/>
</g>
<!-- 右上：学习进度条 -->
<g filter="url(#shadowSm)">
<rect x="520" y="40" width="170" height="110" rx="10" fill="#fff" opacity="0.9"/>
<text x="605" y="62" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="9" fill="#0c4a6e" font-weight="bold">\u5b66\u4e60\u8fdb\u5ea6</text>
<rect x="540" y="75" width="130" height="8" rx="4" fill="#e0f2fe"/>
<rect x="540" y="75" width="100" height="8" rx="4" fill="#0ea5e9"/>
<rect x="540" y="90" width="130" height="8" rx="4" fill="#e0f2fe"/>
<rect x="540" y="90" width="70" height="8" rx="4" fill="#8b5cf6"/>
<rect x="540" y="105" width="130" height="8" rx="4" fill="#e0f2fe"/>
<rect x="540" y="105" width="115" height="8" rx="4" fill="#f59e0b"/>
<rect x="540" y="120" width="130" height="8" rx="4" fill="#e0f2fe"/>
<rect x="540" y="120" width="85" height="8" rx="4" fill="#10b981"/>
<text x="605" y="142" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#64748b">\u5404\u79d1\u7ee9\u6548</text>
</g>
<!-- 中央：翻开的书 -->
<g filter="url(#shadow)">
<!-- 书底 -->
<path d="M 200 280 L 360 250 L 520 280 L 520 350 L 360 320 L 200 350 Z" fill="#bae6fd" opacity="0.5"/>
<!-- 左页 -->
<path d="M 200 180 L 360 155 L 360 320 L 200 350 Z" fill="url(#bookL)" stroke="#0284c7" stroke-width="1"/>
<!-- 右页 -->
<path d="M 360 155 L 520 180 L 520 350 L 360 320 Z" fill="url(#bookR)" stroke="#0284c7" stroke-width="1"/>
<!-- 书脊 -->
<line x1="360" y1="155" x2="360" y2="320" stroke="#0284c7" stroke-width="1.5"/>
<!-- 左页文字线 -->
<line x1="230" y1="210" x2="340" y2="190" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="230" y1="225" x2="340" y2="205" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="230" y1="240" x2="340" y2="220" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="230" y1="255" x2="340" y2="235" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="230" y1="270" x2="320" y2="255" stroke="#94a3b8" stroke-width="0.8"/>
<!-- 右页文字线 -->
<line x1="380" y1="190" x2="490" y2="210" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="380" y1="205" x2="490" y2="225" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="380" y1="220" x2="490" y2="240" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="380" y1="235" x2="490" y2="255" stroke="#94a3b8" stroke-width="0.8"/>
<line x1="380" y1="255" x2="470" y2="270" stroke="#94a3b8" stroke-width="0.8"/>
</g>
<!-- 知识光芒 -->
<g opacity="0.4">
<line x1="360" y1="155" x2="280" y2="100" stroke="#0ea5e9" stroke-width="1"/>
<line x1="360" y1="155" x2="360" y2="90" stroke="#0ea5e9" stroke-width="1"/>
<line x1="360" y1="155" x2="440" y2="100" stroke="#0ea5e9" stroke-width="1"/>
<circle cx="280" cy="100" r="4" fill="#0ea5e9"/>
<circle cx="360" cy="90" r="5" fill="#8b5cf6"/>
<circle cx="440" cy="100" r="4" fill="#0ea5e9"/>
</g>
<!-- 左下：学习卡片1 -->
<g filter="url(#shadow)">
<rect x="30" y="195" width="130" height="50" rx="8" fill="url(#card1)"/>
<text x="95" y="218" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u8d39\u66fc\u6280\u5de7</text>
<text x="95" y="233" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#e0f2fe">Feynman</text>
</g>
<!-- 左下：学习卡片2 -->
<g filter="url(#shadow)">
<rect x="30" y="255" width="130" height="50" rx="8" fill="url(#card2)"/>
<text x="95" y="278" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u95f4\u9694\u91cd\u590d</text>
<text x="95" y="293" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#ede9fe">Spaced</text>
</g>
<!-- 右下：学习卡片3 -->
<g filter="url(#shadow)">
<rect x="560" y="195" width="130" height="50" rx="8" fill="url(#card3)"/>
<text x="625" y="218" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u4e3b\u52a8\u56de\u5fc6</text>
<text x="625" y="233" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#fef3c7">Recall</text>
</g>
<!-- 右下：学习卡片4 -->
<g filter="url(#shadow)">
<rect x="560" y="255" width="130" height="50" rx="8" fill="#10b981"/>
<text x="625" y="278" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u7ec4\u5757\u5b66\u4e60</text>
<text x="625" y="293" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#d1fae5">Blocks</text>
</g>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#0c4a6e" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#67e8f9">{safe}</text>
</svg>'''


def _scene_lifestyle(topic: str, p: dict) -> str:
    """生活场景：亮色日出山景 + 旅行元素"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#fef3c7"/><stop offset="0.3" stop-color="#fde68a"/><stop offset="0.5" stop-color="#fdba74"/><stop offset="0.7" stop-color="#fb923c"/><stop offset="1" stop-color="#f97316"/>
</linearGradient>
<radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#fffbeb" stop-opacity="1"/><stop offset="0.5" stop-color="#fcd34d" stop-opacity="0.6"/><stop offset="1" stop-color="#f97316" stop-opacity="0"/>
</radialGradient>
<linearGradient id="m1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#84cc16"/><stop offset="1" stop-color="#4d7c0f"/>
</linearGradient>
<linearGradient id="m2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#65a30d"/><stop offset="1" stop-color="#365314"/>
</linearGradient>
<linearGradient id="m3" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#3f6212"/><stop offset="1" stop-color="#1a2e05"/>
</linearGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#fb7185"/><stop offset="1" stop-color="#e11d48"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#34d399"/><stop offset="1" stop-color="#059669"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="5" stdDeviation="6" flood-color="#7c2d12" flood-opacity="0.2"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="4" flood-color="#7c2d12" flood-opacity="0.15"/></filter>
</defs>
<!-- 亮色天空 -->
<rect width="720" height="420" fill="url(#sky)"/>
<!-- 太阳光晕 -->
<circle cx="540" cy="130" r="140" fill="url(#sun)"/>
<circle cx="540" cy="130" r="40" fill="#fffbeb" opacity="0.9"/>
<circle cx="540" cy="130" r="28" fill="#fde047"/>
<!-- 云朵 -->
<g fill="#fff" opacity="0.7">
<ellipse cx="150" cy="80" rx="40" ry="14"/>
<ellipse cx="170" cy="72" rx="25" ry="12"/>
<ellipse cx="620" cy="60" rx="35" ry="12"/>
<ellipse cx="635" cy="55" rx="22" ry="10"/>
</g>
<!-- 飞鸟 -->
<g fill="#7c2d12" opacity="0.6">
<path d="M 300 90 Q 310 85 320 90 Q 330 85 340 90" fill="none" stroke="#7c2d12" stroke-width="2" stroke-linecap="round"/>
<path d="M 380 75 Q 388 71 396 75 Q 404 71 412 75" fill="none" stroke="#7c2d12" stroke-width="1.5" stroke-linecap="round"/>
<path d="M 250 110 Q 258 106 266 110 Q 274 106 282 110" fill="none" stroke="#7c2d12" stroke-width="1.5" stroke-linecap="round"/>
</g>
<!-- 远山 -->
<polygon points="0,280 100,180 200,240 320,160 440,220 560,170 660,210 720,190 720,360 0,360" fill="url(#m1)" opacity="0.6"/>
<!-- 中山 -->
<polygon points="0,320 80,250 180,290 280,230 380,270 480,240 580,280 720,260 720,360 0,360" fill="url(#m2)" opacity="0.75"/>
<!-- 近山 -->
<polygon points="0,360 60,310 140,340 240,300 340,330 440,305 540,335 640,315 720,330 720,360 0,360" fill="url(#m3)"/>
<!-- 树木 -->
<g>
<rect x="98" y="325" width="4" height="20" fill="#52525b"/>
<circle cx="100" cy="320" r="12" fill="#16a34a"/>
<rect x="248" y="318" width="4" height="22" fill="#52525b"/>
<circle cx="250" cy="312" r="14" fill="#15803d"/>
<rect x="448" y="320" width="4" height="20" fill="#52525b"/>
<circle cx="450" cy="315" r="11" fill="#16a34a"/>
<rect x="598" y="322" width="4" height="18" fill="#52525b"/>
<circle cx="600" cy="317" r="10" fill="#15803d"/>
</g>
<!-- 左下：旅行卡片1 -->
<g filter="url(#shadow)">
<rect x="20" y="195" width="140" height="55" rx="8" fill="url(#card1)"/>
<text x="90" y="218" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u653e\u677e\u5fc3\u60c5</text>
<text x="90" y="233" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#fecdd3">Relax</text>
</g>
<!-- 右下：旅行卡片2 -->
<g filter="url(#shadow)">
<rect x="560" y="195" width="140" height="55" rx="8" fill="url(#card2)"/>
<text x="630" y="218" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u89aa\u8fd1\u81ea\u7136</text>
<text x="630" y="233" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#d1fae5">Nature</text>
</g>
<!-- 左上：指南针图标 -->
<g transform="translate(60,150)" opacity="0.7">
<circle r="18" fill="#fff" opacity="0.8"/>
<circle r="18" fill="none" stroke="#7c2d12" stroke-width="1.5"/>
<polygon points="0,-12 4,0 0,12 -4,0" fill="#dc2626"/>
<polygon points="0,-12 4,0 0,0" fill="#991b1b"/>
<text y="-22" text-anchor="middle" font-family="Arial" font-size="8" fill="#7c2d12" font-weight="bold">N</text>
</g>
<!-- 右上：相机图标 -->
<g transform="translate(660,150)" opacity="0.7">
<rect x="-20" y="-12" width="40" height="28" rx="4" fill="#fff" opacity="0.8"/>
<rect x="-20" y="-12" width="40" height="28" rx="4" fill="none" stroke="#7c2d12" stroke-width="1.5"/>
<circle r="8" fill="none" stroke="#7c2d12" stroke-width="1.5"/>
<circle r="4" fill="#7c2d12" opacity="0.3"/>
<rect x="-15" y="-15" width="10" height="5" rx="1" fill="#7c2d12"/>
</g>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#7c2d12" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#fde047">{safe}</text>
</svg>'''


def _scene_nature(topic: str, p: dict) -> str:
    """自然场景：亮色森林湖泊 + 生态元素"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#dbeafe"/><stop offset="0.4" stop-color="#e0f2fe"/><stop offset="0.7" stop-color="#f0fdf4"/><stop offset="1" stop-color="#dcfce7"/>
</linearGradient>
<linearGradient id="water" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#7dd3fc"/><stop offset="0.5" stop-color="#38bdf8"/><stop offset="1" stop-color="#0ea5e9"/>
</linearGradient>
<linearGradient id="grass" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#4ade80"/><stop offset="1" stop-color="#16a34a"/>
</linearGradient>
<linearGradient id="trunk" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#92400e"/><stop offset="1" stop-color="#451a03"/>
</linearGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#22c55e"/><stop offset="1" stop-color="#15803d"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#0ea5e9"/><stop offset="1" stop-color="#0369a1"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="5" stdDeviation="6" flood-color="#14532d" flood-opacity="0.2"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="4" flood-color="#14532d" flood-opacity="0.15"/></filter>
</defs>
<!-- 亮色天空 -->
<rect width="720" height="420" fill="url(#sky)"/>
<!-- 太阳 -->
<circle cx="580" cy="80" r="30" fill="#fef9c3" opacity="0.8"/>
<circle cx="580" cy="80" r="20" fill="#fde047"/>
<!-- 云朵 -->
<g fill="#fff" opacity="0.85">
<ellipse cx="120" cy="60" rx="35" ry="13"/>
<ellipse cx="140" cy="52" rx="22" ry="10"/>
<ellipse cx="300" cy="40" rx="30" ry="11"/>
<ellipse cx="315" cy="35" rx="18" ry="8"/>
<ellipse cx="450" cy="55" rx="28" ry="10"/>
</g>
<!-- 远山（雪山） -->
<polygon points="0,250 80,150 160,210 240,130 320,190 400,120 480,180 560,140 640,200 720,170 720,280 0,280" fill="#cbd5e1" opacity="0.6"/>
<polygon points="80,150 100,170 60,170" fill="#fff" opacity="0.8"/>
<polygon points="240,130 260,150 220,150" fill="#fff" opacity="0.8"/>
<polygon points="400,120 420,140 380,140" fill="#fff" opacity="0.8"/>
<!-- 中山 -->
<polygon points="0,290 60,230 140,260 220,210 300,250 380,220 460,255 540,225 620,250 720,235 720,310 0,310" fill="#86efac" opacity="0.6"/>
<!-- 草地 -->
<rect y="280" width="720" height="80" fill="url(#grass)"/>
<!-- 湖泊 -->
<ellipse cx="360" cy="310" rx="200" ry="35" fill="url(#water)" opacity="0.85"/>
<!-- 水波纹 -->
<g stroke="#fff" stroke-width="0.8" fill="none" opacity="0.4">
<ellipse cx="320" cy="305" rx="40" ry="6"/>
<ellipse cx="400" cy="315" rx="35" ry="5"/>
<ellipse cx="280" cy="318" rx="25" ry="4"/>
</g>
<!-- 树木 -->
<g>
<rect x="58" y="245" width="6" height="35" fill="url(#trunk)"/>
<polygon points="61,210 45,250 77,250" fill="#15803d"/>
<polygon points="61,225 48,255 74,255" fill="#16a34a"/>
<rect x="148" y="240" width="5" height="40" fill="url(#trunk)"/>
<polygon points="150,205 135,245 165,245" fill="#16a34a"/>
<polygon points="150,220 138,250 162,250" fill="#22c55e"/>
<rect x="570" y="245" width="6" height="35" fill="url(#trunk)"/>
<polygon points="573,210 557,250 589,250" fill="#15803d"/>
<polygon points="573,225 560,255 586,255" fill="#16a34a"/>
<rect x="660" y="240" width="5" height="40" fill="url(#trunk)"/>
<polygon points="662,205 647,245 677,245" fill="#16a34a"/>
<polygon points="662,220 650,250 674,250" fill="#22c55e"/>
</g>
<!-- 左下：生态卡片1 -->
<g filter="url(#shadow)">
<rect x="20" y="165" width="140" height="55" rx="8" fill="url(#card1)"/>
<text x="90" y="188" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u751f\u6001\u4fdd\u62a4</text>
<text x="90" y="203" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#dcfce7">Eco</text>
</g>
<!-- 右下：生态卡片2 -->
<g filter="url(#shadow)">
<rect x="560" y="165" width="140" height="55" rx="8" fill="url(#card2)"/>
<text x="630" y="188" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u7eff\u8272\u5730\u7403</text>
<text x="630" y="203" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#e0f2fe">Green</text>
</g>
<!-- 飞鸟 -->
<g opacity="0.5">
<path d="M 200 70 Q 208 66 216 70 Q 224 66 232 70" fill="none" stroke="#1e3a5f" stroke-width="1.5" stroke-linecap="round"/>
<path d="M 350 55 Q 357 52 364 55 Q 371 52 378 55" fill="none" stroke="#1e3a5f" stroke-width="1.2" stroke-linecap="round"/>
</g>
<!-- 太阳能板小图标 -->
<g transform="translate(100,335)" opacity="0.6">
<rect x="-12" y="-8" width="24" height="16" rx="2" fill="#1e40af"/>
<line x1="-12" y1="0" x2="12" y2="0" stroke="#3b82f6" stroke-width="0.5"/>
<line x1="0" y1="-8" x2="0" y2="8" stroke="#3b82f6" stroke-width="0.5"/>
<rect x="-1" y="8" width="2" height="6" fill="#1e40af"/>
</g>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#14532d" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#86efac">{safe}</text>
</svg>'''


def _scene_city(topic: str, p: dict) -> str:
    """城市场景：亮色夕阳城市 + 街道灯光 + 建筑细节"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#fef3c7"/><stop offset="0.25" stop-color="#fdba74"/><stop offset="0.5" stop-color="#fb7185"/><stop offset="0.75" stop-color="#c084fc"/><stop offset="1" stop-color="#818cf8"/>
</linearGradient>
<radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#fffbeb" stop-opacity="1"/><stop offset="0.5" stop-color="#fcd34d" stop-opacity="0.5"/><stop offset="1" stop-color="#fb7185" stop-opacity="0"/>
</radialGradient>
<linearGradient id="bldg1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#6b7280"/><stop offset="1" stop-color="#374151"/>
</linearGradient>
<linearGradient id="bldg2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#4b5563"/><stop offset="1" stop-color="#1f2937"/>
</linearGradient>
<linearGradient id="bldg3" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#78716c"/><stop offset="1" stop-color="#44403c"/>
</linearGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#8b5cf6"/><stop offset="1" stop-color="#6d28d9"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#f59e0b"/><stop offset="1" stop-color="#d97706"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="5" stdDeviation="6" flood-color="#4c1d95" flood-opacity="0.2"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="4" flood-color="#4c1d95" flood-opacity="0.15"/></filter>
</defs>
<!-- 亮色天空 -->
<rect width="720" height="420" fill="url(#sky)"/>
<!-- 装饰圆 -->
<circle cx="360" cy="200" r="250" fill="url(#sun)" opacity="0.5"/>
<!-- 太阳 -->
<circle cx="500" cy="120" r="45" fill="#fffbeb" opacity="0.8"/>
<circle cx="500" cy="120" r="30" fill="#fde047"/>
<!-- 云朵 -->
<g fill="#fff" opacity="0.6">
<ellipse cx="100" cy="70" rx="30" ry="10"/>
<ellipse cx="115" cy="64" rx="18" ry="8"/>
<ellipse cx="620" cy="50" rx="25" ry="9"/>
</g>
<!-- 远景建筑 -->
<g opacity="0.35">
<rect x="0" y="200" width="40" height="160" fill="#9ca3af"/>
<rect x="45" y="180" width="35" height="180" fill="#9ca3af"/>
<rect x="85" y="210" width="30" height="150" fill="#9ca3af"/>
<rect x="640" y="190" width="35" height="170" fill="#9ca3af"/>
<rect x="680" y="210" width="40" height="150" fill="#9ca3af"/>
</g>
<!-- 中景建筑 -->
<g>
<rect x="20" y="160" width="50" height="200" rx="2" fill="url(#bldg1)"/>
<rect x="75" y="130" width="55" height="230" rx="2" fill="url(#bldg2)"/>
<rect x="135" y="170" width="45" height="190" rx="2" fill="url(#bldg3)"/>
<rect x="540" y="150" width="50" height="210" rx="2" fill="url(#bldg1)"/>
<rect x="595" y="120" width="55" height="240" rx="2" fill="url(#bldg2)"/>
<rect x="655" y="165" width="45" height="195" rx="2" fill="url(#bldg3)"/>
</g>
<!-- 窗户灯光（暖色） -->
<g fill="#fcd34d" opacity="0.8">
<rect x="28" y="175" width="5" height="7"/><rect x="38" y="175" width="5" height="7"/><rect x="48" y="175" width="5" height="7"/><rect x="28" y="190" width="5" height="7"/><rect x="48" y="190" width="5" height="7"/><rect x="28" y="205" width="5" height="7"/><rect x="38" y="205" width="5" height="7"/><rect x="28" y="220" width="5" height="7"/><rect x="48" y="220" width="5" height="7"/><rect x="38" y="235" width="5" height="7"/><rect x="28" y="250" width="5" height="7"/><rect x="48" y="250" width="5" height="7"/><rect x="28" y="265" width="5" height="7"/><rect x="38" y="265" width="5" height="7"/>
<rect x="85" y="145" width="5" height="7"/><rect x="95" y="145" width="5" height="7"/><rect x="105" y="145" width="5" height="7"/><rect x="115" y="145" width="5" height="7"/><rect x="85" y="160" width="5" height="7"/><rect x="105" y="160" width="5" height="7"/><rect x="115" y="160" width="5" height="7"/><rect x="85" y="175" width="5" height="7"/><rect x="95" y="175" width="5" height="7"/><rect x="115" y="175" width="5" height="7"/><rect x="85" y="190" width="5" height="7"/><rect x="105" y="190" width="5" height="7"/><rect x="85" y="205" width="5" height="7"/><rect x="95" y="205" width="5" height="7"/><rect x="115" y="205" width="5" height="7"/><rect x="85" y="220" width="5" height="7"/><rect x="105" y="220" width="5" height="7"/><rect x="95" y="235" width="5" height="7"/><rect x="115" y="235" width="5" height="7"/><rect x="85" y="250" width="5" height="7"/><rect x="105" y="250" width="5" height="7"/>
<rect x="550" y="165" width="5" height="7"/><rect x="560" y="165" width="5" height="7"/><rect x="570" y="165" width="5" height="7"/><rect x="580" y="165" width="5" height="7"/><rect x="550" y="180" width="5" height="7"/><rect x="570" y="180" width="5" height="7"/><rect x="580" y="180" width="5" height="7"/><rect x="550" y="195" width="5" height="7"/><rect x="560" y="195" width="5" height="7"/><rect x="580" y="195" width="5" height="7"/><rect x="550" y="210" width="5" height="7"/><rect x="570" y="210" width="5" height="7"/><rect x="550" y="225" width="5" height="7"/><rect x="560" y="225" width="5" height="7"/><rect x="580" y="225" width="5" height="7"/><rect x="550" y="240" width="5" height="7"/><rect x="570" y="240" width="5" height="7"/>
<rect x="605" y="135" width="5" height="7"/><rect x="615" y="135" width="5" height="7"/><rect x="625" y="135" width="5" height="7"/><rect x="635" y="135" width="5" height="7"/><rect x="605" y="150" width="5" height="7"/><rect x="625" y="150" width="5" height="7"/><rect x="635" y="150" width="5" height="7"/><rect x="605" y="165" width="5" height="7"/><rect x="615" y="165" width="5" height="7"/><rect x="635" y="165" width="5" height="7"/><rect x="605" y="180" width="5" height="7"/><rect x="625" y="180" width="5" height="7"/><rect x="605" y="195" width="5" height="7"/><rect x="615" y="195" width="5" height="7"/><rect x="635" y="195" width="5" height="7"/><rect x="605" y="210" width="5" height="7"/><rect x="625" y="210" width="5" height="7"/><rect x="605" y="225" width="5" height="7"/><rect x="615" y="225" width="5" height="7"/><rect x="635" y="225" width="5" height="7"/>
</g>
<!-- 中央：城市计划卡片 -->
<g filter="url(#shadow)">
<rect x="200" y="70" width="320" height="100" rx="12" fill="#fff" opacity="0.9"/>
<text x="360" y="95" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="11" fill="#4c1d95" font-weight="bold">\u57ce\u5e02\u89c4\u5212\u8981\u7d20</text>
<line x1="230" y1="105" x2="490" y2="105" stroke="#e5e7eb" stroke-width="1"/>
<!-- 三个指标条 -->
<text x="240" y="125" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#6b7280">\u4ea4\u901a</text>
<rect x="280" y="118" width="180" height="6" rx="3" fill="#e9d5ff"/>
<rect x="280" y="118" width="140" height="6" rx="3" fill="#8b5cf6"/>
<text x="240" y="142" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#6b7280">\u4f4f\u5b85</text>
<rect x="280" y="135" width="180" height="6" rx="3" fill="#fed7aa"/>
<rect x="280" y="135" width="110" height="6" rx="3" fill="#f59e0b"/>
<text x="240" y="159" font-family="Microsoft YaHei,sans-serif" font-size="8" fill="#6b7280">\u5546\u4e1a</text>
<rect x="280" y="152" width="180" height="6" rx="3" fill="#e9d5ff"/>
<rect x="280" y="152" width="155" height="6" rx="3" fill="#8b5cf6"/>
</g>
<!-- 左下卡片 -->
<g filter="url(#shadow)">
<rect x="20" y="200" width="140" height="50" rx="8" fill="url(#card1)"/>
<text x="90" y="222" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u4ea4\u901a\u7f51\u7edc</text>
<text x="90" y="237" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#ede9fe">Transit</text>
</g>
<!-- 右下卡片 -->
<g filter="url(#shadow)">
<rect x="560" y="200" width="140" height="50" rx="8" fill="url(#card2)"/>
<text x="630" y="222" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u793e\u533a\u89c4\u5212</text>
<text x="630" y="237" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#fef3c7">Community</text>
</g>
<!-- 街道灯光 -->
<g fill="#fcd34d" opacity="0.6">
<circle cx="185" cy="350" r="2"/><circle cx="220" cy="355" r="2"/><circle cx="260" cy="350" r="2"/><circle cx="300" cy="355" r="2"/><circle cx="340" cy="350" r="2"/><circle cx="380" cy="355" r="2"/><circle cx="420" cy="350" r="2"/><circle cx="460" cy="355" r="2"/><circle cx="500" cy="350" r="2"/><circle cx="535" cy="355" r="2"/>
</g>
<!-- 地面 -->
<rect y="360" width="720" height="60" fill="#1f2937" opacity="0.8"/>
<!-- 道路线 -->
<line x1="0" y1="380" x2="720" y2="380" stroke="#fbbf24" stroke-width="1" stroke-dasharray="10,8" opacity="0.6"/>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#1f2937" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#fbbf24">{safe}</text>
</svg>'''


def _scene_culture(topic: str, p: dict) -> str:
    """文化场景：亮色水墨风 + 文化元素 + 印章"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#fefce8"/><stop offset="0.5" stop-color="#fef9c3"/><stop offset="1" stop-color="#fde68a"/>
</linearGradient>
<linearGradient id="mountain1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#92400e"/><stop offset="1" stop-color="#451a03"/>
</linearGradient>
<linearGradient id="mountain2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#a16207"/><stop offset="1" stop-color="#713f12"/>
</linearGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#dc2626"/><stop offset="1" stop-color="#991b1b"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#b45309"/><stop offset="1" stop-color="#78350f"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="5" stdDeviation="6" flood-color="#78350f" flood-opacity="0.2"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="4" flood-color="#78350f" flood-opacity="0.15"/></filter>
</defs>
<!-- 亮色宣纸背景 -->
<rect width="720" height="420" fill="url(#bg)"/>
<!-- 背景装饰圆 -->
<circle cx="580" cy="100" r="100" fill="#fde68a" opacity="0.4"/>
<circle cx="120" cy="320" r="80" fill="#fef3c7" opacity="0.5"/>
<!-- 远山 -->
<polygon points="0,220 60,140 120,190 200,110 280,170 360,100 440,160 520,120 600,180 720,140 720,280 0,280" fill="#d6d3d1" opacity="0.4"/>
<!-- 中山 -->
<polygon points="0,260 50,190 130,230 210,160 290,210 370,150 450,200 530,170 610,220 720,190 720,300 0,300" fill="url(#mountain2)" opacity="0.5"/>
<!-- 近山 -->
<polygon points="0,300 40,250 100,280 180,230 260,270 340,220 420,260 500,235 580,275 660,245 720,270 720,320 0,320" fill="url(#mountain1)" opacity="0.6"/>
<!-- 竹子 -->
<g>
<rect x="85" y="160" width="5" height="160" rx="2" fill="#4d7c0f"/>
<rect x="83" y="185" width="9" height="3" fill="#365314"/>
<rect x="83" y="215" width="9" height="3" fill="#365314"/>
<rect x="83" y="245" width="9" height="3" fill="#365314"/>
<rect x="83" y="275" width="9" height="3" fill="#365314"/>
<!-- 竹叶 -->
<path d="M 90 175 Q 110 165 120 175 Q 110 180 90 175" fill="#4d7c0f"/>
<path d="M 90 170 Q 75 158 65 168 Q 75 173 90 170" fill="#65a30d"/>
<path d="M 90 205 Q 110 195 125 205 Q 110 210 90 205" fill="#4d7c0f"/>
<path d="M 90 200 Q 72 190 60 200 Q 72 205 90 200" fill="#65a30d"/>
<path d="M 90 235 Q 108 225 118 235 Q 108 240 90 235" fill="#4d7c0f"/>
<path d="M 90 265 Q 110 255 122 265 Q 110 270 90 265" fill="#65a30d"/>
</g>
<g>
<rect x="630" y="170" width="5" height="150" rx="2" fill="#4d7c0f"/>
<rect x="628" y="195" width="9" height="3" fill="#365314"/>
<rect x="628" y="225" width="9" height="3" fill="#365314"/>
<rect x="628" y="255" width="9" height="3" fill="#365314"/>
<path d="M 635 185 Q 615 175 605 185 Q 615 190 635 185" fill="#4d7c0f"/>
<path d="M 635 180 Q 650 168 660 178 Q 650 183 635 180" fill="#65a30d"/>
<path d="M 635 215 Q 615 205 600 215 Q 615 220 635 215" fill="#4d7c0f"/>
<path d="M 635 245 Q 650 235 662 245 Q 650 250 635 245" fill="#65a30d"/>
</g>
<!-- 左下：文化卡片1 -->
<g filter="url(#shadow)">
<rect x="20" y="165" width="140" height="55" rx="8" fill="url(#card1)"/>
<text x="90" y="188" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u4f20\u7edf\u6587\u5316</text>
<text x="90" y="203" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#fecaca">Heritage</text>
</g>
<!-- 右下：文化卡片2 -->
<g filter="url(#shadow)">
<rect x="560" y="165" width="140" height="55" rx="8" fill="url(#card2)"/>
<text x="630" y="188" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u827a\u672f\u5ba1\u7f8e</text>
<text x="630" y="203" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#fef3c7">Aesthetics</text>
</g>
<!-- 中央：书法卷轴 -->
<g filter="url(#shadow)">
<rect x="220" y="80" width="280" height="130" rx="6" fill="#fff" opacity="0.9"/>
<rect x="210" y="75" width="10" height="140" rx="3" fill="#78350f"/>
<rect x="500" y="75" width="10" height="140" rx="3" fill="#78350f"/>
<!-- 书法文字 -->
<text x="360" y="120" text-anchor="middle" font-family="Microsoft YaHei,serif" font-size="28" fill="#451a03" font-weight="bold">\u6587\u5316</text>
<text x="360" y="150" text-anchor="middle" font-family="Microsoft YaHei,serif" font-size="14" fill="#78350f" opacity="0.7">\u4ee5\u6587\u5316\u4eba</text>
<text x="360" y="180" text-anchor="middle" font-family="Microsoft YaHei,serif" font-size="11" fill="#a16207" opacity="0.5">\u4ee5\u4eba\u5316\u5df1</text>
</g>
<!-- 印章 -->
<g transform="translate(470,145)">
<rect x="-15" y="-15" width="30" height="30" rx="2" fill="#dc2626" opacity="0.85"/>
<text y="3" text-anchor="middle" font-family="Microsoft YaHei,serif" font-size="12" fill="#fff" font-weight="bold">\u5370</text>
</g>
<!-- 飞鸟 -->
<g opacity="0.4">
<path d="M 200 90 Q 208 86 216 90 Q 224 86 232 90" fill="none" stroke="#451a03" stroke-width="1.5" stroke-linecap="round"/>
<path d="M 450 75 Q 458 71 466 75 Q 474 71 482 75" fill="none" stroke="#451a03" stroke-width="1.2" stroke-linecap="round"/>
</g>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#451a03" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#fde68a">{safe}</text>
</svg>'''


def _scene_abstract(topic: str, p: dict) -> str:
    """抽象场景：亮色流动光带 + 几何粒子 + 星空感"""
    safe = html.escape(topic)
    return f'''<svg width="720" height="420" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#ede9fe"/><stop offset="0.3" stop-color="#ddd6fe"/><stop offset="0.6" stop-color="#c4b5fd"/><stop offset="1" stop-color="#a78bfa"/>
</linearGradient>
<radialGradient id="glow1" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#f0abfc" stop-opacity="0.5"/><stop offset="1" stop-color="#ede9fe" stop-opacity="0"/>
</radialGradient>
<radialGradient id="glow2" cx="0.5" cy="0.5" r="0.5">
<stop offset="0" stop-color="#67e8f9" stop-opacity="0.4"/><stop offset="1" stop-color="#ede9fe" stop-opacity="0"/>
</radialGradient>
<linearGradient id="band1" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#8b5cf6" stop-opacity="0"/><stop offset="0.3" stop-color="#8b5cf6" stop-opacity="0.6"/><stop offset="0.7" stop-color="#a78bfa" stop-opacity="0.4"/><stop offset="1" stop-color="#c4b5fd" stop-opacity="0"/>
</linearGradient>
<linearGradient id="band2" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#06b6d4" stop-opacity="0"/><stop offset="0.4" stop-color="#22d3ee" stop-opacity="0.5"/><stop offset="0.8" stop-color="#67e8f9" stop-opacity="0.3"/><stop offset="1" stop-color="#a5f3fc" stop-opacity="0"/>
</linearGradient>
<linearGradient id="band3" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#ec4899" stop-opacity="0"/><stop offset="0.5" stop-color="#f472b6" stop-opacity="0.4"/><stop offset="1" stop-color="#f9a8d4" stop-opacity="0"/>
</linearGradient>
<linearGradient id="card1" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#8b5cf6"/><stop offset="1" stop-color="#6d28d9"/>
</linearGradient>
<linearGradient id="card2" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#06b6d4"/><stop offset="1" stop-color="#0891b2"/>
</linearGradient>
<filter id="shadow"><feDropShadow dx="2" dy="5" stdDeviation="6" flood-color="#4c1d95" flood-opacity="0.2"/></filter>
<filter id="shadowSm"><feDropShadow dx="1" dy="3" stdDeviation="4" flood-color="#4c1d95" flood-opacity="0.15"/></filter>
<filter id="blur"><feGaussianBlur stdDeviation="3"/></filter>
</defs>
<!-- 亮色背景 -->
<rect width="720" height="420" fill="url(#bg)"/>
<!-- 光晕 -->
<circle cx="200" cy="100" r="150" fill="url(#glow1)"/>
<circle cx="560" cy="280" r="170" fill="url(#glow2)"/>
<!-- 流动光带 -->
<path d="M -20 120 Q 180 60 360 140 T 740 100" fill="none" stroke="url(#band1)" stroke-width="40" opacity="0.5" filter="url(#blur)"/>
<path d="M -20 120 Q 180 60 360 140 T 740 100" fill="none" stroke="url(#band1)" stroke-width="3" opacity="0.6"/>
<path d="M -20 200 Q 200 260 400 180 T 740 220" fill="none" stroke="url(#band2)" stroke-width="35" opacity="0.4" filter="url(#blur)"/>
<path d="M -20 200 Q 200 260 400 180 T 740 220" fill="none" stroke="url(#band2)" stroke-width="2.5" opacity="0.5"/>
<path d="M -20 280 Q 150 220 360 300 T 740 270" fill="none" stroke="url(#band3)" stroke-width="30" opacity="0.35" filter="url(#blur)"/>
<path d="M -20 280 Q 150 220 360 300 T 740 270" fill="none" stroke="url(#band3)" stroke-width="2" opacity="0.45"/>
<!-- 几何粒子 -->
<g opacity="0.7">
<circle cx="80" cy="60" r="4" fill="#8b5cf6"/>
<circle cx="150" cy="90" r="3" fill="#06b6d4"/>
<circle cx="240" cy="50" r="5" fill="#ec4899"/>
<circle cx="320" cy="80" r="3" fill="#8b5cf6"/>
<circle cx="450" cy="55" r="4" fill="#06b6d4"/>
<circle cx="550" cy="85" r="3" fill="#ec4899"/>
<circle cx="640" cy="60" r="5" fill="#8b5cf6"/>
<circle cx="690" cy="100" r="3" fill="#06b6d4"/>
<circle cx="60" cy="340" r="4" fill="#06b6d4"/>
<circle cx="140" cy="370" r="3" fill="#ec4899"/>
<circle cx="250" cy="350" r="4" fill="#8b5cf6"/>
<circle cx="400" cy="370" r="3" fill="#06b6d4"/>
<circle cx="500" cy="345" r="4" fill="#ec4899"/>
<circle cx="620" cy="365" r="3" fill="#8b5cf6"/>
<circle cx="680" cy="340" r="4" fill="#06b6d4"/>
</g>
<!-- 几何形状 -->
<g opacity="0.3">
<circle cx="120" cy="180" r="25" fill="none" stroke="#8b5cf6" stroke-width="2"/>
<polygon points="600,200 620,180 640,200 620,220" fill="none" stroke="#06b6d4" stroke-width="2"/>
<circle cx="360" cy="210" r="40" fill="none" stroke="#ec4899" stroke-width="1.5" stroke-dasharray="4,4"/>
</g>
<!-- 中央哲学圆 -->
<g transform="translate(360,200)">
<circle r="55" fill="none" stroke="#fff" stroke-width="1" opacity="0.5" stroke-dasharray="3,3"/>
<circle r="40" fill="#fff" opacity="0.15"/>
<circle r="30" fill="#8b5cf6" opacity="0.2" filter="url(#blur)"/>
<circle r="20" fill="#fff" opacity="0.3"/>
<text y="5" text-anchor="middle" font-family="Microsoft YaHei,serif" font-size="14" fill="#4c1d95" font-weight="bold">\u9053</text>
</g>
<!-- 左下卡片 -->
<g filter="url(#shadow)">
<rect x="20" y="275" width="140" height="50" rx="8" fill="url(#card1)"/>
<text x="90" y="298" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u54f2\u5b66\u601d\u8003</text>
<text x="90" y="313" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#ede9fe">Philosophy</text>
</g>
<!-- 右下卡片 -->
<g filter="url(#shadow)">
<rect x="560" y="275" width="140" height="50" rx="8" fill="url(#card2)"/>
<text x="630" y="298" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="10" fill="#fff" font-weight="bold">\u4eba\u751f\u610f\u4e49</text>
<text x="630" y="313" text-anchor="middle" font-family="Microsoft YaHei,sans-serif" font-size="7" fill="#cffafe">Meaning</text>
</g>
<!-- 标题 -->
<rect x="170" y="378" width="380" height="34" rx="17" fill="#4c1d95" opacity="0.9" filter="url(#shadowSm)"/>
<text x="360" y="400" text-anchor="middle" font-family="Microsoft YaHei,PingFang SC,sans-serif" font-size="15" font-weight="bold" fill="#ddd6fe">{safe}</text>
</svg>'''


# ═══════════════════════════════════════════════════════════
# SVG 生成 → PNG 转换 → 剪贴板写入 → 粘贴
# ═══════════════════════════════════════════════════════════


def generate_svg(topic: str, content: str = "") -> str:
    """根据文章主题和内容，生成场景插画式 SVG 配图"""
    scene = _detect_scene(topic, content)
    print(f"[image_gen] 场景: {scene}")
    # 所有场景模板共用统一的 p 参数（预留配色方案）
    p = {}
    templates = {
        "tech": _scene_tech,
        "business": _scene_business,
        "education": _scene_education,
        "lifestyle": _scene_lifestyle,
        "nature": _scene_nature,
        "city": _scene_city,
        "culture": _scene_culture,
        "abstract": _scene_abstract,
    }
    fn = templates.get(scene, _scene_abstract)
    print(f"[image_gen] 模板: {fn.__name__}")
    return fn(topic, p)


# 浏览器 Canvas API 将 SVG 渲染为 PNG
SVG_TO_PNG_JS = """(async (svgCode) => {
  const img = new Image();
  const svgBlob = new Blob([svgCode], { type: 'image/svg+xml' });
  const url = URL.createObjectURL(svgBlob);
  await new Promise((resolve, reject) => {
    img.onload = resolve;
    img.onerror = reject;
    img.src = url;
  });
  const canvas = document.createElement('canvas');
  canvas.width = 720;
  canvas.height = 420;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, 720, 420);
  ctx.drawImage(img, 0, 0, 720, 420);
  URL.revokeObjectURL(url);
  const dataUrl = canvas.toDataURL('image/png');
  return dataUrl;
})"""


def _fix_javascript_string(fn: str) -> str:
    """修复 JS 函数字符串格式，使其符合 browser-use Page.evaluate() 的要求
    
    browser-use 要求传入的 JS 字符串必须以 `(` 开头且包含 `=>`，
    即 IIFE 格式：`(async (arg) => { ... })(arg)`
    """
    s = fn.strip()
    if not s.startswith('('):
        # 如果不以 ( 开头，包装成 IIFE
        if s.startswith('async'):
            s = '(' + s + ')'
        else:
            s = '(async () => { ' + s + ' })()'
    return s


async def generate_and_paste_image(
    article_topic: str,
    article_content: str,
    page
) -> ActionResult:
    """生成 SVG 配图 → PNG → 剪贴板 → Ctrl+V 粘贴到编辑器

    Args:
        article_topic: 文章标题（用于场景识别和标题文字）
        article_content: 文章正文（辅助场景识别）
        page: browser-use Page 对象
    
    Returns:
        ActionResult with success/failure info
    """
    import time
    
    try:
        # 1. 生成 SVG
        svg_code = generate_svg(article_topic, article_content)
        if not svg_code:
            return ActionResult(error="SVG 生成失败")
        
        print(f"[image_gen] SVG 生成成功，{len(svg_code)} chars")
        
        # 2. 通过浏览器 Canvas 转换为 PNG
        js_fn = _fix_javascript_string(SVG_TO_PNG_JS)
        # browser-use 的 page.evaluate 会自动传参
        data_url = await page.evaluate(js_fn, svg_code)
        
        if not data_url or not data_url.startswith('data:image/png'):
            return ActionResult(error="PNG 转换失败：未返回有效的 data URL")
        
        print(f"[image_gen] PNG 转换成功，{len(data_url)} chars data URL")
        
        # 3. 将 PNG 写入 Windows 系统剪贴板
        # 从 data URL 中提取 base64 数据
        base64_data = data_url.split(',')[1]
        png_bytes = base64.b64decode(base64_data)
        
        # 使用 PIL 确保 PNG 格式正确
        img = Image.open(io.BytesIO(png_bytes))
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # 转换为 BMP 格式写入剪贴板（Windows 剪贴板原生支持 BMP）
        output = io.BytesIO()
        # BMP 格式需要去掉 alpha 通道
        img_rgb = Image.new('RGB', img.size, (255, 255, 255))
        img_rgb.paste(img, mask=img.split()[3] if img.mode == 'RGBA' else None)
        img_rgb.save(output, 'BMP')
        bmp_data = output.getvalue()[14:]  # 去掉 BMP 文件头前14字节
        
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_DIB, bmp_data)
        win32clipboard.CloseClipboard()
        
        print(f"[image_gen] 图片已写入剪贴板")
        
        # 4. 在编辑器中粘贴
        # 先尝试聚焦编辑器
        try:
            # 尝试找到编辑器元素并聚焦
            ed = await page.query_selector('.ProseMirror, .public-DraftEditor-content, [contenteditable="true"]')
            if ed:
                await ed.click()
                await ed.focus()
                print(f"[image_gen] 编辑器已聚焦")
        except Exception:
            pass
        
        # 粘贴（重试机制）
        pasted = False
        for attempt in range(3):
            try:
                await page.press('Control+v')
                await asyncio.sleep(0.5)
                pasted = True
                print(f"[image_gen] 粘贴成功 (attempt {attempt + 1})")
                break
            except Exception as e:
                print(f"[image_gen] 粘贴重试 {attempt + 1}: {e}")
                await asyncio.sleep(0.3)
        
        if not pasted:
            return ActionResult(error="Ctrl+V 粘贴失败")
        
        # 等待图片上传/渲染
        await asyncio.sleep(1.5)
        
        return ActionResult(
            extracted_content=f"配图已生成并粘贴（场景: {_detect_scene(article_topic, article_content)}）",
            include_in_memory=True
        )
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return ActionResult(error=f"配图生成失败: {str(e)}")

