#!/usr/bin/env python3
"""m3u-epg-validator: 订阅测活 + 分组 + 按名去重 + 自然排序

修改说明：
- CCTV+ 显示为「CCTV+新闻N」，避免与 CCTV-1 混淆
- 支持同名多线路（KEEP_MULTI_LINE=True，默认最多 2 条）
- 分组借鉴 Collect-IPTV（优先级从高到低）：
  1) geo 市县→省（pca-code 最长匹配）
  2) groups.txt（繁简归一 + 最长关键词优先）
  3) 主题智能兜底（港澳台/体育/少儿/轮播/MV）
  4) 📂其他频道
"""
from __future__ import annotations

import concurrent.futures
import gzip
import ipaddress
import json
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CONFIG, OUTPUT = ROOT / "config", ROOT / "output"
SUBSCRIBE_FILE = CONFIG / "subscribe.txt"
SOURCES_FALLBACK = CONFIG / "sources.txt"
EPG_FILE = CONFIG / "epg.txt"
BLACKLIST_FILE = CONFIG / "blacklist.txt"
WHITELIST_FILE = CONFIG / "whitelist.txt"
GROUPS_FILE = CONFIG / "groups.txt"
GEO_CACHE_FILE = CONFIG / "geo_province_tokens.json"

CONCURRENCY, TIMEOUT, EPG_TIMEOUT = 40, 8, 30
MAX_CHANNELS_PER_SOURCE = 2000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
RAW_BASE = "https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output"

# 同名多线路开关：True=最多保留 MAX_LINES_PER_CHANNEL 条不同 URL；False=只留最快 1 条
KEEP_MULTI_LINE = True
MAX_LINES_PER_CHANNEL = 2

JUNK_PATTERNS = [r"^更新时间", r"^更新$", r"关于本源", r"^声明", r"^公告", r"^\d{4}[-/年]\d{1,2}"]

GROUP_ORDER = [
    "📺央视频道", "📡卫视频道", "🇭🇰港澳台", "🔁轮播影院", "🎵MV点播",
    "⚽体育频道", "🧸少儿频道",
    "☘️浙江频道", "☘️江西频道", "☘️广东频道", "☘️福建频道", "☘️上海频道",
    "☘️北京频道", "☘️江苏频道", "☘️湖南频道", "☘️四川频道", "☘️湖北频道",
    "☘️河南频道", "☘️山东频道", "☘️河北频道", "☘️安徽频道", "☘️辽宁频道",
    "☘️吉林频道", "☘️黑龙江频道", "☘️陕西频道", "☘️山西频道", "☘️重庆频道",
    "☘️天津频道", "☘️云南频道", "☘️贵州频道", "☘️广西频道", "☘️海南频道",
    "☘️内蒙古频道", "☘️新疆频道", "☘️西藏频道", "☘️宁夏频道", "☘️青海频道",
    "☘️甘肃频道", "📍地方综合", "📂其他频道",
]

# 行政区划名 → 本项目分组名
PROVINCE_NAME_TO_GROUP: Dict[str, str] = {
    "北京": "☘️北京频道", "北京市": "☘️北京频道", "上海": "☘️上海频道",
    "上海市": "☘️上海频道", "天津": "☘️天津频道", "天津市": "☘️天津频道",
    "重庆": "☘️重庆频道", "重庆市": "☘️重庆频道", "河北": "☘️河北频道",
    "河北省": "☘️河北频道", "山西": "☘️山西频道", "山西省": "☘️山西频道",
    "辽宁": "☘️辽宁频道", "辽宁省": "☘️辽宁频道", "吉林": "☘️吉林频道",
    "吉林省": "☘️吉林频道", "黑龙江": "☘️黑龙江频道", "黑龙江省": "☘️黑龙江频道",
    "江苏": "☘️江苏频道", "江苏省": "☘️江苏频道", "浙江": "☘️浙江频道",
    "浙江省": "☘️浙江频道", "安徽": "☘️安徽频道", "安徽省": "☘️安徽频道",
    "福建": "☘️福建频道", "福建省": "☘️福建频道", "江西": "☘️江西频道",
    "江西省": "☘️江西频道", "山东": "☘️山东频道", "山东省": "☘️山东频道",
    "河南": "☘️河南频道", "河南省": "☘️河南频道", "湖北": "☘️湖北频道",
    "湖北省": "☘️湖北频道", "湖南": "☘️湖南频道", "湖南省": "☘️湖南频道",
    "广东": "☘️广东频道", "广东省": "☘️广东频道", "海南": "☘️海南频道",
    "海南省": "☘️海南频道", "四川": "☘️四川频道", "四川省": "☘️四川频道",
    "贵州": "☘️贵州频道", "贵州省": "☘️贵州频道", "云南": "☘️云南频道",
    "云南省": "☘️云南频道", "陕西": "☘️陕西频道", "陕西省": "☘️陕西频道",
    "甘肃": "☘️甘肃频道", "甘肃省": "☘️甘肃频道", "青海": "☘️青海频道",
    "青海省": "☘️青海频道", "台湾": "🇭🇰港澳台", "台湾省": "🇭🇰港澳台",
    "内蒙古": "☘️内蒙古频道", "内蒙古自治区": "☘️内蒙古频道", "广西": "☘️广西频道",
    "广西壮族自治区": "☘️广西频道", "西藏": "☘️西藏频道", "西藏自治区": "☘️西藏频道",
    "宁夏": "☘️宁夏频道", "宁夏回族自治区": "☘️宁夏频道", "新疆": "☘️新疆频道",
    "新疆维吾尔自治区": "☘️新疆频道", "香港": "🇭🇰港澳台", "香港特别行政区": "🇭🇰港澳台",
    "澳门": "🇭🇰港澳台", "澳门特别行政区": "🇭🇰港澳台",
}

PROVINCE_ALIASES: Dict[str, Set[str]] = {
    "北京": {"北京台", "BTV"},
    "上海": {"上海台", "东方明珠", "沪上"},
    "天津": {"天津台"},
    "重庆": {"重庆台"},
    "河北": {"河北台"},
    "山西": {"山西台", "三晋"},
    "辽宁": {"辽宁台", "辽沈"},
    "吉林": {"吉林台"},
    "黑龙江": {"龙江", "黑龙江台"},
    "江苏": {"江苏台", "苏南"},
    "浙江": {"浙江台", "之江"},
    "安徽": {"安徽台"},
    "福建": {"福建台", "八闽"},
    "江西": {"江西台"},
    "山东": {"山东台", "齐鲁"},
    "河南": {"河南台", "中原"},
    "湖北": {"湖北台"},
    "湖南": {"湖南台"},
    "广东": {"广东台", "南粤"},
    "广西": {"广西台"},
    "海南": {"海南台"},
    "四川": {"四川台", "巴蜀"},
    "贵州": {"贵州台"},
    "云南": {"云南台", "七彩云南"},
    "西藏": {"西藏台"},
    "陕西": {"陕西台", "三秦"},
    "甘肃": {"甘肃台", "陇原"},
    "青海": {"青海台"},
    "宁夏": {"宁夏台"},
    "新疆": {"新疆台"},
    "内蒙古": {"内蒙", "内蒙古"},
}

# 非地名词（避免把「新闻/综合」当市县）
NON_GEO_TOKENS = {
    "新闻", "综合", "公共", "生活", "民生", "都市", "经济", "科教", "教育", "少儿",
    "影视", "娱乐", "体育", "文旅", "旅游", "文化", "资讯", "导视", "频道", "电视",
    "法治", "军事", "党建", "购物", "健康", "养生", "时尚", "美食", "游戏", "电竞",
    "戏曲", "戏剧", "曲艺", "纪录", "综艺", "台", "TV", "HD", "SD", "UHD", "FHD",
    "4K", "8K", "卫视", "一套", "二套", "三套", "四套", "五套",
}

IGNORED_GEO_NAMES = {
    "市辖区", "城区", "郊区", "新区", "开发区", "高新区", "矿区", "城区街道",
    "其他", "直辖", "省直辖县级行政区划", "自治区直辖县级行政区划",
    "市辖县", "县级市", "直辖县级", "工业园区", "示范区", "合作区", "管理区",
}

PROVINCE_SUFFIXES = ("特别行政区", "维吾尔自治区", "壮族自治区", "回族自治区", "自治区", "省", "市")
AREA_SUFFIXES = (
    "自治县", "自治州", "自治区", "特别行政区", "新区", "开发区", "高新区",
    "地区", "林区", "矿区", "县", "市", "区", "州", "盟", "旗", "镇", "乡", "街道",
)

ONLINE_GEO_DATA_URLS = [
    "https://raw.githubusercontent.com/modood/Administrative-divisions-of-China/master/dist/pca-code.json",
    "https://fastly.jsdelivr.net/gh/modood/Administrative-divisions-of-China/dist/pca-code.json",
]

# ---------- 智能分组：繁简归一化（借鉴 Collect-IPTV）----------
CHAR_NORMALIZATION_MAP = str.maketrans({
    "頻": "频", "視": "视", "臺": "台", "綜": "综", "聞": "闻", "體": "体",
    "藝": "艺", "經": "经", "濟": "济", "娛": "娱", "樂": "乐", "電": "电",
    "廣": "广", "畫": "画", "劇": "剧", "紀": "纪", "錄": "录", "網": "网",
    "導": "导", "髮": "发", "衛": "卫", "陰": "阴", "陽": "阳", "麗": "丽",
    "龍": "龙", "鄉": "乡", "鎮": "镇", "區": "区", "縣": "县", "灣": "湾",
    "滬": "沪", "閩": "闽", "贛": "赣", "蘇": "苏", "浙": "浙", "魯": "鲁",
    "豫": "豫", "鄂": "鄂", "湘": "湘", "粵": "粤", "瓊": "琼", "渝": "渝",
    "遼": "辽", "寧": "宁", "貴": "贵", "雲": "云", "藏": "藏", "陝": "陕",
    "晉": "晋", "冀": "冀", "錫": "锡", "內": "内", "濛": "蒙", "齊": "齐",
    "鳳": "凤", "華": "华", "國": "国", "際": "际",
})

def normalize_for_match(text: str) -> str:
    """归一化用于匹配：繁简转换 + 去空格标点 + 大写。"""
    if not text:
        return ""
    t = text.translate(CHAR_NORMALIZATION_MAP).strip().upper().replace("＋", "+")
    t = re.sub(r"[ \t\r\n\-_|·•:：,，.。/\\()\[\]【】「」'\"`]+", "", t)
    return t

# 主题智能兜底（优先级低于 geo / groups.txt）
SMART_CATEGORY_RULES: List[Tuple[Tuple[str, ...], str]] = [
    (
        (
            "翡翠台",
            "明珠台",
            "无线新闻",
            "有线新闻",
            "HOY",
            "VIU",
            "凤凰",
            "寰宇",
            "纬来",
            "东森",
            "中天",
            "台视",
            "华视",
            "民视",
            "三立",
            "非凡",
            "年代",
            "TVBS",
            "八大",
            "香港",
            "澳门",
            "台湾",
            "澳亚",
            "星空卫视",
        ),
        "🇭🇰港澳台",
    ),
    (
        (
            "轮播",
            "影院",
            "电影",
            "剧场",
            "电视剧",
            "经典剧",
            "CHC",
            "港鬼片",
            "周星驰",
            "周润发",
            "甄子丹",
            "叶问",
            "仙剑",
            "笑傲江湖",
        ),
        "🔁轮播影院",
    ),
    (("音乐", "MV", "演唱会", "舞曲", "音乐欣赏", "MovieMusic"), "🎵MV点播"),
    (
        ("体育", "足球", "篮球", "网球", "高尔夫", "搏击", "赛事", "NBA", "英超"),
        "⚽体育频道",
    ),
    (
        (
            "少儿",
            "卡通",
            "动漫",
            "动画",
            "童话",
            "嘉佳",
            "小当家",
            "柯南",
            "哆啦A梦",
            "海绵宝宝",
        ),
        "🧸少儿频道",
    ),
]

_SMART_NORMALIZED: List[Tuple[List[str], str]] = [
    (
        sorted(
            [normalize_for_match(k) for k in keys if k],
            key=len,
            reverse=True,
        ),
        group,
    )
    for keys, group in SMART_CATEGORY_RULES
]

NON_GEO_NORMALIZED = {normalize_for_match(t) for t in NON_GEO_TOKENS}
IGNORED_GEO_NORMALIZED = {normalize_for_match(t) for t in IGNORED_GEO_NAMES}

# geo 误伤黑名单：常见频道品牌/台标词，避免被区县同名抢走（如 凤凰县→湖南）
GEO_TOKEN_BLOCKLIST = {
    "凤凰", "星空", "明珠", "翡翠", "无线", "有线", "纬来", "东森", "中天",
    "台视", "华视", "民视", "三立", "非凡", "年代", "八大", "寰宇",
    "央视", "卫视", "CCTV", "CGTN", "CHC", "CETV",
}
GEO_TOKEN_BLOCKLIST_NORMALIZED = {normalize_for_match(t) for t in GEO_TOKEN_BLOCKLIST}

# 运行时由 load_geo_province_matchers() 填充：normalized_token -> group
_GEO_TOKEN_TO_GROUP: Dict[str, str] = {}
_GEO_LOADED = False

@dataclass
class Channel:
    name: str
    url: str
    group: str = "📂其他频道"
    tvg_id: str = ""
    tvg_name: str = ""
    tvg_logo: str = ""
    source_name: str = ""
    is_ipv6: bool = False
    latency_ms: float = 0.0

@dataclass
class SourceEntry:
    name: str
    url: str

def load_lines(path: Path) -> List[str]:
    if not path.exists():
        return []
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]

def load_subscribe() -> List[SourceEntry]:
    path = SUBSCRIBE_FILE if SUBSCRIBE_FILE.exists() else SOURCES_FALLBACK
    entries = []
    for line in load_lines(path):
        if "|" in line:
            name, url = [x.strip() for x in line.split("|", 1)]
        else:
            url = line.strip()
            name = urlparse(url).path.rsplit("/", 1)[-1] or "source"
        if url.startswith(("http://", "https://")):
            entries.append(SourceEntry(name=name, url=url))
    return entries

def load_epg_urls() -> List[str]:
    return [u for u in load_lines(EPG_FILE) if u.startswith(("http://", "https://"))]

def load_keywords(path: Path) -> Set[str]:
    return {kw.lower() for kw in load_lines(path)}

def load_group_rules() -> List[Tuple[str, str]]:
    """加载 groups.txt，返回 (原始关键词, 分组) 列表。"""
    rules = []
    for line in load_lines(GROUPS_FILE):
        if "|" not in line:
            continue
        key, group = [x.strip() for x in line.split("|", 1)]
        if key and group:
            rules.append((key, group))
    return rules

def match_kw(text: str, kws: Set[str]) -> bool:
    t = text.lower()
    return any(k in t for k in kws)

def is_junk(name: str) -> bool:
    return any(re.search(p, name, re.I) for p in JUNK_PATTERNS)

def detect_ipv6(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        return False

def strip_suffix_once(name: str, suffixes: Tuple[str, ...]) -> str:
    for suffix in sorted(suffixes, key=len, reverse=True):
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)]
    return name

def geo_name_variants(name: str) -> Set[str]:
    cleaned = re.sub(r"\s+", "", name)
    if not cleaned:
        return set()
    variants = {cleaned}
    stripped = strip_suffix_once(cleaned, AREA_SUFFIXES)
    if stripped and stripped != cleaned:
        variants.add(stripped)
    return {
        v
        for v in variants
        if len(v) >= 2 and normalize_for_match(v) not in IGNORED_GEO_NORMALIZED
    }

def _iter_named_items(payload) -> List[str]:
    out: List[str] = []
    if isinstance(payload, list):
        for item in payload:
            out.extend(_iter_named_items(item))
    elif isinstance(payload, dict):
        name = payload.get("name")
        if isinstance(name, str) and name.strip():
            out.append(name.strip())
        children = payload.get("children")
        if children is not None:
            out.extend(_iter_named_items(children))
        else:
            for key, value in payload.items():
                if key == "name":
                    continue
                if isinstance(value, (list, dict)):
                    out.extend(_iter_named_items(value))
    return out

def _build_geo_from_pca(payload) -> Dict[str, str]:
    """从 pca-code.json 构建 normalized_token -> group 映射。"""
    token_to_group: Dict[str, str] = {}
    if not isinstance(payload, list):
        return token_to_group

    for node in payload:
        if not isinstance(node, dict):
            continue
        raw_province = str(node.get("name") or "").strip()
        if not raw_province:
            continue
        # 映射到项目分组
        group = PROVINCE_NAME_TO_GROUP.get(raw_province)
        if not group:
            base = strip_suffix_once(raw_province, PROVINCE_SUFFIXES)
            group = PROVINCE_NAME_TO_GROUP.get(base)
        if not group:
            continue

        # 省本名 + 别名
        candidates = {raw_province, strip_suffix_once(raw_province, PROVINCE_SUFFIXES)}
        base = strip_suffix_once(raw_province, PROVINCE_SUFFIXES)
        candidates.update(PROVINCE_ALIASES.get(base, set()))
        for c in candidates:
            n = normalize_for_match(c)
            if len(n) >= 2 and n not in NON_GEO_NORMALIZED and n not in GEO_TOKEN_BLOCKLIST_NORMALIZED:
                token_to_group[n] = group

        # 市 / 区 / 县
        for raw_name in _iter_named_items(node.get("children", [])):
            for variant in geo_name_variants(raw_name):
                n = normalize_for_match(variant)
                if len(n) < 2 or n in NON_GEO_NORMALIZED or n in IGNORED_GEO_NORMALIZED:
                    continue
                if n in GEO_TOKEN_BLOCKLIST_NORMALIZED:
                    continue
                if n not in token_to_group:
                    token_to_group[n] = group

    return token_to_group

def _fetch_json(url: str) -> Optional[object]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as e:
        print(f"  geo 下载失败: {url} -> {e}")
        return None

def load_geo_province_matchers(force_refresh: bool = False) -> Dict[str, str]:
    """
    加载 市县→省 映射。
    优先读本地缓存 config/geo_province_tokens.json；
    没有或 force_refresh 则从 GitHub 拉 pca-code.json 并缓存。
    """
    global _GEO_TOKEN_TO_GROUP, _GEO_LOADED

    if _GEO_LOADED and _GEO_TOKEN_TO_GROUP and not force_refresh:
        return _GEO_TOKEN_TO_GROUP

    # 1) 本地缓存
    if GEO_CACHE_FILE.exists() and not force_refresh:
        try:
            data = json.loads(GEO_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                _GEO_TOKEN_TO_GROUP = {str(k): str(v) for k, v in data.items()}
                _GEO_LOADED = True
                print(f"geo 缓存加载: {len(_GEO_TOKEN_TO_GROUP)} 个地名词")
                return _GEO_TOKEN_TO_GROUP
        except Exception as e:
            print(f"geo 缓存读取失败: {e}")

    # 2) 在线拉取
    token_to_group: Dict[str, str] = {}
    for url in ONLINE_GEO_DATA_URLS:
        payload = _fetch_json(url)
        if payload is None:
            continue
        token_to_group = _build_geo_from_pca(payload)
        if token_to_group:
            print(f"geo 在线加载: {len(token_to_group)} 个地名词 <- {url}")
            break

    if token_to_group:
        try:
            GEO_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            GEO_CACHE_FILE.write_text(
                json.dumps(token_to_group, ensure_ascii=False, indent=0),
                encoding="utf-8",
            )
            print(f"geo 已缓存: {GEO_CACHE_FILE}")
        except Exception as e:
            print(f"geo 缓存写入失败: {e}")

    _GEO_TOKEN_TO_GROUP = token_to_group
    _GEO_LOADED = True
    return _GEO_TOKEN_TO_GROUP

def match_geo_province(normalized_channel: str) -> Optional[str]:
    """按最长地名词命中省份分组（跳过品牌黑名单 token）。"""
    if not _GEO_TOKEN_TO_GROUP or not normalized_channel:
        return None
    best_group = None
    best_len = 0
    for token, group in _GEO_TOKEN_TO_GROUP.items():
        if not token or token not in normalized_channel:
            continue
        if token in GEO_TOKEN_BLOCKLIST_NORMALIZED:
            continue
        if len(token) > best_len:
            best_len = len(token)
            best_group = group
    return best_group

def classify(name: str, rules: List[Tuple[str, str]]) -> str:
    """
    智能分组（优先级从高到低）：
    1. geo 市县→省 最长匹配
    2. groups.txt：最长关键词优先；同长按 GROUP_ORDER 靠前优先
    3. 主题智能兜底（港澳台/体育/少儿/轮播/MV）
    4. 📂其他频道
    """
    if not name:
        return "📂其他频道"

    raw_lower = name.lower()
    normalized = normalize_for_match(name)

    # 1) geo 市县 → 省（优先）
    geo_group = match_geo_province(normalized)
    if geo_group:
        return geo_group

    # 2) groups.txt：最长优先 + GROUP_ORDER
    scored: List[Tuple[int, str]] = []
    for key, group in rules:
        if not key:
            continue
        key_l = key.lower()
        key_n = normalize_for_match(key)
        hit = False
        score = 0
        if key_l and key_l in raw_lower:
            hit = True
            score = len(key_l)
        elif key_n and key_n in normalized:
            hit = True
            score = len(key_n)
        if hit:
            scored.append((score, group))

    if scored:
        rank = {g: i for i, g in enumerate(GROUP_ORDER)}
        scored.sort(key=lambda x: (-x[0], rank.get(x[1], 99), x[1]))
        return scored[0][1]

    # 3) 主题兜底
    for keywords, group in _SMART_NORMALIZED:
        for kw in keywords:
            if kw and kw in normalized:
                return group

    # 4) 其他
    return "📂其他频道"

def normalize_name(name: str) -> str:
    raw = name.strip()
    n = raw.lower()
    n = re.sub(r"\[.*?\]", "", n)
    n = re.sub(r"[\(（].*?[\)）]", "", n)
    n = re.sub(r"[\s\-_·•]+", "", n)
    n = re.sub(
        r"(hd|sd|4k|8k|fhd|uhd|1080p|720p|576i|1080i|600p|综合|财经|综艺|中文国际|"
        r"体育|电影|国防军事|电视剧|纪录|科教|戏曲|社会与法|新闻|少儿|音乐|"
        r"奥林匹克|农业农村)$",
        "",
        n,
        flags=re.I,
    )
    m = re.search(r"cctv\+(\d+)", n)
    if m:
        return "cctvplus" + m.group(1)
    if re.search(r"cctv5\+|cctv\-?5\+", raw.lower().replace(" ", "")):
        return "cctv5plus"
    m = re.search(r"cctv(\d{1,2})", n)
    if m:
        return "cctv" + str(int(m.group(1)))
    cn = {
        "一套": "1",
        "一台": "1",
        "二套": "2",
        "三套": "3",
        "四套": "4",
        "五套": "5",
        "六套": "6",
        "七套": "7",
        "八套": "8",
        "九套": "9",
        "十套": "10",
        "十一套": "11",
        "十二套": "12",
        "十三套": "13",
        "十四套": "14",
        "十五套": "15",
        "十六套": "16",
        "十七套": "17",
    }
    for k, v in cn.items():
        if k in raw:
            return "cctv" + v
    return n

def display_name(key: str, original: str) -> str:
    """统一显示名。CCTV+ 写成 CCTV+新闻N，避免和 CCTV-1 混淆。"""
    m = re.fullmatch(r"cctv(\d{1,2})", key)
    if m:
        return f"CCTV-{int(m.group(1))}"
    if key == "cctv5plus":
        return "CCTV-5+"
    if key.startswith("cctvplus"):
        return "CCTV+新闻" + key.replace("cctvplus", "")
    return original

def natural_key(s: str):
    parts = re.split(r"(\d+)", s.lower())
    out = []
    for p in parts:
        out.append((0, int(p)) if p.isdigit() else (1, p))
    return out

def fetch_bytes(url: str, timeout: int = TIMEOUT) -> Optional[bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        print(f"  下载失败: {url} -> {e}")
        return None

def fetch_text(url: str, timeout: int = TIMEOUT) -> Optional[str]:
    data = fetch_bytes(url, timeout)
    if data is None:
        return None
    if url.endswith(".gz") or data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except Exception:
            pass
    return data.decode("utf-8", errors="ignore")

def download_epg(epg_urls: List[str]) -> List[str]:
    epg_dir = OUTPUT / "epg"
    epg_dir.mkdir(parents=True, exist_ok=True)
    usable = []
    for i, url in enumerate(epg_urls):
        print(f"下载 EPG [{i + 1}/{len(epg_urls)}]: {url}")
        data = fetch_bytes(url, timeout=EPG_TIMEOUT)
        if not data:
            usable.append(url)
            continue
        name = Path(urlparse(url).path).name or f"epg_{i}.xml"
        if not name.endswith((".xml", ".gz", ".xml.gz")):
            name = f"epg_{i}.xml"
        (epg_dir / name).write_bytes(data)
        print(f"  已保存: {name}")
        usable.append(f"{RAW_BASE}/epg/{name}")
    (epg_dir / "epg_urls.txt").write_text("\n".join(usable) + "\n", encoding="utf-8")
    return usable

def parse_m3u(content, source_name, blacklist, whitelist, rules) -> List[Channel]:
    channels, lines, i = [], content.splitlines(), 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            attrs = {
                m.group(1).lower(): m.group(2)
                for m in re.finditer(r'([\w-]+)="([^"]*)"', line)
            }
            name = (
                line.split(",", 1)[-1].strip()
                if "," in line
                else attrs.get("tvg-name", "未知")
            )
            tvg_id = attrs.get("tvg-id", "")
            tvg_name = attrs.get("tvg-name", name)
            tvg_logo = attrs.get("tvg-logo", "")
            i += 1
            while i < len(lines) and (
                not lines[i].strip() or lines[i].strip().startswith("#")
            ):
                i += 1
            if i >= len(lines):
                break
            url = lines[i].strip()
            if not url.startswith(("http://", "https://")):
                i += 1
                continue
            if is_junk(name) or match_kw(name, blacklist) or match_kw(url, blacklist):
                i += 1
                continue
            if whitelist and not (
                match_kw(name, whitelist) or match_kw(url, whitelist)
            ):
                i += 1
                continue
            channels.append(
                Channel(
                    name=name,
                    url=url,
                    group=classify(name, rules),
                    tvg_id=tvg_id,
                    tvg_name=tvg_name,
                    tvg_logo=tvg_logo,
                    source_name=source_name,
                    is_ipv6=detect_ipv6(url),
                )
            )
        i += 1
    return channels

def parse_txt(content, source_name, blacklist, whitelist, rules) -> List[Channel]:
    channels = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.endswith("#genre#"):
            continue
        if "," not in line:
            continue
        name, url = [x.strip() for x in line.split(",", 1)]
        if not url.startswith(("http://", "https://")):
            continue
        if is_junk(name) or match_kw(name, blacklist) or match_kw(url, blacklist):
            continue
        if whitelist and not (match_kw(name, whitelist) or match_kw(url, whitelist)):
            continue
        channels.append(
            Channel(
                name=name,
                url=url,
                group=classify(name, rules),
                source_name=source_name,
                is_ipv6=detect_ipv6(url),
            )
        )
    return channels

def check_stream(ch: Channel) -> Optional[Channel]:
    start = time.perf_counter()
    req = urllib.request.Request(ch.url, headers={"User-Agent": USER_AGENT}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read(256)
            ch.latency_ms = (time.perf_counter() - start) * 1000
            try:
                peer = resp.fp.raw._sock.getpeername()  # type: ignore
                if peer:
                    ch.is_ipv6 = isinstance(
                        ipaddress.ip_address(peer[0]), ipaddress.IPv6Address
                    )
            except Exception:
                pass
            return ch
    except Exception:
        return None

def write_m3u(channels: List[Channel], path: Path, epg_urls: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    epg_attr = ",".join(epg_urls) if epg_urls else ""
    lines = [f'#EXTM3U x-tvg-url="{epg_attr}"' if epg_attr else "#EXTM3U"]
    for ch in channels:
        attrs = []
        if ch.tvg_id:
            attrs.append(f'tvg-id="{ch.tvg_id}"')
        if ch.tvg_name:
            attrs.append(f'tvg-name="{ch.tvg_name}"')
        if ch.tvg_logo:
            attrs.append(f'tvg-logo="{ch.tvg_logo}"')
        attrs.append(f'group-title="{ch.group}"')
        lines.append(f"#EXTINF:-1 {' '.join(attrs)},{ch.name}")
        lines.append(ch.url)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  写入: {path} ({len(channels)} 频道)")

def write_txt(channels: List[Channel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    groups: dict = {}
    for ch in channels:
        groups.setdefault(ch.group, []).append(ch)
    lines = []
    for g in GROUP_ORDER:
        if g not in groups:
            continue
        lines.append(f"{g},#genre#")
        for ch in groups[g]:
            lines.append(f"{ch.name},{ch.url}")
    for g, chs in groups.items():
        if g in GROUP_ORDER:
            continue
        lines.append(f"{g},#genre#")
        for ch in chs:
            lines.append(f"{ch.name},{ch.url}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  写入: {path} ({len(channels)} 频道)")

def main() -> None:
    print("=== m3u-epg-validator 开始 ===")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sources = load_subscribe()
    epg_urls = load_epg_urls()
    blacklist = load_keywords(BLACKLIST_FILE)
    whitelist = load_keywords(WHITELIST_FILE)
    rules = load_group_rules()

    # 市县→省 geo 映射（缓存到 config/geo_province_tokens.json）
    load_geo_province_matchers()

    print(
        f"订阅源: {len(sources)} | EPG: {len(epg_urls)} | "
        f"分组规则: {len(rules)} | geo词: {len(_GEO_TOKEN_TO_GROUP)}"
    )

    saved_epg = download_epg(epg_urls) if epg_urls else []

    all_cands: List[Channel] = []
    for src in sources:
        print(f"拉取源: {src.name} -> {src.url}")
        text = fetch_text(src.url)
        if not text:
            continue
        if "#EXTM3U" in text[:200] or "#EXTINF" in text[:500]:
            chs = parse_m3u(text, src.name, blacklist, whitelist, rules)
        else:
            chs = parse_txt(text, src.name, blacklist, whitelist, rules)
        print(f"  解析得到 {len(chs)} 个频道")
        all_cands.extend(chs[:MAX_CHANNELS_PER_SOURCE])

    print(f"候选: {len(all_cands)}")
    alive: List[Channel] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(check_stream, ch): ch for ch in all_cands}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            done += 1
            if done % 100 == 0 or done == len(all_cands):
                print(f"  测活: {done}/{len(all_cands)}")
            r = fut.result()
            if r:
                alive.append(r)
    print(f"测活通过: {len(alive)}")

    # ---------- 去重 + 统一显示名 + 可选多线路 ----------
    seen_url: Set[str] = set()
    name_count: dict = {}
    unique: List[Channel] = []

    for ch in sorted(alive, key=lambda c: c.latency_ms):
        if ch.url in seen_url:
            continue
        key = normalize_name(ch.name)
        base = display_name(key, ch.name)
        count = name_count.get(key, 0)

        if KEEP_MULTI_LINE:
            if count >= MAX_LINES_PER_CHANNEL:
                continue
            if count == 0:
                ch.name = base
            else:
                ch.name = f"{base}·线路{count + 1}"
        else:
            if count >= 1:
                continue
            ch.name = base

        seen_url.add(ch.url)
        name_count[key] = count + 1
        unique.append(ch)

    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    unique.sort(key=lambda c: (rank.get(c.group, 99), natural_key(c.name)))

    ipv4 = [c for c in unique if not c.is_ipv6]
    ipv6 = [c for c in unique if c.is_ipv6]
    print(f"去重后: {len(unique)} | IPv4 {len(ipv4)} | IPv6 {len(ipv6)}")

    write_m3u(unique, OUTPUT / "result.m3u", saved_epg)
    write_txt(unique, OUTPUT / "result.txt")
    write_m3u(ipv4, OUTPUT / "ipv4" / "result.m3u", saved_epg)
    write_txt(ipv4, OUTPUT / "ipv4" / "result.txt")
    write_m3u(ipv6, OUTPUT / "ipv6" / "result.m3u", saved_epg)
    write_txt(ipv6, OUTPUT / "ipv6" / "result.txt")

    epg0 = saved_epg[0] if saved_epg else (epg_urls[0] if epg_urls else "")
    (OUTPUT / "live_sources_for_libretv.txt").write_text(
        "\n".join(
            [
                f"聚合全部|{RAW_BASE}/result.m3u|{epg0}",
                f"聚合IPv4|{RAW_BASE}/ipv4/result.m3u|{epg0}",
                f"聚合IPv6|{RAW_BASE}/ipv6/result.m3u|{epg0}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("=== 完成 ===")

if __name__ == "__main__":
    main()
