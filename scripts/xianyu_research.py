#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""闲鱼关键词热销调研抓取器（XianyuPub 项目专用）。

直连闲鱼 mtop 搜索接口（免浏览器、绕过滑块），抓取某关键词的搜索结果，
按「人想要」数（闲鱼公开页面最接近销量的热度指标）排序，输出榜单报告。

⚠️ 必须在 xianyu-assistant-api-1 容器内运行（依赖项目 cookie 解密 + DB）：
    docker exec -i xianyu-assistant-api-1 sh -c 'cat > /tmp/xianyu_research.py' < scripts/xianyu_research.py
    docker exec -e PYTHONPATH=/app xianyu-assistant-api-1 python /tmp/xianyu_research.py "AI" 20 --top 50

用法:
    python xianyu_research.py <关键词> [页数] [--top N] [--out DIR]
    - 关键词:  必填，闲鱼搜索词
    - 页数:    可选，默认 20（每页 30 条 ≈ 600 条；抓太快会触发滑块风控）
    - --top N: 榜单条数，默认 50
    - --out:   输出目录（容器内路径），默认 /tmp/xr_<关键词>

输出（容器内 --out 目录）:
    items.ndjson      全部去重商品（item_id/title/price/want/seller/area/publishTime/image）
    all_sorted.json   按 want 降序的全量
    top<N>.md         榜单 Markdown（含商品链接）
    top<N>.csv        榜单 CSV
榜单同时打印到 stdout，方便直接重定向。

产物拷回宿主机:
    docker cp xianyu-assistant-api-1:/tmp/xr_<关键词>/ ./xianyu-research-<关键词>/
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time

try:
    import pymysql
    import requests
except ImportError:
    sys.stderr.write("缺少依赖 pymysql/requests：请在容器内运行（docker exec ... python）\n")
    sys.exit(2)

H5_API_BASE = "https://h5api.m.goofish.com/h5"
APP_KEY = "34839810"
JSV = "2.7.2"
ACCOUNT_ID = 1  # XianyuPub 默认账号
SEARCH_API = "mtop.taobao.idlemtopsearch.pc.search"
VERSION = "1.0"
WANT_RE = re.compile(r"(\d+)\s*人想要")


def decrypt(enc):
    try:
        from app.core.cookie_crypto import decrypt_cookie_if_needed
        return decrypt_cookie_if_needed(enc or "")
    except Exception:
        return enc or ""


def load_account_auth():
    """从 XianyuPub MySQL 读账号 cookie（解密）。"""
    try:
        password = open("/run/secrets/mysql_password").read().strip()
        host = "mysql"
    except Exception:
        # 宿主机直跑兜底：环境变量
        password = os.environ.get("XIANYU_DB_PASSWORD", "")
        host = os.environ.get("XIANYU_DB_HOST", "localhost")
    conn = pymysql.connect(
        host=host, port=int(os.environ.get("XIANYU_DB_PORT", 3306)),
        user=os.environ.get("XIANYU_DB_USER", "xianyu_app"),
        password=password, database=os.environ.get("XIANYU_DB_NAME", "xianyu_opensource"),
        charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT auth.encrypted_cookie, auth.encrypted_token, auth.cookie_status "
                "FROM xianyu_account_auth auth WHERE auth.account_id=%s AND auth.deleted=0 LIMIT 1",
                (ACCOUNT_ID,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        sys.exit("账号认证信息不存在（account_id=%d），请先在 XianyuPub 扫码登录" % ACCOUNT_ID)
    if row.get("cookie_status") not in (1, None):
        sys.exit("cookie_status=%s：账号 cookie 非正常，请重新扫码登录" % row.get("cookie_status"))
    cookie_str = decrypt(row.get("encrypted_cookie") or "")
    if not cookie_str:
        sys.exit("Cookie 为空，请重新扫码登录")
    m = re.search(r"_m_h5_tk=([^;]+)", cookie_str)
    token_raw = m.group(1) if m else decrypt(row.get("encrypted_token") or "")
    token = (token_raw or "").split("_")[0]
    if not token:
        sys.exit("无法从 cookie 提取 _m_h5_tk 签名 token")
    return cookie_str, token


def call_search(cookie_str, token, data_map, timeout=25):
    """调用闲鱼 mtop 搜索接口（带 H5 签名）。"""
    data_str = json.dumps(data_map, separators=(",", ":"))
    t_ms = int(time.time() * 1000)
    sign = hashlib.md5(f"{token}&{t_ms}&{APP_KEY}&{data_str}".encode()).hexdigest()
    url = f"{H5_API_BASE}/{SEARCH_API}/{VERSION}/"
    params = {
        "jsv": JSV, "appKey": APP_KEY, "t": str(t_ms), "sign": sign, "v": VERSION,
        "type": "originaljson", "dataType": "json", "timeout": "20000",
        "api": SEARCH_API, "sessionOption": "AutoLoginOnly"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9", "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json", "Cookie": cookie_str,
        "Referer": "https://www.goofish.com/", "Origin": "https://www.goofish.com"}
    resp = requests.post(url, params=params, data={"data": data_str}, headers=headers, timeout=timeout)
    return resp.json()


def parse_item(x):
    """从搜索结果卡片提取商品字段；want = 标签里的最大「x人想要」数。"""
    it = x.get("data", {}).get("item", {}).get("main", {})
    args = it.get("clickParam", {}).get("args", {})
    ex = it.get("exContent", {})
    dp = ex.get("detailParams", {})
    s = json.dumps(it, ensure_ascii=False)
    want = max([int(v) for v in WANT_RE.findall(s)], default=0)
    img = ""
    for u in re.findall(r'"url"\s*:\s*"(https?://[^"]+\.(?:jpg|jpeg|png|webp))[^"]*"', s):
        if "alicdn" in u:
            img = u.replace("\\u002F", "/").replace("\\/", "/")
            break
    return {
        "item_id": args.get("item_id"),
        "title": (dp.get("title") or "").replace("\n", " ").strip(),
        "price": args.get("price") or dp.get("soldPrice"),
        "want": want,
        "seller": dp.get("userNick", ""),
        "area": ex.get("area", ""),
        "publishTime": args.get("publishTime"),
        "image": img,
    }


def fetch(keyword, pages):
    """抓取多页，返回 {item_id: item} 去重字典。"""
    cookie_str, token = load_account_auth()
    items = {}
    rn = ""
    for page in range(1, pages + 1):
        ok = False
        for attempt in range(4):
            try:
                dm = {"pageNumber": page, "keyword": keyword, "fromFilter": False,
                      "rowsPerPage": 30, "sortValue": "", "sortField": "",
                      "customDistance": "", "gps": "", "propValueStr": {},
                      "customGps": "", "searchReqFromPage": "pcSearch",
                      "extraFilterValue": "{}", "userPositionJson": "{}"}
                if rn:
                    dm["rn"] = rn
                r = call_search(cookie_str, token, dm)
                ret = " ".join(r.get("ret", []))
                if ret.startswith("SUCCESS"):
                    for x in r.get("data", {}).get("resultList", []):
                        it = parse_item(x)
                        if it["item_id"] and it["item_id"] not in items:
                            items[it["item_id"]] = it
                    sc = r.get("data", {}).get("resultInfo", {}).get("searchResControlFields", {})
                    rn = sc.get("rn", rn)
                    ok = True
                    break
                if "RGV587" in ret or "USER_VALIDATE" in ret:
                    # 滑块风控：退避重试
                    time.sleep(30 + attempt * 30)
                    continue
                time.sleep(2 + attempt * 3)
            except Exception as e:
                sys.stderr.write(f"[err] page {page} attempt {attempt}: {e}\n")
                time.sleep(3 + attempt * 4)
        if not ok:
            sys.stderr.write(f"[warn] page {page} failed（风控或接口异常，可稍后重跑，会跳过已抓 item_id）\n")
        if page % 5 == 0:
            sys.stderr.write(f"[info] {page}/{pages} 页，已去重 {len(items)} 条\n")
        time.sleep(1.2 + random.random() * 0.8)  # 慢速防风控
    return items


def classify_risk(title):
    """按标题给商品打合规风险标（供选品筛选）。

    🟢 低风险：教程/资料/素材/提示词/字体/硬件等「卖内容」定位
    🟠 中风险：代做/定制/接单/咨询/收徒等「卖服务」定位（可做但需包装成教程，随时可能被风控）
    🔴 高危避雷：会员/账号/代充/激活破解/外挂/自动化批量/引流推广等「卖工具」定位
    """
    # 注意：闲鱼搜索返回的 title 字段 = 标题+描述全文拼接。
    # 只取前 80 字符（商品核心标题）做风险判定，避免描述里的正常词误伤
    # （如"门店推广"、"分镜脚本"、"全流程自动化"、"无会员费"）。
    t = (title or "").lower()[:80]
    HIGH = [
        "会员", "账号", "代充", "订阅", "年卡", "直充", "出租", "激活", "破解",
        "远程安装", "弹窗", "外挂", "记牌器", "辅助", "脚本", "自动化", "批量",
        "养机", "分发", "引流", "流量", "收录", "排名", "代挂", "挂机",
        "水军", "权限", "非正版", "解锁",
    ]
    MID = [
        "代做", "代生成", "代剪", "代写", "代画", "代定制", "代部署", "代制作",
        "代剪辑", "接单", "定制", "咨询", "收徒", "陪跑", "学徒", "代工",
        "软件开发", "本地部署", "服务", "无限积分", "测试卡", "积分",
    ]
    for kw in HIGH:
        if kw in t:
            return "🔴"
    for kw in MID:
        if kw in t:
            return "🟠"
    return "🟢"


def risk_note(risk):
    return {
        "🟢": "低风险：教程/资料/素材，可参考",
        "🟠": "中风险：卖服务，需包装成教程定位",
        "🔴": "高危避雷：会员/账号/外挂/自动化/工具",
    }[risk]


def fmt_report(items, top_n, keyword):
    top = items[:top_n]
    lines = [
        f"# 闲鱼『{keyword}』热销 TOP {top_n} 调研报告",
        "",
        f"- 抓取时间：{time.strftime('%Y-%m-%d %H:%M')}",
        f"- 数据源：闲鱼 PC 搜索接口（登录态，综合排序，共 {len(items)} 条去重商品）",
        "- 排序指标：**「人想要」数**（闲鱼公开页面最接近销量的热度指标，反映收藏+询价意向）",
        "- 注意：闲鱼不公开已售数量，该指标与成交高度正相关",
        "",
        f"## TOP {top_n} 榜单",
        "",
        "| # | 人想要 | 价格(¥) | 标题(节选) | 风险 | 卖家 | 地区 | 链接 |",
        "|---|-------|--------|-----------|------|------|------|------|",
    ]
    for i, x in enumerate(top, 1):
        t = x["title"][:50].replace("|", "/")
        link = f"https://www.goofish.com/item?id={x['item_id']}"
        risk = classify_risk(x["title"])
        lines.append(f"| {i} | {x['want']} | {x['price']} | {t} | {risk} | {x['seller'][:14]} | {x['area']} | [链接]({link}) |")
    lines += [
        "",
        "> **风险图例**：🟢 低风险（教程/资料/素材，可参考）｜🟠 中风险（卖服务，需包装成教程定位）"
        "｜🔴 高危避雷（会员/账号/外挂/自动化/工具，建议跳过）。",
        "> 避雷规则详见 xianyu-research skill；选品优先 🟢，🟠 需重写文案，🔴 直接跳过。",
        "",
    ]
    return "\n".join(lines)


def load_from_ndjson(ndjson_path):
    """浏览器方案产物（items.ndjson）→ 去重 dict。"""
    items = {}
    for line in open(ndjson_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            x = json.loads(line)
            if x.get("item_id") and x["item_id"] not in items:
                items[x["item_id"]] = x
        except Exception:
            continue
    return items


def main():
    ap = argparse.ArgumentParser(description="闲鱼关键词热销调研抓取器")
    ap.add_argument("keyword", help="闲鱼搜索关键词")
    ap.add_argument("pages", nargs="?", type=int, default=20, help="抓取页数（每页30条），默认20")
    ap.add_argument("--top", type=int, default=50, help="榜单条数，默认50")
    ap.add_argument("--out", default=None, help="输出目录（容器内路径），默认 /tmp/xr_<关键词>")
    ap.add_argument("--from-ndjson", default=None,
                    help="离线模式：读浏览器方案抓的 items.ndjson 生成榜单（跳过网络抓取）")
    args = ap.parse_args()

    out_dir = args.out or f"/tmp/xr_{args.keyword}"
    os.makedirs(out_dir, exist_ok=True)

    if args.from_ndjson:
        items = load_from_ndjson(args.from_ndjson)
        sys.stderr.write(f"[offline] 读取 {args.from_ndjson} → {len(items)} 条去重\n")
        if not items:
            sys.exit("NDJSON 里没有有效商品")
        lst = sorted(items.values(), key=lambda x: -(x.get("want") or 0))
        with open(os.path.join(out_dir, "all_sorted.json"), "w") as f:
            json.dump(lst, f, ensure_ascii=False, indent=1)
        md = fmt_report(lst, args.top, args.keyword)
        md_path = os.path.join(out_dir, f"top{args.top}.md")
        open(md_path, "w").write(md)
        with open(os.path.join(out_dir, f"top{args.top}.csv"), "w") as f:
            f.write("rank,want,price,title,risk,seller,area,publish_date,url\n")
            for i, x in enumerate(lst[:args.top], 1):
                ts = x.get("publishTime")
                pub = time.strftime("%Y-%m-%d", time.localtime(int(ts) / 1000)) if ts else ""
                f.write(f'{i},{x["want"]},{x["price"]},"{x["title"]}",{classify_risk(x["title"])},{x["seller"]},{x["area"]},{pub},'
                        f'https://www.goofish.com/item?id={x["item_id"]}\n')
        sys.stderr.write(f"[done] 榜单已存 {md_path}\n")
        print(md)
        return

    sys.stderr.write(f"[start] 关键词={args.keyword} 页数={args.pages} → {out_dir}\n")
    items = fetch(args.keyword, args.pages)
    if not items:
        sys.exit("未抓到任何商品（可能被风控，稍后重试或换浏览器方案）")

    lst = sorted(items.values(), key=lambda x: -(x.get("want") or 0))

    # 全量落盘
    with open(os.path.join(out_dir, "items.ndjson"), "w") as f:
        for it in items.values():
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "all_sorted.json"), "w") as f:
        json.dump(lst, f, ensure_ascii=False, indent=1)

    # 榜单 md + csv
    md = fmt_report(lst, args.top, args.keyword)
    md_path = os.path.join(out_dir, f"top{args.top}.md")
    open(md_path, "w").write(md)
    with open(os.path.join(out_dir, f"top{args.top}.csv"), "w") as f:
        f.write("rank,want,price,title,risk,seller,area,publish_date,url\n")
        for i, x in enumerate(lst[:args.top], 1):
            ts = x.get("publishTime")
            pub = time.strftime("%Y-%m-%d", time.localtime(int(ts) / 1000)) if ts else ""
            f.write(f'{i},{x["want"]},{x["price"]},"{x["title"]}",{classify_risk(x["title"])},{x["seller"]},{x["area"]},{pub},'
                    f'https://www.goofish.com/item?id={x["item_id"]}\n')

    sys.stderr.write(f"[done] 共 {len(lst)} 条去重商品，榜单已存 {md_path}\n")
    print(md)  # stdout：榜单直接可看/重定向


if __name__ == "__main__":
    main()
