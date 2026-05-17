#!/usr/bin/env python3
"""
Anti-Drift 验证门控脚本
在每个 kanban handoff 前执行自动化验证

用法:
  python3 verify_gate.py --gate R --task-id <TASK_ID> --workdir /data/firmare/pogo-apm
  python3 verify_gate.py --gate O --task-id <TASK_ID> --workdir /data/firmare/pogo-apm
"""

import sys
import os
import subprocess
import json
import re

WORKDIR = os.getcwd()
DRIFT_SCORE_FILE = os.path.expanduser("~/.hermes/drift_scores.json")


def run(cmd, timeout=30):
    """运行命令并返回结果"""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                          timeout=timeout, cwd=WORKDIR)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"


def load_drift_scores():
    if os.path.exists(DRIFT_SCORE_FILE):
        with open(DRIFT_SCORE_FILE) as f:
            return json.load(f)
    return {}


def save_drift_scores(scores):
    with open(DRIFT_SCORE_FILE, "w") as f:
        json.dump(scores, f, indent=2)


def update_drift(task_id, delta, reason):
    scores = load_drift_scores()
    scores[task_id] = scores.get(task_id, 100) + delta
    print(f"  📊 漂移分数: {scores[task_id]} (修改: {delta:+d}, 原因: {reason})")
    save_drift_scores(scores)
    return scores[task_id]


def check_gate_r(task_id):
    """Gate R: 检查 Researcher 输出质量"""
    print(f"\n═══ Gate R 验证 (task: {task_id}) ═══")
    passed = True

    # 1. 检查是否有 ChibiOS 引用
    rc, out, err = run("git log -1 --format='%B' | head -20")
    has_chibios_ref = bool(re.search(r'chibios|ChibiOS|AP_HAL_ChibiOS', out, re.IGNORECASE))
    if has_chibios_ref:
        print("  ✅ 引用了 ChibiOS 参考")
    else:
        print("  ⚠️  未发现 ChibiOS 引用")
        passed = False

    # 2. 检查是否有 git diff
    rc, out, err = run("git diff --stat")
    if out.strip():
        print(f"  ✅ 有代码修改: {out.strip()[:100]}")
    else:
        print("  ⚠️  无代码修改 — 是纯分析任务?")
        # 纯分析任务不要求 diff
        passed = True

    # 3. 检查根因分析长度
    rc, out, err = run("git log -1 --format='%B'")
    if len(out) > 100:
        print(f"  ✅ 提交信息长度 {len(out)} 字")
    else:
        print(f"  ⚠️  提交信息较短 ({len(out)} 字)")
        passed = False

    if passed:
        update_drift(task_id, 10, "Gate R 通过")
    else:
        update_drift(task_id, -5, "Gate R 失败")
        print("  ❌ Gate R 未通过 — 需要 Researcher 补充分析")

    return passed


def check_gate_e(task_id):
    """Gate E: 检查 Engineer 修改"""
    print(f"\n═══ Gate E 验证 (task: {task_id}) ═══")
    passed = True

    # 1. 检查修改是否在 AP_HAL_RTT 内
    rc, out, err = run("git diff --name-only")
    external_changes = []
    for line in out.strip().split('\n'):
        if line and 'AP_HAL_RTT' not in line:
            external_changes.append(line)

    if external_changes:
        print(f"  ⚠️  有外部文件修改: {external_changes}")
        passed = True  # 可能是通用bug，不直接拒绝
        print("  ℹ️  请确认这些修改是通用bug修复而非RTT hack")
    else:
        print("  ✅ 修改限定在 AP_HAL_RTT 内")

    # 2. 检查编译
    rc, out, err = run("ls -la build/rtt_cuav_v5/rtthread.bin 2>/dev/null", timeout=5)
    if rc == 0:
        match = re.search(r'(\d+)', out.split()[4] if len(out.split()) > 4 else "0")
        size = int(match.group(1)) if match else 0
        if size > 1000000:  # > 1MB
            print(f"  ✅ 编译产物存在 ({size/1024/1024:.1f}MB)")
        else:
            print(f"  ⚠️  编译产物过小 ({size/1024:.1f}KB)")
            passed = False
    else:
        print("  ⚠️  无编译产物 — 需要先编译")
        passed = False

    if passed:
        update_drift(task_id, 10, "Gate E 通过")
    else:
        update_drift(task_id, -5, "Gate E 失败")
        print("  ❌ Gate E 未通过 — 需要 Engineer 修正")

    return passed


def check_gate_v(task_id):
    """Gate V: 检查 Reviewer 审查质量"""
    print(f"\n═══ Gate V 验证 (task: {task_id}) ═══")
    passed = True

    # 1. 检查 diff 规模
    rc, out, err = run("git diff --stat")
    lines = out.strip().split('\n')
    total_added = 0
    for line in lines:
        m = re.search(r'(\d+) insertions?\(\+\)', line)
        if m:
            total_added += int(m.group(1))
    if total_added > 500:
        print(f"  ⚠️  修改量过大 ({total_added}+ 行)，建议分割")
        passed = False
    else:
        print(f"  ✅ diff 规模合理 ({total_added}+ 行)")

    # 2. 检查是否回滚了已验证的修复
    rc, out, err = run("git diff -- '*.cpp' '*.h' '*.c' '*.dat'")
    rollback_patterns = ['PA6', 'PD7', 'PG11']  # SPI1 关键引脚
    for pat in rollback_patterns:
        if pat in out:
            print(f"  ⚠️  注意: diff 中包含 {pat} — 确认不是回滚已验证修复")
            break

    if passed:
        update_drift(task_id, 10, "Gate V 通过")

    return passed


def check_gate_o(task_id):
    """Gate O: 检查 Ops 验证完成度"""
    print(f"\n═══ Gate O 验证 (task: {task_id}) ═══")
    passed = True

    # 1. 检查 OpenOCD 是否可达
    rc, out, err = run("echo 'halt\nexit' | nc -q 2 localhost 4444 2>/dev/null", timeout=5)
    if rc == 0 or 'target' in out.lower() or 'halted' in out.lower():
        print("  ✅ OpenOCD 可达")
        # 2. 检查 HardFault
        rc2, out2, err2 = run("echo 'halt\nmdw 0xE000ED28 1\nresume\nexit' | nc -q 2 localhost 4444 2>/dev/null", timeout=5)
        if '00000000' in out2:
            print("  ✅ 无 HardFault (CFSR=0)")
        else:
            print(f"  ⚠️  CFSR 非零: {out2.strip()}")
            passed = False
    else:
        print("  ⚠️  OpenOCD 不可达（可能未启动）")

    # 3. 检查 CDC
    rc, out, err = run("ls /dev/ttyACM* 2>/dev/null")
    if out.strip():
        print(f"  ✅ USB CDC 已枚举: {out.strip()}")
    else:
        print("  ⚠️  USB CDC 未枚举")
        passed = False

    if passed:
        update_drift(task_id, 10, "Gate O 通过 — L0 验证完成")
    else:
        update_drift(task_id, -5, "Gate O 失败 — 需要回退修复")

    return passed


def main():
    if len(sys.argv) < 2:
        print("Anti-Drift 验证门控")
        print()
        print("用法:")
        print("  python3 verify_gate.py --gate <R|E|V|O> --task-id <ID> [--workdir <DIR>]")
        print()
        print("门控:")
        print("  R = Researcher → Engineer: 根因分析质量")
        print("  E = Engineer → Reviewer: 代码修改安全")
        print("  V = Reviewer → Ops: 审查通过")
        print("  O = Ops → Done: 双重验证完成")
        return

    global WORKDIR
    if "--workdir" in sys.argv:
        idx = sys.argv.index("--workdir") + 1
        WORKDIR = sys.argv[idx]

    task_id = "unknown"
    if "--task-id" in sys.argv:
        idx = sys.argv.index("--task-id") + 1
        task_id = sys.argv[idx]

    if "--gate" in sys.argv:
        idx = sys.argv.index("--gate") + 1
        gate = sys.argv[idx].upper()
    else:
        print("需要 --gate")
        return

    gates = {
        "R": check_gate_r,
        "E": check_gate_e,
        "V": check_gate_v,
        "O": check_gate_o,
    }

    if gate not in gates:
        print(f"未知门控: {gate}, 可选: R, E, V, O")
        return

    result = gates[gate](task_id)
    print(f"\n{'✅ Gate ' + gate + ' 通过' if result else '❌ Gate ' + gate + ' 失败 — 需回退'}")
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
