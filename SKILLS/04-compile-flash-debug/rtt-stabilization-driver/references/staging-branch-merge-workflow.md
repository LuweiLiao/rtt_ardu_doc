# Staging 分支合并工作流

RTT 移植在 `staging/pogo-rtt` 分支上开发。稳定后的功能提交通过短期子分支管理。

## 分支结构

```
remotes/origin/staging/pogo-rtt    ← 远程主开发分支（可 push）
  ↕ (merge --no-ff, 推送前工作)
staging/pogo-rtt                   ← 本地主开发分支
  ↕ (子分支完成后 merge --no-ff)
staging/pogo-rtt-clean             ← 子分支（架构清理 + 稳定性修复）
staging/pogo-rtt-feature-name      ← 其他特性子分支
```

## 工作流

### 1. 创建子分支

```bash
# 从 staging/pogo-rtt 最新状态创建子分支
git checkout staging/pogo-rtt
git branch staging/pogo-rtt-clean
git checkout staging/pogo-rtt-clean
```

### 2. 在子分支上开发

- 每个 commit 独立可验证（编译→烧录→双重验证）
- 按阶段逐步提交，不堆积未验证代码
- commit 信息格式：`type(scope): message`

### 3. 合并回主分支

```bash
git checkout staging/pogo-rtt
git merge staging/pogo-rtt-clean --no-ff -m "merge(scope): 描述合并内容"
```

### 4. 经验证的合并不再回退

- 合并前确保工作区 **clean**（无未提价修改）
- 合并后不再做额外修改——要改就在子分支上改完再合
- 如发现合并后的代码有问题，在子分支修复后重新合并，**不直接在主分支上 revert**

## 本会话示范（2026-05-10）

```text
staging/pogo-rtt-clean（10 commits, 含 Phase 0 + 64KB 栈 + IWDG 修复）
  ↕ merge-base = b8a92f78f0（与 staging/pogo-rtt 同基）
staging/pogo-rtt（5 commits, 含 IOMCU 启用等）

合并: 3c37a30ea4 — merge(l0): 稳定 L0 基线
  25 files changed, 104 insertions(+), 381 deletions(-)
  无冲突 → 快进合并（ort 策略）
  合并后 HEAD 在 staging/pogo-rtt，包含所有 15 commits
```
