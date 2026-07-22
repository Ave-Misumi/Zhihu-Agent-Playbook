"""Auto-memory: 运行时自动记录页面元素选择器到 memory/ 文件夹

通过 register_new_step_callback 钩子，每步完成后自动从 BrowserStateSummary 
的 selector_map 中提取高价值元素的选择器，合并写入 zhihu_playbook.json。
无需 LLM 手动调用 save_to_playbook。
"""
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Any

MEMORY_DIR = Path(__file__).parent.parent / "memory"
PLAYBOOK_FILE = MEMORY_DIR / "zhihu_playbook.json"
AUTO_LOG_FILE = MEMORY_DIR / "auto_memory_log.jsonl"


def _ensure_dirs():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def load_playbook() -> dict:
    _ensure_dirs()
    if PLAYBOOK_FILE.exists():
        with open(PLAYBOOK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_playbook(data: dict):
    _ensure_dirs()
    with open(PLAYBOOK_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ─── URL → Page Name ──────────────────────────────────────────
def _url_to_page_name(url: str) -> str:
    """从 URL 提取页面对应的语义名称"""
    if not url:
        return "unknown"
    if "zhuanlan.zhihu.com/write" in url:
        return "zhihu_write"
    if "zhuanlan.zhihu.com/p/" in url:
        return "zhihu_article"
    if "zhihu.com/search" in url:
        return "zhihu_search"
    if "zhihu.com/signin" in url:
        return "zhihu_login"
    if "zhihu.com/question" in url:
        return "zhihu_question"
    # 归约通用子域名
    m = re.search(r"https?://([^/]+)", url)
    host = m.group(1) if m else url
    # 去掉 www/子域名前缀，只保留域名主体
    domain = re.sub(r"^(www\d?\.)?", "", host)
    domain = re.sub(r"\..*$", "", domain)
    return domain or "unknown"


# ─── Element → Human-readable label ──────────────────────────
def _element_label(node: Any) -> str:
    """从 EnhancedDOMTreeNode 提取人类可读的元素描述"""
    tag = getattr(node, "node_name", "").lower().strip() or "element"
    attrs = getattr(node, "attributes", {}) or {}

    # 1) aria-label
    aria = attrs.get("aria-label", "").strip()
    if aria:
        return f"{tag}({aria})"

    # 2) placeholder
    ph = attrs.get("placeholder", "").strip()
    if ph:
        return f"{tag}[placeholder={ph[:30]}]"

    # 3) class 中语义化部分
    cls = attrs.get("class", "")
    if cls:
        parts = [p for p in cls.split() if len(p) > 2 and not p.startswith("css-")][:2]
        if parts:
            return f"{tag}.{'.'.join(parts[:2])}"

    # 4) id
    eid = attrs.get("id", "").strip()
    if eid:
        return f"{tag}#{eid}"

    # 5) ax_node / snapshot_node text
    ax = getattr(node, "ax_node", None)
    if ax and getattr(ax, "name", None):
        name = ax.name.strip()
        if name and len(name) < 50:
            return f"{tag}「{name}」"

    snap = getattr(node, "snapshot_node", None)
    if snap and getattr(snap, "node_value", None):
        val = snap.node_value.strip()
        if val and len(val) < 40:
            return f"{tag}「{val}」"

    return tag


def _build_selector(node: Any) -> str | None:
    """为 DOM 节点构造一个稳定的 CSS selector"""
    attrs = getattr(node, "attributes", {}) or {}
    tag = getattr(node, "node_name", "").lower().strip() or ""

    # 优先级：aria-label → data-* → id → class链 → nth-of-type

    # aria-label
    aria = attrs.get("aria-label", "").strip()
    if aria and len(aria) < 60:
        # 转义内部引号
        safe = aria.replace('"', '\\"')
        sel = f'{tag}[aria-label="{safe}"]'
        # 验证选择器不要太长
        if len(sel) < 120:
            return sel

    # data-testid
    for dk in ("data-testid", "data-test", "data-test-id"):
        dv = attrs.get(dk, "").strip()
        if dv and len(dv) < 40:
            return f'{tag}[{dk}="{dv}"]'

    # role + name pattern
    role = attrs.get("role", "").strip()
    if role and aria:
        safe = aria.replace('"', '\\"')
        return f'{tag}[role="{role}"][aria-label="{safe}"]'

    # id
    eid = attrs.get("id", "").strip()
    if eid and re.match(r"^[a-zA-Z][\w-]*$", eid):
        return f"#{eid}"

    # placeholder (input)
    ph = attrs.get("placeholder", "").strip()
    if ph and len(ph) < 40:
        safe = ph.replace('"', '\\"')
        return f'{tag}[placeholder="{safe}"]'

    # 类名链（过滤随机化的 css-xxx）
    cls = attrs.get("class", "")
    if cls:
        parts = [c for c in cls.split() if len(c) > 2 and not re.match(r"^css-[a-z0-9]{4,}$", c)]
        if parts:
            return f"{tag}.{'.'.join(parts[:3])}"

    return None


import re as _re

# 动态内容过滤正则：匹配 "赞同 N"、"反对 N"、"N条通知"、"N条私信" 等
_DYNAMIC_PATTERNS = [
    _re.compile(r'^赞同\s+\d'),          # 赞同 62, 赞同 2.8 万
    _re.compile(r'^反对\s*\d*'),          # 反对, 反对 3
    _re.compile(r'^\d+条通知$'),           # 8条通知, 10条通知
    _re.compile(r'^\d+条私信$'),           # 8条私信, 1条私信
    _re.compile(r'^\d+条评论$'),           # N条评论
    _re.compile(r'话题下的优秀答主'),        # XX话题下的优秀答主
    _re.compile(r'^已认证'),               # 已认证机构号
    _re.compile(r'知势榜'),                # 知势榜影响力榜XX领域上榜答主
    _re.compile(r'^作家，'),               # 作家，代表作...
    _re.compile(r'^宁波维特'),              # 具体公司名
    _re.compile(r'法定代表人$'),
    _re.compile(r'持证人$'),               # XX执业资格证持证人
    _re.compile(r'^开源项目'),              # 开源项目XX 作者
    _re.compile(r'\d+个话题下'),           # XX等N个话题下的优秀答主
    _re.compile(r'^互动量'),               # 互动量
    _re.compile(r'投放「'),                # 投放计划类广告
    # 用户身份信息（具体学校/公司/头衔，每次浏览不同用户都不同）
    _re.compile(r'大学.*硕士'),             # XX大学 XX硕士
    _re.compile(r'大学.*博士'),             # XX大学 XX博士
    _re.compile(r'University.*PhD'),        # Gachon University 药物学博士
    _re.compile(r'University.*Master'),
    _re.compile(r'行业.*从业人员'),          # XX行业 从业人员
    _re.compile(r'行业.*经营者'),
    _re.compile(r'行业.*COO'),
    _re.compile(r'行业.*商学院副教授'),
    _re.compile(r'集团.*员工'),             # XX集团 员工
    _re.compile(r'科技.*董事'),             # XX科技 董事
    _re.compile(r'科技.*经理'),
    _re.compile(r'网络科技.*经理'),
    _re.compile(r'博士后$'),                # XX博士后
    _re.compile(r'博士在读'),               # XX博士在读
    _re.compile(r'^知名'),                  # 知名导演、编剧
    _re.compile(r'^社会学家'),
    _re.compile(r'^全球'),                  # 全球人工智能教育...
    _re.compile(r'DeepLearning'),
    _re.compile(r'导演.*编剧'),
    _re.compile(r'^新知答主'),
    _re.compile(r'上榜答主'),
    _re.compile(r'官方账号$'),              # 知乎 官方账号
]


def _is_dynamic_label(aria: str, placeholder: str = "") -> bool:
    """检测 aria-label 或 placeholder 是否为动态内容（不应记录到 playbook）"""
    text = aria or placeholder
    if not text:
        return False
    for pat in _DYNAMIC_PATTERNS:
        if pat.search(text):
            return True
    # placeholder 包含具体搜索词（热搜榜标题）通常也是动态的
    # 只保留通用 placeholder（请输入标题、搜索你感兴趣的内容等）
    if placeholder and len(placeholder) > 2:
        if '请输入' not in placeholder and '搜索你感兴趣' not in placeholder:
            return True
    return False


def _is_valuable_element(node: Any) -> bool:
    """判断元素是否有记录价值（交互元素 + 有语义标签 + 非动态内容）"""
    tag = getattr(node, "node_name", "").lower().strip()
    if tag not in ("a", "button", "input", "textarea", "select"):
        return False

    attrs = getattr(node, "attributes", {}) or {}

    # 必须有 aria-label 或 placeholder 或 role
    aria = attrs.get("aria-label", "").strip()
    placeholder = attrs.get("placeholder", "").strip()

    # ★ 动态内容过滤：赞同N、通知N条、热搜词、答主身份等
    if _is_dynamic_label(aria, placeholder):
        return False

    if aria:
        # 过滤噪音标签
        NOISE_LABELS = {
            "关闭", "返回", "更多", "分享", "举报", "删除", "编辑", "收", "上移", "下移",
            "点赞", "喜欢", "关注", "取消关注", "收藏", "内容管理",
            "消息", "私信", "通知", "首页", "搜索", "设置", "退出",
            "写文章",
            "创建", "新建", "发布", "提交", "取消", "确定",
            "上一页", "下一页", "加载更多",
            "复制", "粘贴", "全选", "撤销", "重做",
            "播放", "暂停", "静音", "音量",
            "展开", "收起", "折叠",
        }
        USEFUL_NOISE = {"写文章", "发布文章", "保存草稿", "预览", "插入链接", "插入图片", "插入视频"}
        if aria in NOISE_LABELS and aria not in USEFUL_NOISE:
            return False
        # aria-label 必须 ≥2 个中文字符才记录
        if len([c for c in aria if '\u4e00' <= c <= '\u9fff']) < 2:
            return False
        return True
    if placeholder:
        # 已被 _is_dynamic_label 过滤的不会到这里
        # 只保留通用 placeholder（请输入标题、搜索你感兴趣的内容等）
        if '请输入' in placeholder or '搜索你感兴趣' in placeholder:
            return True
        return False

    role = attrs.get("role", "").strip()
    if role in ("button", "link", "textbox", "searchbox", "combobox", "menuitem"):
        return True

    # ax_node 有语义
    ax = getattr(node, "ax_node", None)
    if ax and getattr(ax, "name", None):
        name = ax.name.strip()
        if name and 2 < len(name) < 50:
            return True

    return False


# ─── Core: Step callback ──────────────────────────────────────
class AutoMemoryCollector:
    """自动收集每个 step 的页面元素，持久化到 memory/"""

    def __init__(self):
        self.new_elements_this_run: dict[str, int] = {}  # page_name -> count

    def __call__(self, browser_state: Any, agent_output: Any, step_num: int):
        """对应 register_new_step_callback(browser_state, agent_output, step)"""
        try:
            url = getattr(browser_state, "url", "") or ""
            dom = getattr(browser_state, "dom_state", None)
            if dom is None:
                return

            selector_map = getattr(dom, "selector_map", None)
            if not selector_map:
                return

            page = _url_to_page_name(url)
            playbook = load_playbook()
            page_elements = playbook.get(page, {})
            starting_count = len(page_elements)
            saved_this_step = 0

            for idx, node in selector_map.items():
                if not _is_valuable_element(node):
                    continue
                label = _element_label(node)
                if not label:
                    continue
                selector = _build_selector(node)
                if not selector:
                    continue

                # 不去重：同一 label 可能被多个不同元素使用；用 selector 作为 key
                if label in page_elements:
                    continue  # 已存在则跳过

                page_elements[label] = selector
                saved_this_step += 1

                # 同时记录一条持久化日志
                _append_auto_log({
                    "ts": datetime.now().isoformat(),
                    "step": step_num,
                    "page": page,
                    "label": label,
                    "selector": selector,
                    "url": url,
                })

            if saved_this_step > 0:
                playbook[page] = page_elements
                save_playbook(playbook)
                self.new_elements_this_run[page] = (
                    self.new_elements_this_run.get(page, 0) + saved_this_step
                )
                print(
                    f"[AutoMemory] Step {step_num} | {page}: "
                    f"+{saved_this_step} new, "
                    f"total {len(page_elements)} (hit rate on this page: "
                    f"{starting_count}/{starting_count + saved_this_step} reused)"
                )

        except Exception as e:
            # 静默失败，不能影响 Agent 主流程
            print(f"[AutoMemory] ⚠️ Step {step_num} error (non-fatal): {e}")


def _append_auto_log(entry: dict):
    _ensure_dirs()
    with open(AUTO_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── 导出：创建回调 ──────────────────────────────────────────
def create_auto_memory_callback() -> AutoMemoryCollector:
    """返回一个可传给 register_new_step_callback 的 callable"""
    return AutoMemoryCollector()
