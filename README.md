# m3u-epg-validator

通过 **txt 文本** 添加 M3U / EPG 地址，**每天自动测活**，只保留可正常访问的节目源，生成干净的 `result.m3u` / `result.txt`，并按 IPv4 / IPv6 分离。

输出结构专门方便被 [libretv-aggregate](https://github.com/xuy132/libretv-aggregate) 直接采用。

## 目录结构
m3u-epg-validator/
config/

subscribe.txt   # 直播源

epg.txt         # EPG（会下载到 output/epg/）

blacklist.txt

whitelist.txt

scripts/main.py

output/

result.m3u / result.txt

ipv4/result.m3u / result.txt

ipv6/result.m3u / result.txt

epg/            # 下载的 EPG 文件
 README.md


## 订阅地址（Actions 跑完后）

| 类型 | 地址 |
|------|------|
| 全部 m3u | https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/result.m3u |
| 全部 txt | https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/result.txt |
| IPv4 | https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/ipv4/result.m3u |
| IPv6 | https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/ipv6/result.m3u |



## 给 libretv-aggregate 使用

聚合全部|[raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/result.m3u%7Chttp://epg.51zmt.top:8000/e.xml.gz](https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/result.m3u%7Chttp://epg.51zmt.top:8000/e.xml.gz)
聚合IPv4|[raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/ipv4/result.m3u%7Chttp://epg.51zmt.top:8000/e.xml.gz](https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/ipv4/result.m3u%7Chttp://epg.51zmt.top:8000/e.xml.gz)
聚合IPv6|[raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/ipv6/result.m3u%7Chttp://epg.51zmt.top:8000/e.xml.gz](https://raw.githubusercontent.com/xuy132/m3u-epg-validator/main/output/ipv6/result.m3u%7Chttp://epg.51zmt.top:8000/e.xml.gz)

## 免责声明

本项目仅做公开直播源的可用性检测与整理，不存储、不提供任何版权内容。请确保你添加的源符合当地法律法规。

## License

MIT
