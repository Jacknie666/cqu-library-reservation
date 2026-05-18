#!/usr/bin/env python3
"""
重庆大学图书馆座位预约系统 - 自动签到脚本
===========================================
逆向工程说明：
  前端 HMAC 签名算法（来自 app.js）:
    1. 生成随机 UUID (X-request-id)
    2. 取当前时间戳毫秒 (X-request-date)
    3. 拼接签名字符串: "seat::{uuid}::{timestamp}::POST"
    4. 对 systemInfo.hmacKey 用 AES-256-CBC 解密（key=server_date_time, iv=client_date_time）得到真实 hmac 密钥
    5. 用 HMAC-SHA256(签名字符串, 真实密钥) 生成 X-hmac-request-key

  AES 解密：
    - key = "server_date_time" (16字节, UTF-8)
    - iv  = "client_date_time" (16字节, UTF-8)
    - mode = AES-CBC, padding = PKCS7

  签到时间窗口：
    - 预约开始时间的前 30 分钟 到 后 30 分钟内可以签到

使用方式：
  1. 直接运行: python3 checkin.py            → 立即尝试签到
  2. 定时模式: python3 checkin.py --schedule → 等待到签到窗口开放后自动签到

依赖安装：
  pip install requests pycryptodome
"""

import os
import uuid
import time
import hmac
import hashlib
import base64
import json
import re
import sys
import argparse
from datetime import datetime, timedelta
import requests
import urllib3
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────────────────────────
# 配置区（根据实际情况填写）
# ─────────────────────────────────────────────────────────────────
CONFIG = {
    # ── 从浏览器 sessionStorage 提取的值 ──
    # 打开浏览器控制台输入:
    #   sessionStorageProxy.getItem('token')
    #   JSON.parse(sessionStorageProxy.getItem('systemInfo')).hmacKey
    "TOKEN":               "5f2ea8e8001544cc4f3b5699cd246257991f801f10121343",
    "HMAC_KEY_ENCRYPTED":  "+8xxVUW/YINTRP6kQa5luw==",
    "SESSION_COOKIE":      "15CE5C8B5C8AF66E59B57EDD61501C61",

    # ── SSO 自动登录（token 过期时启用）──
    "USE_SSO_LOGIN": False,
    "USERNAME":      os.environ.get("CQU_USERNAME", ""),
    "PASSWORD":      os.environ.get("CQU_PASSWORD", ""),

    # ── 签到设置 ──
    # 提前几分钟开始尝试签到（服务器允许开始时间前 30 分钟）
    "CHECKIN_ADVANCE_MINUTES": 1,   # 提前 1 分钟开始（保险起见不要太早）
    # 签到失败重试间隔（秒）
    "RETRY_INTERVAL_SECONDS": 30,
    # 最大重试次数
    "MAX_RETRIES": 5,

    # ── API 端点 ──
    "BASE_URL":    "https://libspace.cqu.edu.cn/jsq",
    "CHECKIN_URL": "https://libspace.cqu.edu.cn/jsq/static/frontApi/make/checkIn?qrMd5=PC",
    "BOOKING_URL": "https://libspace.cqu.edu.cn/jsq/static/frontApi/user/currentUseMake",

    # ── AES 解密常量（app.js 硬编码）──
    "AES_KEY": "server_date_time",
    "AES_IV":  "client_date_time",
}
# ─────────────────────────────────────────────────────────────────


def aes_decrypt(ciphertext_b64: str) -> str:
    """AES-CBC 解密，与前端 crypto-js 实现一致"""
    key_bytes = CONFIG["AES_KEY"].encode("utf-8")[:16].ljust(16, b"\x00")
    iv_bytes  = CONFIG["AES_IV"].encode("utf-8")[:16].ljust(16, b"\x00")
    ct = base64.b64decode(ciphertext_b64)
    cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
    plaintext = unpad(cipher.decrypt(ct), AES.block_size)
    return plaintext.decode("utf-8")


def build_hmac_headers(method: str = "POST") -> dict:
    """
    构建签名 Headers，完全还原前端签名逻辑：
      sign_str  = "seat::{uuid}::{timestamp_ms}::POST"
      hmac_key  = AES_decrypt(hmacKeyEncrypted)
      hmac_val  = HMAC-SHA256(sign_str, hmac_key).hexdigest()
    """
    request_id = str(uuid.uuid4())
    timestamp  = int(time.time() * 1000)
    real_key   = aes_decrypt(CONFIG["HMAC_KEY_ENCRYPTED"])
    sign_str   = f"seat::{request_id}::{timestamp}::{method.upper()}"
    hmac_val   = hmac.new(
        real_key.encode("utf-8"),
        sign_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return {
        "X-request-id":       request_id,
        "X-request-date":     str(timestamp),
        "X-hmac-request-key": hmac_val,
    }


def build_headers(extra: dict = None) -> dict:
    """组装完整的请求头"""
    hmac_hdrs = build_hmac_headers()
    headers = {
        "Accept":        "application/json, text/plain, */*",
        "Content-Type":  "application/json;charset=UTF-8",
        "loginType":     "PC",
        "logintype":     "PC",
        "Origin":        "https://libspace.cqu.edu.cn",
        "Referer":       "https://libspace.cqu.edu.cn/jsq-v/",
        "User-Agent":    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "token":         CONFIG["TOKEN"],
        "Cookie":        f"jsq_JSESSIONID={CONFIG['SESSION_COOKIE']}",
    }
    headers.update(hmac_hdrs)
    if extra:
        headers.update(extra)
    return headers


def api_post(url: str, payload: dict = None) -> dict:
    """通用 POST 请求"""
    resp = requests.post(
        url,
        headers=build_headers(),
        json=payload or {},
        verify=False,
        timeout=15
    )
    resp.raise_for_status()
    return resp.json()


def get_today_booking() -> dict | None:
    """
    查询今日预约信息
    返回当前预约状态，包含开始时间等信息
    """
    try:
        data = api_post(CONFIG["BOOKING_URL"])
        if data.get("status") and data.get("data"):
            return data["data"]
        return None
    except Exception as e:
        print(f"[!] 查询预约信息失败: {e}")
        return None


def parse_booking_start_time(booking: dict) -> datetime | None:
    """
    从预约数据中解析开始时间
    接口返回格式示例: {"beginTime": "08:00", "date": "2026-05-10", ...}
    """
    if not booking:
        return None
    try:
        date_str = booking.get("date") or datetime.now().strftime("%Y-%m-%d")
        time_str = (
            booking.get("beginTime") or
            booking.get("startTime") or
            booking.get("begin") or
            ""
        )
        if not time_str:
            # 尝试从 makeTime 字段解析
            make_time = booking.get("makeTime", "")
            time_match = re.search(r"(\d{2}:\d{2})", make_time)
            if time_match:
                time_str = time_match.group(1)

        if time_str:
            dt_str = f"{date_str} {time_str}"
            return datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
    except Exception as e:
        print(f"[!] 解析预约时间失败: {e} | 原始数据: {booking}")
    return None


def do_checkin() -> bool:
    """执行一次签到请求"""
    print(f"\n[*] {datetime.now().strftime('%H:%M:%S')} 尝试签到...")
    try:
        data = api_post(CONFIG["CHECKIN_URL"])
        msg  = data.get("message", "")

        if data.get("status") is True or data.get("code") == 200:
            print(f"[+] ✅ 签到成功！响应: {msg}")
            return True
        else:
            print(f"[-] ❌ 签到失败: {msg}")
            return False
    except Exception as e:
        print(f"[-] 请求异常: {e}")
        return False


def checkin_now() -> bool:
    """立即签到（含重试）"""
    for attempt in range(1, CONFIG["MAX_RETRIES"] + 1):
        print(f"\n[*] 第 {attempt}/{CONFIG['MAX_RETRIES']} 次尝试...")
        if do_checkin():
            return True
        if attempt < CONFIG["MAX_RETRIES"]:
            print(f"[*] {CONFIG['RETRY_INTERVAL_SECONDS']} 秒后重试...")
            time.sleep(CONFIG["RETRY_INTERVAL_SECONDS"])
    return False


def checkin_scheduled() -> bool:
    """
    定时模式：查询预约时间，等待到签到窗口开放后自动签到
    签到时间窗口 = 预约开始时间 - 30min  到  预约开始时间 + 30min
    """
    print("\n[*] 定时模式：查询今日预约信息...")
    booking = get_today_booking()

    if not booking:
        print("[!] 未找到今日有效预约，请先完成预约")
        print("[*] 尝试查询历史预约接口...")
        # fallback: 尝试查询 currentUseMake
        try:
            data = api_post(f"{CONFIG['BASE_URL']}/static/frontApi/user/lastMake")
            if data.get("status") and data.get("data"):
                booking = data["data"]
                print(f"[+] 获取到最近预约: {json.dumps(booking, ensure_ascii=False)[:200]}")
        except Exception as e:
            print(f"[!] 备用接口查询失败: {e}")

    if not booking:
        print("[-] 无法获取预约信息，退出定时模式")
        return False

    print(f"\n[+] 预约信息: {json.dumps(booking, ensure_ascii=False, indent=2)}")

    start_dt = parse_booking_start_time(booking)
    if not start_dt:
        print("[!] 无法解析预约开始时间，立即尝试签到...")
        return checkin_now()

    # 计算签到窗口：开始时间 - 30min（服务器规定）
    # 我们提前 CHECKIN_ADVANCE_MINUTES 分钟进入窗口后开始尝试
    window_open  = start_dt - timedelta(minutes=30)
    window_close = start_dt + timedelta(minutes=30)
    trigger_time = window_open + timedelta(minutes=CONFIG["CHECKIN_ADVANCE_MINUTES"])

    now = datetime.now()
    print(f"\n  预约开始时间  : {start_dt.strftime('%H:%M')}")
    print(f"  签到窗口开放  : {window_open.strftime('%H:%M')}")
    print(f"  签到窗口关闭  : {window_close.strftime('%H:%M')}")
    print(f"  计划触发签到  : {trigger_time.strftime('%H:%M')}")
    print(f"  当前时间      : {now.strftime('%H:%M:%S')}")

    if now > window_close:
        print("\n[-] 签到窗口已关闭（超过预约开始时间 30 分钟），无法签到")
        return False

    if now >= trigger_time:
        print("\n[*] 已在签到窗口内，立即尝试签到...")
        return checkin_now()

    wait_seconds = (trigger_time - now).total_seconds()
    print(f"\n[*] 等待 {int(wait_seconds//60)} 分 {int(wait_seconds%60)} 秒后开始签到...")

    # 倒计时等待
    while True:
        now = datetime.now()
        remaining = (trigger_time - now).total_seconds()
        if remaining <= 0:
            break
        if remaining > 60:
            # 每分钟显示一次进度
            print(f"[*] 距离签到还有 {int(remaining//60)} 分 {int(remaining%60)} 秒...", end="\r")
            time.sleep(30)
        else:
            print(f"[*] 距离签到还有 {int(remaining)} 秒...   ", end="\r")
            time.sleep(1)

    print("\n[*] ⏰ 签到时间到！")
    return checkin_now()


# ─────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="CQU 图书馆座位预约系统 - 自动签到脚本"
    )
    parser.add_argument(
        "--schedule", "-s",
        action="store_true",
        help="定时模式：自动查询预约时间并等待到签到窗口后执行签到"
    )
    parser.add_argument(
        "--token", "-t",
        help="覆盖配置文件中的 token"
    )
    args = parser.parse_args()

    if args.token:
        CONFIG["TOKEN"] = args.token

    print("=" * 60)
    print("  重庆大学图书馆座位预约系统 - 自动签到脚本")
    print("=" * 60)

    # 验证 HMAC 密钥解密
    try:
        real_key = aes_decrypt(CONFIG["HMAC_KEY_ENCRYPTED"])
        print(f"\n[+] HMAC 密钥解密成功: {repr(real_key)}")
    except Exception as e:
        print(f"\n[-] HMAC 密钥解密失败: {e}")
        print("    请检查 HMAC_KEY_ENCRYPTED 配置是否正确")
        sys.exit(1)

    print(f"[*] 使用 token: {CONFIG['TOKEN'][:20]}...")
    print(f"[*] 模式: {'定时模式' if args.schedule else '立即签到'}")

    if args.schedule:
        success = checkin_scheduled()
    else:
        success = checkin_now()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
