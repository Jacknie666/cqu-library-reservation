#!/bin/bash

# 自动数据同步脚本
# 路径：/root/lib_manage/sync_data.sh

cd /root/lib_manage

# 检查是否有文件更新
if [[ -n $(git status -s) ]]; then
    echo "$(date): Found changes, starting sync..."
    
    # 仅添加数据（由 .gitignore 确保忽略 .py 等源码）
    git add .
    
    # 提交
    git commit -m "Auto-update data: $(date +'%Y-%m-%d %H:%M:%S')"
    
    # 推送
    if git remote | grep -q 'origin'; then
        git push origin master
        if [ $? -eq 0 ]; then
            echo "$(date): Push successful! Cleaning up local data..."
            # 1. 删除已同步的数据文件（节省空间）
            # 注意：Git 会记录这些删除，建议将远程仓库作为历史存档
            rm -f data_collection/*.csv
            
            # 2. 清理临时日志文件
            if [ -f "output.log" ]; then
                > output.log
            fi
            
            # 3. 清理 Chrome 调试残留 (如果有)
            rm -rf chrome-debug/*
            
            echo "$(date): Local data cleaned."
        else
            echo "$(date): Push failed. Keeping local data for retry."
        fi
    else
        echo "$(date): Error: No remote 'origin' configured."
    fi
else
    echo "$(date): No changes detected."
fi

# 磁盘空间预警 (仅当低于 5G 时输出)
FREE_G=$(df -BG / | awk 'NR==2 {print $4}' | sed 's/G//')
if [ "$FREE_G" -lt 6 ]; then
    echo "$(date): [WARNING] Disk space is low: ${FREE_G}G available."
fi
