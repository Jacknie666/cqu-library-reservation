# CQU Library Reservation (重庆大学图书馆座位自动化系统)

![Python](https://img.shields.io/badge/Python-3.8%2B-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Active-success)

## 📌 顶层设计 & 底层逻辑

本项目的底层逻辑在于**优化学习资源分配的 ROI**。通过协议级的数据包重放与逆向工程，绕过繁琐的 Web UI 交互，直击 API 底层，实现从**座位数据采集** -> **自动化预约抢占** -> **自动签到** 的完整业务闭环。

因为信任所以简单——本项目旨在为 CQUer 提供最稳定、最高效的座位保障，让你将精力聚焦于真正有价值的学习与科研上。

## 🚀 核心抓手 (核心模块)

本项目主要由三个核心模块构成，颗粒度细化到业务的每一个环节：

### 1. `auto_book.py` (自动化预约引擎)
- **精准打击**：支持指定日期、楼栋、房间、座位的自动预约。
- **协议级加速**：无需浏览器依赖，直接封装 API 请求，毫秒级响应。
- **智能映射**：自动读取本地 `library_seat_map.csv` 将人类可读的座位号映射为系统 UUID。

### 2. `checkin.py` (自动签到系统)
- **端到端闭环**：在预约即将生效的 30 分钟窗口内自动完成签到，防止违约。
- **动态签名解密**：逆向解密了前端的 AES-256-CBC 和 HMAC-SHA256 签名算法 (`X-hmac-request-key`)。
- ⚠️ **风险提示**：目前 `checkin` 模块**仅支持闸机签到模式 (PC/Turnstile)**，不支持蓝牙/扫码签到。请注意业务边界。

### 3. `collect_seats.py` (座位数据采集探针)
- **全量感知**：周期性扫描全馆各楼栋、各阅览室的座位状态。
- **数据沉淀**：将结构化数据落盘为 CSV，为后续的数据分析和预约预测提供基本盘。

## 🛠 落地闭环 (快速开始)

### 环境依赖
确保你已经安装了 Python 3.8+，然后安装核心依赖：
```bash
pip install -r requirements.txt
```
*(主要依赖 `requests` 和 `pycryptodome`)*

### 安全红线 (配置凭证)
我们不硬编码敏感信息。你必须通过环境变量注入凭证（这是底线思维）：

```bash
# MacOS/Linux
export CQU_USERNAME="你的真实账号"
export CQU_PASSWORD="你的真实密码"

# Windows (PowerShell)
$env:CQU_USERNAME="你的真实账号"
$env:CQU_PASSWORD="你的真实密码"
```

### 运行模块

#### 预约座位
修改 `auto_book.py` 内部的 `TARGET_DATE`, `TARGET_ROOM` 等参数，然后执行：
```bash
python auto_book.py
```

#### 采集座位数据
```bash
python collect_seats.py
```

#### 自动签到 (仅支持闸机模式)
你可以使用定时模式让脚本在后台挂机，等待签到窗口开放：
```bash
python checkin.py --schedule
```

## ⚠️ 责任边界 (Disclaimer)
- 本脚本仅供学习与技术交流使用，请勿用于恶意抢占公共资源。
- 滥用导致账号被拉黑，责任自负（谁痛苦谁改变，请为自己的行为 Owner）。

---
> 打造这个工具不是为了让你卷，而是让你有更多时间做难而正确的事。