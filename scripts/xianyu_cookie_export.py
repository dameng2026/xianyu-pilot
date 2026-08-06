#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出 XianyuPub 账号的闲鱼明文 cookie（供浏览器方案注入）。

用法（容器内，输出重定向到宿主机文件）:
    docker exec -e PYTHONPATH=/app xianyu-assistant-api-1 \
        python /tmp/xianyu_cookie_export.py > /tmp/xianyu_cookie.txt
    # 然后浏览器脚本读取该文件注入 cookie。
"""
import re
import sys

try:
    import pymysql
except ImportError:
    sys.stderr.write("缺少 pymysql：请在容器内运行\n")
    sys.exit(2)


def decrypt(enc):
    try:
        from app.core.cookie_crypto import decrypt_cookie_if_needed
        return decrypt_cookie_if_needed(enc or "")
    except Exception:
        return enc or ""


def main():
    account_id = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    try:
        password = open("/run/secrets/mysql_password").read().strip()
        host = "mysql"
    except Exception:
        password = ""
        host = "localhost"
    conn = pymysql.connect(
        host=host, port=3306, user="xianyu_app", password=password,
        database="xianyu_opensource", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT auth.encrypted_cookie FROM xianyu_account_auth auth "
                "WHERE auth.account_id=%s AND auth.deleted=0 LIMIT 1", (account_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        sys.exit("账号认证信息不存在（account_id=%d）" % account_id)
    cookie = decrypt(row.get("encrypted_cookie") or "")
    if not cookie:
        sys.exit("Cookie 为空，请先扫码登录")
    sys.stdout.write(cookie)


if __name__ == "__main__":
    main()
