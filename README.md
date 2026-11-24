# m3u-epg-validator

通过 **txt 文本** 添加 M3U / EPG 地址，**每天自动测活**，只保留可正常访问的节目源，生成干净的 `result.m3u` / `result.txt`，并按 IPv4 / IPv6 分离。

输出结构专门方便被 [libretv-aggregate](https://github.com/xuy132/libretv-aggregate) 直接采用。

## 目录结构
m3u-epg-validator/

├── .github/workflows/update.yml   # 每天自动运行

├── config/

│   ├── sources.txt                # 直播源（M3U/TXT）地址

│   ├── epg.txt                    # EPG 地址

│   └── blacklist.txt              # 黑名单关键字

├── scripts/

│   └── main.py                    # 核心脚本

├── epg/                           # 结果目录（自动生成）

│   ├── result.m3u

│   ├── result.txt

│   ├── ipv4/

│   └── ipv6/

└── README.md

## 订阅地址（跑完 Actions 后可用）

| 类型 | 地址 |
|------|------|
| 全部 | `https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/result.m3u` |
| IPv4 | `https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv4/result.m3u` |
| IPv6 | `https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv6/result.m3u` |

## 给 libretv-aggregate 使用

在 [libretv-aggregate](https://github.com/xuy132/libretv-aggregate) 的 `config/live_sources.txt` 中添加：
聚合直播|[raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/result.m3u%7Chttps://epg.pw/xmltv/epg_CN.xml](https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/result.m3u%7Chttps://epg.pw/xmltv/epg_CN.xml)
聚合IPv4|[raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv4/result.m3u%7Chttps://epg.pw/xmltv/epg_CN.xml](https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv4/result.m3u%7Chttps://epg.pw/xmltv/epg_CN.xml)
聚合IPv6|[raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv6/result.m3u%7Chttps://epg.pw/xmltv/epg_CN.xml](https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/epg/ipv6/result.m3u%7Chttps://epg.pw/xmltv/epg_CN.xml)

## 免责声明

本项目仅做公开直播源的可用性检测与整理，不存储、不提供任何版权内容。请确保你添加的源符合当地法律法规。

## License

MIT
