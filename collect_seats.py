import os
import csv
import base64
import requests
import urllib3
import re
import uuid
import time
import hmac
import hashlib
from datetime import datetime
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ================= 1. 配置区 =================
USERNAME = os.environ.get("CQU_USERNAME", "")
PASSWORD = os.environ.get("CQU_PASSWORD", "")
TIMEOUT = 30
SLEEP_INTERVAL = 1800  # 每次扫描的间隔时间，单位为秒 (1800秒 = 30分钟)
REAL_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
BASE_API_URL = "https://libspace.cqu.edu.cn/jsq/static/frontApi"

# 数据保存目录
SAVE_DIR = "data_collection"
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

# ================= 2. 工具函数 =================
def get_current_minute():
    now = time.localtime()
    return now.tm_hour * 60 + now.tm_min

def encrypt_cas_field(plaintext_str: str, server_croypto: str) -> str:
    if not plaintext_str: return ""
    aes_key_decoded = base64.b64decode(server_croypto)
    aes_cipher = AES.new(aes_key_decoded, AES.MODE_ECB)
    padded_text = pad(plaintext_str.encode('utf-8'), AES.block_size, style='pkcs7')
    return base64.b64encode(aes_cipher.encrypt(padded_text)).decode('utf-8')

def decrypt_hmac_key(encrypted_str):
    key, iv = b"server_date_time", b"client_date_time"
    encrypted_bytes = base64.b64decode(encrypted_str)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(encrypted_bytes), AES.block_size).decode('utf-8')

def get_dynamic_headers(token, jsessionid, secret_key, method="POST"):
    req_id, req_date = str(uuid.uuid4()), str(int(time.time() * 1000))
    message = f"seat::{req_id}::{req_date}::{method.upper()}"
    signature = hmac.new(secret_key.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
    return {
        "x-request-id": req_id, "x-request-date": req_date, "x-hmac-request-key": signature,
        "token": token, "cookie": f"jsq_JSESSIONID={jsessionid}",
        "logintype": "PC", "user-agent": REAL_UA, "content-type": "application/json"
    }

# ================= 3. 数据采集逻辑 =================
def get_all_buildings(headers):
    print("[*] 获取楼栋列表...")
    response = requests.post(f"{BASE_API_URL}/res/buildingFloorDate", headers=headers, json={}, verify=False, timeout=TIMEOUT)
    return response.json().get("data", {}).get("buildings", [])

def get_rooms_in_building(headers, building_id, date, start_min):
    print(f"  [-] 获取楼栋 {building_id} 的房间...")
    payload = {"beginMinute": start_min, "currentPage": 1, "endMinute": 0, "floorId": 0, "minMinute": 0, "pageSize": 1000, "power": False, "roomType": False, "windows": False}
    response = requests.post(f"{BASE_API_URL}/res/findRoomDuration/{building_id}/{date}", headers=headers, json=payload, verify=False, timeout=TIMEOUT)
    return response.json().get("data", {}).get("pageList", [])

def get_seats_in_room(headers, room_id, date, start_min):
    payload = {"beginMinute": start_min, "endMinute": 0, "minMinute": 0}
    response = requests.post(f"{BASE_API_URL}/res/freeSeatIdsDuration/{room_id}/{date}", headers=headers, json=payload, verify=False, timeout=TIMEOUT)
    return response.json().get("data", {})

# ================= 4. 主爬虫流程 =================
def run_collection():
    current_date = datetime.now().strftime("%Y-%m-%d")
    now_min = get_current_minute()
    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    ENTRY_URL = "https://libspace.cqu.edu.cn/rem/static/sso/login?redirectUrl=https://libspace.cqu.edu.cn/jsq-v/"
    session = requests.Session()
    
    try:
        # [鉴权阶段]
        print("[1] 登录并获取身份凭证...")
        res_entry = session.get(ENTRY_URL, verify=False, allow_redirects=False, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
        cas_url = res_entry.headers.get("Location")
        res_get = session.get(cas_url, verify=False, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
        
        c_key = re.search(r'id=[\'\"]login-croypto[\'\"][^>]*>([^<]+)<', res_get.text).group(1)
        e_val = re.search(r'id=[\'\"]login-page-flowkey[\'\"][^>]*>([^<]+)<', res_get.text).group(1)
        login_data = {"username": USERNAME, "type": "UsernamePassword", "_eventId": "submit", "execution": e_val, "croypto": c_key, "password": encrypt_cas_field(PASSWORD, c_key)}
        res_post = session.post(cas_url, data=login_data, verify=False, allow_redirects=False, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
        
        final_res = session.get(res_post.headers.get("Location"), verify=False, allow_redirects=True, timeout=TIMEOUT, headers={'User-Agent': REAL_UA})
        lib_jwt = re.search(r'token=([^&/#]+)', final_res.url).group(1)
        
        sys_res = session.post("https://libspace.cqu.edu.cn/jsq/static/public/cg/getSysSet/PC", json={}, timeout=TIMEOUT)
        sec_key = decrypt_hmac_key(sys_res.json()["data"]["hmacKey"])
        
        auth_res = session.post(f"https://libspace.cqu.edu.cn/jsq/static/public/auth/cas/{lib_jwt}", json={"token": lib_jwt, "loginType": "PC"}, headers={"logintype": "PC", "Content-Type": "application/json", "User-Agent": REAL_UA}, timeout=TIMEOUT)
        biz_token = auth_res.json()["data"]["token"]
        js_id = session.cookies.get("jsq_JSESSIONID")
        
        def headers(): return get_dynamic_headers(biz_token, js_id, sec_key)
        
        # [采集阶段]
        print("\n[2] 开始全局座位扫描...")
        all_seat_data = []
        buildings = get_all_buildings(headers())
        
        for b in buildings:
            b_name = b['name']
            rooms = get_rooms_in_building(headers(), b['id'], current_date, now_min)
            for r in rooms:
                r_name = r['name']
                print(f"  -> 正在抓取: {b_name} - {r_name}")
                seats = get_seats_in_room(headers(), r['id'], current_date, now_min)
                
                for sid, info in seats.items():
                    all_seat_data.append({
                        "timestamp": timestamp_str,
                        "date": current_date,
                        "time_minute": now_min,
                        "building": b_name,
                        "room": r_name,
                        "seat_id": sid,
                        "seat_label": info.get("label", ""),
                        "status": info.get("status", "UNKNOWN"),
                        "power": info.get("power", False),
                        "window": info.get("window", False)
                    })
                time.sleep(0.5) # 防止请求过快被服务器拦截
        
        # [存储阶段]
        csv_filename = os.path.join(SAVE_DIR, f"seats_snapshot_{timestamp_str}.csv")
        print(f"\n[3] 扫描完成！共采集到 {len(all_seat_data)} 个座位数据。")
        print(f"正在保存至: {csv_filename}")
        
        with open(csv_filename, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=all_seat_data[0].keys())
            writer.writeheader()
            writer.writerows(all_seat_data)
            
        print("🎉 数据保存成功！")
        
    except Exception as e:
        print(f"\n[!] 运行出错: {str(e)}")

if __name__ == "__main__":
    print("🚀 开始启动服务器后台扫描任务...")
    print(f"⏱️  设定的扫描间隔为: {SLEEP_INTERVAL} 秒 ({SLEEP_INTERVAL/60:.1f} 分钟)\n")
    
    while True:
        print(f"=== 新一轮扫描开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===")
        run_collection()
        
        print(f"\n💤 本轮结束，等待 {SLEEP_INTERVAL/60:.1f} 分钟后进行下一次扫描...\n")
        time.sleep(SLEEP_INTERVAL)
