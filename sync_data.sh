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
        echo "$(date): Push successful!"
    else
        echo "$(date): Error: No remote 'origin' configured."
    fi
else
    echo "$(date): No changes detected."
fi
