# 📚 CQU Library Reservation System

> **底层逻辑**：针对图书馆位稀缺痛点，通过自动化脚本实现座位的高效预约与资源抢占。将技术红利转化为学习效率。

---

## 🚀 核心功能 (Features)
*   **自动预约**：支持设定特定时间点触发预约请求。
*   **并发抢占**：在高并发场景下利用脚本优势实现秒级锁定。
*   **多账号支持**：灵活切换不同账号进行资源分配。

## 🛠 技术架构 (Stack)
*   **Language**: Python / Shell
*   **Library**: Requests / Selenium
*   **Schedule**: Crontab / GitHub Actions

## 📖 使用说明 (Usage)
1. 配置 `config.py` 中的学号与密码。
2. 设置目标座位号及时间段。
3. 运行脚本：`bash run.sh`

---
*Created by Jacknie666*
