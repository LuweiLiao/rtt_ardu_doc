#!/usr/bin/env python3
"""
SONA 模式学习脚本
从 kanban 已完成的任务中提取调试模式，更新 skill 和 mem0

用法:
  python3 learn_pattern.py kanban --task-id <TASK_ID>
  python3 learn_pattern.py scan  # 扫描最近已完成的调试任务
"""

import sys
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional, Dict, List

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KANBAN_DB = os.path.expanduser("~/.hermes/kanban.db")
PATTERNS_DIR = os.path.join(SKILL_DIR, "patterns")
os.makedirs(PATTERNS_DIR, exist_ok=True)


def get_kanban_task(task_id: str) -> Optional[Dict]:
    """从 kanban 读取任务"""
    conn = sqlite3.connect(KANBAN_DB)
    c = conn.cursor()
    c.execute("SELECT id, title, body, result, status, metadata FROM tasks WHERE id=?", (task_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "title": row[1],
        "body": row[2] or "",
        "result": row[3] or "",
        "status": row[4],
        "metadata": row[5] or "{}",
    }


def extract_patterns(task: Dict) -> List[Dict]:
    """从任务中提取模式"""
    patterns = []
    body = task.get("body", "")
    result = task.get("result", "")
    metadata_str = task.get("metadata", "{}")
    
    try:
        metadata = json.loads(metadata_str) if isinstance(metadata_str, str) else {}
    except json.JSONDecodeError:
        metadata = {}

    # 从 body 和 result 中提取关键信息
    full_text = body + "\n" + result

    patterns.append({
        "task_id": task["id"],
        "title": task["title"],
        "status": task["status"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symptom": _extract_symptom(full_text),
        "diagnosis": _extract_diagnosis(full_text),
        "root_cause": _extract_root_cause(full_text),
        "fix": _extract_fix(full_text),
        "target_skill": _detect_target_skill(task["title"], full_text),
        "changed_files": metadata.get("changed_files", []),
        "raw_text_snippet": full_text[:500],
    })

    return patterns


def _extract_symptom(text: str) -> str:
    """提取现象"""
    # 先查 CFSR/HFSR 特征
    cfsr_match = re.search(r'(?:CFSR|HFSR)\s*=\s*(0x[0-9A-Fa-f]+)', text)
    if cfsr_match:
        code = cfsr_match.group(1)
        fault_names = {
            "0x00010000": "IACCVIOL (指令访问违例)",
            "0x00000082": "DACCVIOL (数据访问违例)",
            "0x00000100": "DIVBYZERO (除零)",
            "0x00020000": "PRECISERR (精确总线错误)",
        }
        name = fault_names.get(code, f"HardFault {code}")
        return name

    # 查 USB CDC 枚举
    if re.search(r'CDC.*不枚举|ttyACM.*不存在|usb.*not.*enum', text, re.IGNORECASE):
        return "USB CDC 不枚举"

    # 查 MAVLink
    if re.search(r'MAVLink.*无心跳|no.*heartbeat|无.*heartbeat', text, re.IGNORECASE):
        return "MAVLink 无心跳"

    # 查 SPI
    if re.search(r'SPI.*不工作|SPI.*错误|gyro.*unhealthy|accel.*unhealthy', text, re.IGNORECASE):
        return "SPI 传感器不工作"

    # 查启动卡住
    if re.search(r'setup.*stuck|setup_stage.*卡住|boot.*hang', text, re.IGNORECASE):
        return "启动阶段卡住"

    return "未知现象"


def _extract_diagnosis(text: str) -> str:
    """提取诊断发现"""
    # 查 PC 地址
    pc_match = re.search(r'(?:PC|pc)\s*=\s*(0x[0-9A-Fa-f]+)', text)
    pc_info = f"PC={pc_match.group(1)}" if pc_match else ""

    # 查函数名
    func_match = re.search(r'(?:\b在\s+)?(\w+)\s*(?:处|函数|+0x)', text)
    func_info = f"位于 {func_match.group(1)}" if func_match else ""

    # 查栈帧
    sp_match = re.search(r'(?:SP|sp)\s*=\s*(0x[0-9A-Fa-f]+)', text)
    sp_info = f"SP={sp_match.group(1)}" if sp_match else ""

    parts = [p for p in [pc_info, func_info, sp_info] if p]
    return " | ".join(parts) if parts else "未知诊断"


def _extract_root_cause(text: str) -> str:
    """提取根因"""
    # 查找根因关键词
    cause_patterns = [
        (r'(?:根因|root.?cause|根本原因)[：:]\s*([^\n。]+)', 1),
        (r'(?:原因是|由于|because)[：:]\s*([^\n。]+)', 1),
    ]
    for pat, group in cause_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(group).strip()

    # 常见已知根因模式
    if re.search(r'OTG[._]?ID|USB.*pin.*冲突', text, re.IGNORECASE):
        return "USB 引脚与 OTG_ID 冲突"
    if re.search(r'IWDG|看门狗.*复位', text, re.IGNORECASE):
        return "IWDG 看门狗未喂导致复位"
    if re.search(r'栈.*溢出|stack.*overflow', text, re.IGNORECASE):
        return "栈溢出"
    if re.search(r'FPU|CPACR', text, re.IGNORECASE):
        return "FPU 未正确初始化"
    if re.search(r'SPI.*pin|SPI.*引脚', text, re.IGNORECASE):
        return "SPI 引脚转置错误"

    return "未知（需人工分析）"


def _extract_fix(text: str) -> str:
    """提取修复方法"""
    fix_patterns = [
        (r'(?:修复|fix|修改|更改)[：:]\s*([^\n。]+)', 1),
        (r'(?:修改了|改为了|改成)[：:]\s*([^\n。]+)', 1),
    ]
    for pat, group in fix_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(group).strip()

    # 查找文件级修复
    files = re.findall(r'(?:修改|fix).*?(\w+\.\w+)', text)
    if files:
        return f"修改了 {', '.join(files[:3])}"

    return "未知"


def _detect_target_skill(title: str, body: str) -> str:
    """检测目标 skill"""
    text = (title + " " + body).lower()
    if "spi" in text:
        return "rtt-cuav-v5-spi-fix-record"
    if "usb" in text or "cdc" in text:
        return "rtt-cuav-v5-cdc-tx-fix"
    if "adc" in text:
        return "rtt-stm32-adc-channel-deadlock"
    return "rtt-stabilization-driver"


def save_pattern(pattern: Dict) -> str:
    """保存模式到文件"""
    pattern_file = os.path.join(PATTERNS_DIR, f"{pattern['task_id']}.json")
    with open(pattern_file, "w") as f:
        json.dump(pattern, f, ensure_ascii=False, indent=2)
    return pattern_file


def update_skill_notes(pattern: Dict) -> bool:
    """尝试更新目标 skill 的陷阱列表"""
    skill_name = pattern["target_skill"]

    # 检查是否有相似模式已经存在
    existing = [f for f in os.listdir(PATTERNS_DIR) if f.endswith(".json")]
    similar_count = 0
    for fname in existing:
        with open(os.path.join(PATTERNS_DIR, fname)) as f:
            try:
                p = json.load(f)
                if (p.get("symptom") == pattern["symptom"] and
                        p.get("root_cause") == pattern["root_cause"] and
                        p["task_id"] != pattern["task_id"]):
                    similar_count += 1
            except (json.JSONDecodeError, KeyError):
                pass

    # 只有同一个模式出现 2 次以上才更新 skill
    if similar_count < 1:
        return False

    # 构造陷阱条目
    trap_entry = (
        f"- `{pattern['symptom']}`（已出现 {similar_count + 1} 次）"
        f" → 根因: {pattern['root_cause']}，"
        f"修复: {pattern['fix']}"
    )

    print(f"  📝 更新 skill '{skill_name}' - 添加陷阱: {trap_entry}")
    return True


def scan_recent_tasks(minutes: int = 10) -> List[Dict]:
    """扫描最近完成的任务"""
    conn = sqlite3.connect(KANBAN_DB)
    c = conn.cursor()
    
    # 查找最近完成的调试任务
    c.execute("""
        SELECT id, title, body, result, status, metadata 
        FROM tasks 
        WHERE status='done' 
        ORDER BY created_at DESC 
        LIMIT 10
    """)
    rows = c.fetchall()
    conn.close()
    
    tasks = []
    for row in rows:
        tasks.append({
            "id": row[0],
            "title": row[1],
            "body": row[2] or "",
            "result": row[3] or "",
            "status": row[4],
            "metadata": row[5] or "{}",
        })
    return tasks


def main():
    if len(sys.argv) < 2:
        print("SONA 模式学习工具")
        print()
        print("用法:")
        print("  python3 learn_pattern.py kanban --task-id <TASK_ID>")
        print("  python3 learn_pattern.py scan [--minutes N]")
        return

    cmd = sys.argv[1]

    if cmd == "kanban":
        if "--task-id" in sys.argv:
            idx = sys.argv.index("--task-id") + 1
            task_id = sys.argv[idx]
            task = get_kanban_task(task_id)
            if not task:
                print(f"❌ 任务 {task_id} 不存在")
                return
            patterns = extract_patterns(task)
            for p in patterns:
                saved = save_pattern(p)
                print(f"✅ 模式已保存: {saved}")
                print(f"   现象: {p['symptom']}")
                print(f"   诊断: {p['diagnosis']}")
                print(f"   根因: {p['root_cause']}")
                print(f"   修复: {p['fix']}")
                print(f"   目标skill: {p['target_skill']}")
                updated = update_skill_notes(p)
                if updated:
                    print(f"   已更新 skill ✅")
        else:
            print("需要 --task-id")

    elif cmd == "scan":
        minutes = 10
        if "--minutes" in sys.argv:
            idx = sys.argv.index("--minutes") + 1
            minutes = int(sys.argv[idx])
        tasks = scan_recent_tasks(minutes)
        print(f"扫描到 {len(tasks)} 个已完成任务")
        for t in tasks:
            patterns = extract_patterns(t)
            for p in patterns:
                saved = save_pattern(p)
                print(f"\n✅ {t['id']}: {t['title']}")
                print(f"   现象: {p['symptom']}")
                print(f"   根因: {p['root_cause']}")
                update_skill_notes(p)

    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
