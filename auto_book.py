import base64
import requests
import urllib3
import re
import urllib.parse
import uuid
import time
import hmac
import hashlib
import ssl
from datetime import datetime
from requests.adapters import HTTPAdapter
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

# 屏蔽自签证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 1. 配置区 =================
import os
USERNAME = os.environ.get("CQU_USERNAME", "")
PASSWORD = os.environ.get("CQU_PASSWORD", "")

# 默认预约日期：今天。如果需要预约明天，可以写 (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
TARGET_DATE = datetime.now().strftime("%Y-%m-%d")
TARGET_START_TIME = "NOW"   # 预约开始时间，例如 "08:00" 或 "10:45"。如果想立刻预约，可以写 "NOW"
TARGET_END_TIME = "16:00"     # 预约结束时间，例如 "22:00"

TARGET_BUILDING = "建筑馆"
TARGET_ROOM = "北楼一楼阅览室"
TARGET_SEAT = "030"
# TARGET_SEAT_ID 现在会自动从 library_seat_map.csv 中查找，无需手动填写

TIMEOUT = 30
REAL_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36'
BASE_API_URL = "https://libspace.cqu.edu.cn/jsq/static/frontApi"

import csv
import os

# ================= 2. 工具函数 & 适配器 =================
def get_seat_id_from_csv(building, room, label):
    """从本地 CSV 映射表中查找座位 ID"""
    csv_path = os.path.join(os.path.dirname(__file__), "library_seat_map.csv")
    if not os.path.exists(csv_path):
        return None
    
    clean_label = str(label).lstrip('0')
    possible_labels = {str(label), clean_label, clean_label.zfill(3)}
    
    try:
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if (row['building'] == building and 
                    row['room'] == room and 
                    row['seat_label'] in possible_labels):
                    return row['seat_id']
    except Exception as e:
        print(f"[!] 读取 CSV 映射表出错: {e}")
    return None

class LegacySSLAdapter(HTTPAdapter):
    """解决 Python 3.12 与校园网旧服务器 TLS 握手意外中断的问题"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.options |= getattr(ssl, "OP_IGNORE_UNEXPECTED_EOF", 0)
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)

def time_str_to_minute(time_str):
    """将 HH:MM 格式的时间转换为当天的总分钟数。如果输入 'NOW' 则返回当前时间的分钟数"""
    if time_str.strip().upper() == "NOW":
        now = time.localtime()
        return now.tm_hour * 60 + now.tm_min
    parts = time_str.split(':')
    return int(parts[0]) * 60 + int(parts[1])

def encrypt_cas_field(plaintext_str: str, server_croypto: str) -> str:
    """SSO 密码加密"""
    if not plaintext_str: return ""
    aes_key_decoded = base64.b64decode(server_croypto)
    aes_cipher = AES.new(aes_key_decoded, AES.MODE_ECB)
    padded_text = pad(plaintext_str.encode('utf-8'), AES.block_size, style='pkcs7')
    return base64.b64encode(aes_cipher.encrypt(padded_text)).decode('utf-8')

def decrypt_hmac_key(encrypted_str):
    """解密图书馆 HMAC 密钥"""
    key, iv = b"server_date_time", b"client_date_time"
    encrypted_bytes = base64.b64decode(encrypted_str)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(encrypted_bytes), AES.block_size).decode('utf-8')

def get_dynamic_headers(token, jsessionid, secret_key, method="POST"):
    """生成带有 HMAC 签名的请求头"""
    req_id = str(uuid.uuid4())
    req_date = str(int(time.time() * 1000))
    message = f"seat::{req_id}::{req_date}::{method.upper()}"
    signature = hmac.new(secret_key.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    
    return {
        "x-request-id": req_id, "x-request-date": req_date, "x-hmac-request-key": signature,
        "token": token, "cookie": f"jsq_JSESSIONID={jsessionid}",
        "logintype": "PC", "user-agent": REAL_UA, "content-type": "application/json"
    }

# ================= 3. 业务功能逻辑 =================
def get_building_id(headers, target_name):
    print(f"\n[*] 正在查询楼栋列表...")
    url = f"{BASE_API_URL}/res/buildingFloorDate"
    response = requests.post(url, headers=headers, json={}, verify=False, timeout=TIMEOUT)
    for b in response.json().get("data", {}).get("buildings", []):
        if b["name"] == target_name:
            print(f"[+] 找到楼栋: {b['name']} (ID: {b['id']})")
            return b["id"]
    return None

def get_room_id(headers, building_id, target_name, date, start_min):
    print(f"[*] 正在查询房间列表 (起始时间: {start_min//60:02d}:{start_min%60:02d})...")
    url = f"{BASE_API_URL}/res/findRoomDuration/{building_id}/{date}"
    payload = {"beginMinute": start_min, "currentPage": 1, "endMinute": 0, "floorId": 0, "minMinute": 0, "pageSize": 50, "power": False, "roomType": False, "windows": False}
    response = requests.post(url, headers=headers, json=payload, verify=False, timeout=TIMEOUT)
    for r in response.json().get("data", {}).get("pageList", []):
        if r["name"] == target_name:
            print(f"[+] 找到房间: {r['name']} (ID: {r['id']})")
            return r["id"]
    return None

def get_seat_id(headers, room_id, target_label, date, start_min, target_id=None):
    print(f"[*] 正在查询空闲座位 (寻找号码: {target_label}, ID: {target_id or '无'})...")
    url = f"{BASE_API_URL}/res/freeSeatIdsDuration/{room_id}/{date}"
    response = requests.post(url, headers=headers, json={"beginMinute": start_min, "endMinute": 0, "minMinute": 0}, verify=False, timeout=TIMEOUT)
    seats = response.json().get("data", {})
    
    clean_target_label = str(target_label).lstrip('0')
    possible_labels = {str(target_label), clean_target_label, clean_target_label.zfill(3)}

    # 1. 如果有 ID，先验证 ID 对应的 Label 是否正确
    if target_id and target_id in seats:
        info = seats[target_id]
        if info["label"] in possible_labels:
            if info["status"] == "FREE":
                print(f"[+] 找到空闲目标座位 (按 ID): {info['label']} (ID: {target_id})")
                return target_id
            else:
                print(f"[-] 座位 {info['label']} (ID: {target_id}) 当前状态为: {info['status']} (不可预约)")
                return None
        else:
            print(f"[!] 警告: 配置 ID {target_id} 对应的实际座位号是 {info['label']}，与目标 {target_label} 不符。正在重新搜索...")

    # 2. ID 不匹配或未提供，根据 Label 全量搜索
    for sid, info in seats.items():
        if info["label"] in possible_labels:
            if info["status"] == "FREE":
                print(f"[+] 找到空闲目标座位 (按 Label): {info['label']} (ID: {sid})")
                # 建议用户更新 ID 以提效
                print(f"    [建议] 请将配置中的 TARGET_SEAT_ID 更新为: \"{sid}\"")
                return sid
            else:
                print(f"[-] 座位 {info['label']} (ID: {sid}) 当前状态为: {info['status']} (不可预约)")
                return None
    
    print(f"[-] 未能在房间中找到号码为 {target_label} 的座位。")
    return None

def book_seat(headers, seat_id, date, start_min, end_min):
    print(f"[*] 发起最终预约请求: {date} {start_min//60:02d}:{start_min%60:02d} -> {end_min//60:02d}:{end_min%60:02d}")
    url = f"{BASE_API_URL}/make/freeBook/{seat_id}/{date}/-1/{end_min}?capToken=capToken"
    response = requests.post(url, headers=headers, json={}, verify=False, timeout=TIMEOUT)
    res_data = response.json()
    if res_data.get("status"):
        data = res_data['data']
        print(f"🎉 预约成功！\n    时间: {data['makeBeginStr']} - {data['makeEndStr']}")
    else:
        print(f"❌ 预约失败: {res_data.get('message')}")

# ================= 4. 主流程 =================
if __name__ == "__main__":
    ENTRY_URL = "https://libspace.cqu.edu.cn/rem/static/sso/login?redirectUrl=https://libspace.cqu.edu.cn/jsq-v/"
    
    session = requests.Session()
    session.verify = False
    session.mount("https://", LegacySSLAdapter()) # 注入适配器，解决 [SSL: UNEXPECTED_EOF_WHILE_READING]
    
    try:
        print(f"[*] 当前配置日期: {TARGET_DATE}")
        print("[1/6] 正在初始化图书馆会话并获取 SSO 地址...")
        res_entry = session.get(ENTRY_URL, verify=False, allow_redirects=False, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
        cas_login_url = res_entry.headers.get("Location")
        
      
        res_get = session.get(cas_login_url, verify=False, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
        c_key = re.search(r'id=[\'\"]login-croypto[\'\"][^>]*>([^<]+)<', res_get.text).group(1)
        e_val = re.search(r'id=[\'\"]login-page-flowkey[\'\"][^>]*>([^<]+)<', res_get.text).group(1)
        

        login_data = {"username": USERNAME, "type": "UsernamePassword", "_eventId": "submit", "execution": e_val, "croypto": c_key, "password": encrypt_cas_field(PASSWORD, c_key)}
        print("[2/6] 正在发送 SSO 登录请求...")
        res_post = session.post(cas_login_url, data=login_data, verify=False, allow_redirects=False, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
        
        if res_post.status_code in [301, 302]:
            ticket_url = res_post.headers.get("Location")
            print("[3/6] SSO 验证成功，正在自动追踪重定向以获取 Token...")
            final_res = session.get(ticket_url, verify=False, allow_redirects=True, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
            
            token_match = re.search(r'token=([^&/#]+)', final_res.url)
            lib_jwt_token = token_match.group(1)
            print(f"[+] 成功获得初级 Token: {lib_jwt_token[:15]}...")
            

            print("[4/6] 正在获取并解密系统安全配置...")
            sys_set_res = session.post("https://libspace.cqu.edu.cn/jsq/static/public/cg/getSysSet/PC", json={}, timeout=TIMEOUT)
            secret_key = decrypt_hmac_key(sys_set_res.json()["data"]["hmacKey"])
            
            # [Step 5] 激活会话并换取最终业务 Token
            print("[5/6] 正在激活业务会话并换取业务 Token...")
            auth_body = {"token": lib_jwt_token, "loginType": "PC"}
            auth_headers = {"User-Agent": REAL_UA, "logintype": "PC", "Content-Type": "application/json"}
            auth_res = session.post(f"https://libspace.cqu.edu.cn/jsq/static/public/auth/cas/{lib_jwt_token}", json=auth_body, headers=auth_headers, timeout=TIMEOUT)
            
            auth_data = auth_res.json()
            if auth_data.get("status"):
                final_biz_token = auth_data["data"]["token"] # 提取正式业务 Token
                lib_js_id = session.cookies.get("jsq_JSESSIONID")
                print(f"[+] 业务认证成功，短 Token: {final_biz_token[:15]}...")
                
                # 开始执行预约流程
                start_min = time_str_to_minute(TARGET_START_TIME)
                end_min = time_str_to_minute(TARGET_END_TIME)
                def current_headers(): return get_dynamic_headers(final_biz_token, lib_js_id, secret_key)
                
                b_id = get_building_id(current_headers(), TARGET_BUILDING)
                if b_id:
                    r_id = get_room_id(current_headers(), b_id, TARGET_ROOM, TARGET_DATE, start_min)
                    if r_id:
                        # 优先从 CSV 中查找 ID，减少 API 请求
                        csv_seat_id = get_seat_id_from_csv(TARGET_BUILDING, TARGET_ROOM, TARGET_SEAT)
                        if csv_seat_id:
                            print(f"[+] 从本地映射表命中座位 ID: {csv_seat_id}")
                        
                        s_id = get_seat_id(current_headers(), r_id, TARGET_SEAT, TARGET_DATE, start_min, csv_seat_id)
                        if s_id:
                            book_seat(current_headers(), s_id, TARGET_DATE, start_min, end_min)
                print("\n[6/6] 流程执行完毕。")
            else:
                print("[-] 业务认证激活失败:", auth_res.text)
        else:
            print("[-] SSO 登录失败")
    except Exception as e:
        print(f"\n[!] 运行出错: {str(e)}")