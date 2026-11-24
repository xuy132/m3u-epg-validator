#!/usr/bin/env python3
"""
m3u-epg-validator
通过 txt 添加 M3U/EPG 源，并发测活，仅保留可访问频道，
输出 epg/result.m3u、result.txt 以及 ipv4/ipv6 子目录。
可直接被 https://github.com/xuy132/libretv-aggregate 采用。
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Set
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
OUTPUT = ROOT / "epg"
SOURCES_FILE = CONFIG / "sources.txt"
EPG_FILE = CONFIG / "epg.txt"
BLACKLIST_FILE = CONFIG / "blacklist.txt"

CONCURRENCY = 30
TIMEOUT = 8
MAX_CHANNELS_PER_SOURCE = 600
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


@dataclass
class Channel:
    name: str
    url: str
    group: str = "未分类"
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
    lines = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def load_sources() -> List[SourceEntry]:
    entries = []
    for line in load_lines(SOURCES_FILE):
        if "|" in line:
            name, url = line.split("|", 1)
            name, url = name.strip(), url.strip()
        else:
            url = line.strip()
            name = urlparse(url).hostname or "未知源"
        if url:
            entries.append(SourceEntry(name=name, url=url))
    return entries


def load_epg_urls() -> List[str]:
    return load_lines(EPG_FILE)


def load_blacklist() -> Set[str]:
    return {kw.lower() for kw in load_lines(BLACKLIST_FILE)}


def is_blacklisted(text: str, blacklist: Set[str]) -> bool:
    t = text.lower()
    return any(kw in t for kw in blacklist)


def detect_ipv6_from_url(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        ip = ipaddress.ip_address(host)
        return isinstance(ip, ipaddress.IPv6Address)
    except ValueError:
        return False


def fetch_text(url: str) -> Optional[str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT + 5) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    except Exception:
        return None


def parse_m3u(content: str, source_name: str, blacklist: Set[str]) -> List[Channel]:
    channels: List[Channel] = []
    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#EXTINF"):
            attrs = {}
            for m in re.finditer(r'([\w-]+)="([^"]*)"', line):
                attrs[m.group(1).lower()] = m.group(2)
            name = line.split(",", 1)[-1].strip() if "," in line else attrs.get("tvg-name", "未知")
            group = attrs.get("group-title", "未分类")
            tvg_id = attrs.get("tvg-id", "")
            tvg_name = attrs.get("tvg-name", name)
            tvg_logo = attrs.get("tvg-logo", "")

            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("#")):
                i += 1
            if i >= len(lines):
                break
            url = lines[i].strip()
            if not url.startswith(("http://", "https://", "rtmp://", "rtp://")):
                i += 1
                continue
            if is_blacklisted(name, blacklist) or is_blacklisted(url, blacklist):
                i += 1
                continue
            channels.append(
                Channel(
                    name=name,
                    url=url,
                    group=group,
                    tvg_id=tvg_id,
                    tvg_name=tvg_name,
                    tvg_logo=tvg_logo,
                    source_name=source_name,
                    is_ipv6=detect_ipv6_from_url(url),
                )
            )
        i += 1
    return channels


def parse_txt(content: str, source_name: str, blacklist: Set[str]) -> List[Channel]:
    channels: List[Channel] = []
    current_group = "未分类"
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("#genre#"):
            current_group = line.replace("#genre#", "").strip() or "未分类"
            continue
        if "," in line:
            name, url = line.split(",", 1)
            name, url = name.strip(), url.strip()
        else:
            continue
        if not url.startswith(("http://", "https://")):
            continue
        if is_blacklisted(name, blacklist) or is_blacklisted(url, blacklist):
            continue
        channels.append(
            Channel(
                name=name,
                url=url,
                group=current_group,
                source_name=source_name,
                is_ipv6=detect_ipv6_from_url(url),
            )
        )
    return channels


def check_stream(ch: Channel) -> Optional[Channel]:
    start = time.perf_counter()
    req = urllib.request.Request(
        ch.url,
        headers={"User-Agent": USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            resp.read(256)
            ch.latency_ms = (time.perf_counter() - start) * 1000
            try:
                sock = resp.fp.raw._sock  # type: ignore
                peer = sock.getpeername()
                if peer and len(peer) >= 1:
                    ip = ipaddress.ip_address(peer[0])
                    ch.is_ipv6 = isinstance(ip, ipaddress.IPv6Address)
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
        attr_str = " ".join(attrs)
        lines.append(f"#EXTINF:-1 {attr_str},{ch.name}")
        lines.append(ch.url)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_txt(channels: List[Channel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    groups: dict[str, List[Channel]] = {}
    for ch in channels:
        groups.setdefault(ch.group, []).append(ch)
    lines = []
    for group, chs in groups.items():
        lines.append(f"{group},#genre#")
        for ch in chs:
            lines.append(f"{ch.name},{ch.url}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    print("=== m3u-epg-validator 开始 ===")
    sources = load_sources()
    epg_urls = load_epg_urls()
    blacklist = load_blacklist()
    print(f"加载源: {len(sources)} 个 | EPG: {len(epg_urls)} 个 | 黑名单关键字: {len(blacklist)}")

    if not sources:
        print("警告: config/sources.txt 为空")
        for p in [
            OUTPUT / "result.m3u",
            OUTPUT / "result.txt",
            OUTPUT / "ipv4" / "result.m3u",
            OUTPUT / "ipv4" / "result.txt",
            OUTPUT / "ipv6" / "result.m3u",
            OUTPUT / "ipv6" / "result.txt",
        ]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("#EXTM3U\n" if p.suffix == ".m3u" else "", encoding="utf-8")
        return

    all_candidates: List[Channel] = []

    for src in sources:
        print(f"拉取: {src.name} -> {src.url}")
        text = fetch_text(src.url)
        if not text:
            print(f"  失败: 无法获取 {src.url}")
            continue
        if "#EXTM3U" in text or "#EXTINF" in text:
            chs = parse_m3u(text, src.name, blacklist)
        else:
            chs = parse_txt(text, src.name, blacklist)
        print(f"  解析得到 {len(chs)} 个频道")
        all_candidates.extend(chs[:MAX_CHANNELS_PER_SOURCE])

    print(f"候选频道总数: {len(all_candidates)}")

    alive: List[Channel] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as executor:
        futures = {executor.submit(check_stream, ch): ch for ch in all_candidates}
        done = 0
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            if done % 50 == 0 or done == len(all_candidates):
                print(f"  测活进度: {done}/{len(all_candidates)}")
            result = fut.result()
            if result is not None:
                alive.append(result)

    print(f"测活通过: {len(alive)} / {len(all_candidates)}")

    seen: Set[str] = set()
    unique: List[Channel] = []
    for ch in sorted(alive, key=lambda c: c.latency_ms):
        if ch.url in seen:
            continue
        seen.add(ch.url)
        unique.append(ch)

    ipv4 = [c for c in unique if not c.is_ipv6]
    ipv6 = [c for c in unique if c.is_ipv6]

    print(f"去重后: 总计 {len(unique)} | IPv4 {len(ipv4)} | IPv6 {len(ipv6)}")

    write_m3u(unique, OUTPUT / "result.m3u", epg_urls)
    write_txt(unique, OUTPUT / "result.txt")
    write_m3u(ipv4, OUTPUT / "ipv4" / "result.m3u", epg_urls)
    write_txt(ipv4, OUTPUT / "ipv4" / "result.txt")
    write_m3u(ipv6, OUTPUT / "ipv6" / "result.m3u", epg_urls)
    write_txt(ipv6, OUTPUT / "ipv6" / "result.txt")

    live_desc = OUTPUT / "live_sources_for_libretv.txt"
    epg0 = epg_urls[0] if epg_urls else ""
    lines = [
        "# 可直接复制到 libretv-aggregate 的 config/live_sources.txt",
        f"聚合直播(全部)|https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/result.m3u|{epg0}",
        f"聚合直播(IPv4)|https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv4/result.m3u|{epg0}",
        f"聚合直播(IPv6)|https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv6/result.m3u|{epg0}",
    ]
    live_desc.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("=== 完成 ===")
    print(f"结果目录: {OUTPUT}")


if __name__ == "__main__":
    main()
