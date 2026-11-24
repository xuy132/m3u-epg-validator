#!/usr/bin/env python3
"""
m3u-epg-validator
- subscribe.txt 订阅源 → 测活 → 按名去重
- groups.txt 控制分组（MV/轮播保留并单独成组）
- 只过滤真正的说明项（更新时间、关于本源等），不删 MV/轮播
"""
from __future__ import annotations
import concurrent.futures, gzip, ipaddress, re, time, urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set, Tuple
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CONFIG, OUTPUT = ROOT / "config", ROOT / "output"
SUBSCRIBE_FILE = CONFIG / "subscribe.txt"
EPG_FILE = CONFIG / "epg.txt"
BLACKLIST_FILE = CONFIG / "blacklist.txt"
WHITELIST_FILE = CONFIG / "whitelist.txt"
GROUPS_FILE = CONFIG / "groups.txt"

CONCURRENCY, TIMEOUT, EPG_TIMEOUT = 30, 8, 30
MAX_CHANNELS_PER_SOURCE = 800
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
RAW_BASE = "https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output"

# 仅过滤说明/垃圾项，不碰 MV、轮播、电影
JUNK_NAME_PATTERNS = [
    r"^更新时间", r"^更新$", r"关于本源", r"^声明", r"^公告", r"^说明$",
    r"^[\d]{4}[-/年]\d{1,2}[-/月]\d{1,2}",  # 纯日期当频道名
]

# 分组展示顺序
GROUP_ORDER = [
    "📺央视频道",
    "📡卫视频道",
    "🇭🇰港澳台",
    "🔁轮播影院",
    "🎵MV点播",
    "⚽体育频道",
    "🧸少儿频道",
    "☘️浙江频道",
    "☘️江西频道",
    "☘️广东频道",
    "☘️福建频道",
    "☘️上海频道",
    "☘️北京频道",
    "☘️江苏频道",
    "☘️湖南频道",
    "☘️四川频道",
    "☘️湖北频道",
    "☘️河南频道",
    "☘️山东频道",
    "☘️河北频道",
    "☘️安徽频道",
    "☘️辽宁频道",
    "☘️吉林频道",
    "☘️黑龙江频道",
    "☘️陕西频道",
    "☘️山西频道",
    "☘️重庆频道",
    "☘️天津频道",
    "☘️云南频道",
    "☘️贵州频道",
    "☘️广西频道",
    "☘️海南频道",
    "☘️内蒙古频道",
    "☘️新疆频道",
    "☘️西藏频道",
    "☘️宁夏频道",
    "☘️青海频道",
    "☘️甘肃频道",
    "📍地方综合",
    "📂其他频道",
]

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
    return [ln.strip() for ln in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]

def load_subscribe() -> List[SourceEntry]:
    entries = []
    for line in load_lines(SUBSCRIBE_FILE):
        if "|" in line:
            name, url = [x.strip() for x in line.split("|", 1)]
        else:
            url = line.strip()
            name = urlparse(url).hostname or "未知源"
        if url.startswith(("http://", "https://")):
            entries.append(SourceEntry(name=name, url=url))
    return entries

def load_epg_urls() -> List[str]:
    return [u for u in load_lines(EPG_FILE) if u.startswith(("http://", "https://"))]

def load_keywords(path: Path) -> Set[str]:
    return {kw.lower() for kw in load_lines(path)}

def load_group_rules() -> List[Tuple[str, str]]:
    """groups.txt → [(关键词, 分组名), ...] 从上到下优先"""
    rules = []
    for line in load_lines(GROUPS_FILE):
        if "|" not in line:
            continue
        key, group = [x.strip() for x in line.split("|", 1)]
        if key and group:
            rules.append((key, group))
    return rules

def match_keywords(text: str, keywords: Set[str]) -> bool:
    t = text.lower()
    return any(kw in t for kw in keywords)

def is_junk_name(name: str) -> bool:
    for p in JUNK_NAME_PATTERNS:
        if re.search(p, name, re.I):
            return True
    return False

def detect_ipv6_from_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address)
    except ValueError:
        return False

def classify_group(name: str, rules: List[Tuple[str, str]]) -> str:
    for key, group in rules:
        if key.lower() in name.lower():
            return group
    return "📂其他频道"

def normalize_name(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r"[\s\-_·•]+", "", n)
    n = re.sub(r"[\(（].*?[\)）]", "", n)
    n = re.sub(r"(hd|sd|4k|8k|fhd|uhd|1080p|720p|576i|1080i)$", "", n, flags=re.I)
    repl = {
        "中央一套": "cctv1", "中央一台": "cctv1", "央视一套": "cctv1",
        "中央二套": "cctv2", "中央三套": "cctv3", "中央四套": "cctv4",
        "中央五套": "cctv5", "中央五加": "cctv5+", "cctv5+": "cctv5+",
        "中央六套": "cctv6", "中央七套": "cctv7", "中央八套": "cctv8",
        "中央九套": "cctv9", "中央十套": "cctv10", "中央十一套": "cctv11",
        "中央十二套": "cctv12", "中央十三套": "cctv13", "中央十四套": "cctv14",
        "中央十五套": "cctv15", "中央十六套": "cctv16", "中央十七套": "cctv17",
    }
    for k, v in repl.items():
        if k in n:
            n = v
            break
    n = n.replace("cctv-1", "cctv1").replace("cctv－1", "cctv1")
    return n

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

def download_epg_files(epg_urls: List[str]) -> List[str]:
    epg_dir = OUTPUT / "epg"
    epg_dir.mkdir(parents=True, exist_ok=True)
    usable = []
    for i, url in enumerate(epg_urls):
        print(f"下载 EPG [{i+1}/{len(epg_urls)}]: {url}")
        data = fetch_bytes(url, timeout=EPG_TIMEOUT)
        if not data:
            usable.append(url)
            continue
        name = Path(urlparse(url).path).name or f"epg_{i}.xml"
        if not name.endswith((".xml", ".gz", ".xml.gz")):
            name = f"epg_{i}.xml"
        (epg_dir / name).write_bytes(data)
        print(f"  已保存: {name} ({len(data)/1024:.1f} KB)")
        usable.append(f"{RAW_BASE}/epg/{name}")
    (epg_dir / "epg_urls.txt").write_text("\n".join(usable) + "\n", encoding="utf-8")
    return usable

def parse_m3u(content: str, source_name: str, blacklist: Set[str], whitelist: Set[str], rules: List[Tuple[str, str]]) -> List[Channel]:
    channels, lines, i = [], content.splitlines(), 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            attrs = {m.group(1).lower(): m.group(2) for m in re.finditer(r'([\w-]+)="([^"]*)"', line)}
            name = line.split(",", 1)[-1].strip() if "," in line else attrs.get("tvg-name", "未知")
            tvg_id = attrs.get("tvg-id", "")
            tvg_name = attrs.get("tvg-name", name)
            tvg_logo = attrs.get("tvg-logo", "")
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("#")):
                i += 1
            if i >= len(lines):
                break
            url = lines[i].strip()
            if not url.startswith(("http://", "https://")):
                i += 1
                continue
            if is_junk_name(name) or match_keywords(name, blacklist) or match_keywords(url, blacklist):
                i += 1
                continue
            if whitelist and not (match_keywords(name, whitelist) or match_keywords(url, whitelist)):
                i += 1
                continue
            channels.append(Channel(
                name=name, url=url, group=classify_group(name, rules),
                tvg_id=tvg_id, tvg_name=tvg_name, tvg_logo=tvg_logo,
                source_name=source_name, is_ipv6=detect_ipv6_from_url(url),
            ))
        i += 1
    return channels

def parse_txt(content: str, source_name: str, blacklist: Set[str], whitelist: Set[str], rules: List[Tuple[str, str]]) -> List[Channel]:
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
        if is_junk_name(name) or match_keywords(name, blacklist) or match_keywords(url, blacklist):
            continue
        if whitelist and not (match_keywords(name, whitelist) or match_keywords(url, whitelist)):
            continue
        channels.append(Channel(
            name=name, url=url, group=classify_group(name, rules),
            source_name=source_name, is_ipv6=detect_ipv6_from_url(url),
        ))
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
                    ch.is_ipv6 = isinstance(ipaddress.ip_address(peer[0]), ipaddress.IPv6Address)
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
    print(f"订阅源: {len(sources)} | EPG: {len(epg_urls)} | 分组规则: {len(rules)}")

    saved_epg = download_epg_files(epg_urls) if epg_urls else []
    if not sources:
        print("警告: subscribe.txt 为空")
        return

    all_cands: List[Channel] = []
    for src in sources:
        print(f"拉取: {src.name} -> {src.url}")
        text = fetch_text(src.url)
        if not text:
            print("  失败")
            continue
        if "#EXTM3U" in text or "#EXTINF" in text:
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
            if done % 50 == 0 or done == len(all_cands):
                print(f"  测活: {done}/{len(all_cands)}")
            r = fut.result()
            if r:
                alive.append(r)
    print(f"测活通过: {len(alive)}")

    seen_url: Set[str] = set()
    seen_name: Set[str] = set()
    unique: List[Channel] = []
    for ch in sorted(alive, key=lambda c: c.latency_ms):
        if ch.url in seen_url:
            continue
        key = normalize_name(ch.name)
        if key in seen_name:
            continue
        seen_url.add(ch.url)
        seen_name.add(key)
        unique.append(ch)

    rank = {g: i for i, g in enumerate(GROUP_ORDER)}
    unique.sort(key=lambda c: (rank.get(c.group, 99), c.name))

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
        "\n".join([
            "# 复制到 libretv-aggregate 的 config/live_sources.txt",
            f"聚合全部|{RAW_BASE}/result.m3u|{epg0}",
            f"聚合IPv4|{RAW_BASE}/ipv4/result.m3u|{epg0}",
            f"聚合IPv6|{RAW_BASE}/ipv6/result.m3u|{epg0}",
        ]) + "\n",
        encoding="utf-8",
    )
    print("=== 完成 ===")

if __name__ == "__main__":
    main()
