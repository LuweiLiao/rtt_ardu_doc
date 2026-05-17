---
name: openclaw-cc-sigkill-debug
description: Debug Claude Code SIGKILL in OpenClaw — trace timeout layers, find root cause, apply fixes
---

# OpenClaw Claude Code SIGKILL 调试

## 症状
Claude Code (CC) 通过 OpenClaw kimi-claw bridge 运行时，被 SIGKILL 杀掉，通常在 10 分钟左右。

## ⚠️ 关键结论：kimi-claw 不受 watchdog 影响

**kimi-claw 使用 ACP (Agent Communication Protocol) 协议，不注册 CLI backend。**

代码路径：`backend.reliability?.watchdog?.fresh/resume`（auth-profiles JS L214436）
→ 对 ACP backends 返回 `null`（可选链 `?.`）
→ **watchdog 的 10 分钟 SIGKILL 逻辑完全不适用于 kimi-claw**

`CLI_FRESH_WATCHDOG_DEFAULTS.maxMs=600000` 仅适用于通过 `registerCliBackend()`
注册的本地 CLI 后端。kimi-claw 不调用 `registerCliBackend()`，它走 ACP dispatch。

因此，**如果 CC 只在 OpenClaw + kimi-claw 路径下被杀，watchdog 不是原因**。
检查其他超时层（agent timeoutSeconds、Hermes gateway_timeout、Cursor abortController）。

## 超时架构（三层）

| 层级 | 超时类型 | 默认值 | 配置位置 |
|------|----------|--------|----------|
| OpenClaw CLI runner `noOutputTimeout` | CC 无 stdout 输出 | maxMs=600000 (10min) 硬编码 | `agents.defaults.cliBackends.*.reliability.watchdog.fresh.maxMs` |
| OpenClaw agent `timeoutSeconds` | 整体 agent turn | 1800 (30min) | `~/.openclaw/openclaw.json` → `agents.defaults.timeoutSeconds` |
| Hermes gateway `gateway_timeout` | Hermes 不活动检测（非固定超时，基于 `_touch_activity()`） | 1800 (30min) | `~/.hermes/config.yaml` → `agent.gateway_timeout` |

## 关键代码位置

### OpenClaw CLI runner SIGKILL 源
```
~/.nvm/versions/node/v24.14.1/lib/node_modules/openclaw/dist/auth-profiles-B5ypC5S-.js
```
- 行 40748-40750: `cancelAdapter = (_reason) => { adapter.kill("SIGKILL"); }`
- 行 40755-40757: `noOutputTimer` 触发 `requestCancel("no-output-timeout")`
- 行 40751-40753: `timeoutTimer` 触发 `requestCancel("overall-timeout")`

### Watchdog 默认值
```
~/.nvm/versions/node/v24.14.1/lib/node_modules/openclaw/dist/cli-watchdog-defaults-ay_R4q8w.js
```
- `CLI_FRESH_WATCHDOG_DEFAULTS`: `{ noOutputTimeoutRatio: 0.8, minMs: 180000, maxMs: 600000 }`
- `CLI_RESUME_WATCHDOG_DEFAULTS`: `{ noOutputTimeoutRatio: 0.3, minMs: 60000, maxMs: 180000 }`

### Hermes gateway inactivity 检测
```
~/.hermes/hermes-agent/gateway/run.py
```
- 行 7370+: 基于 `agent.get_activity_summary()` 的 `seconds_since_activity`
- 检查间隔 5 秒，只杀真正无活动的 agent

### kimi-claw terminal bridge
```
~/.openclaw/extensions/kimi-claw/dist/src/terminal-session-manager.js
```
- `idleTimeoutMs`: 432000 (5天) — 不是问题
- `maxDurationMs`: 432000 (5天) — 不是问题

## 调试步骤

### 1. 确认进程关系
```bash
ps -eo pid,ppid,pgid,cmd | grep -i openclaw | grep -v grep
```

### 2. 检查当前超时配置
```bash
# OpenClaw timeout
cat ~/.openclaw/openclaw.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d.get('agents',{}), indent=2))"

# Hermes gateway timeout
grep -n "gateway_timeout" ~/.hermes/config.yaml
```

### 3. 在 minified JS 中搜索超时逻辑
```bash
# 找 SIGKILL 来源
grep -oP '.{0,200}(adapter\.kill|requestCancel).{0,200}' openclaw/dist/auth-profiles-*.js

# 找 watchdog 默认值
cat openclaw/dist/cli-watchdog-defaults-*.js

# 排除无关语言文件
grep -v "blade\|hack\|emacs\|php\|racket\|sql"
```

## 修复方案

### 快速修复：增大 OpenClaw 和 Hermes 超时
```bash
# OpenClaw: 30min → 2h
openclaw config set agents.defaults.timeoutSeconds 7200

# Hermes: 编辑 config.yaml
# agent.gateway_timeout: 1800 → 7200
# agent.gateway_timeout_warning: 900 → 3600
```

### 彻底修复：覆盖 watchdog maxMs（如需要）
在 `~/.openclaw/openclaw.json` 的 `agents.defaults.cliBackends` 中配置：
```json
{
  "agents": {
    "defaults": {
      "cliBackends": {
        "<backend-key>": {
          "reliability": {
            "watchdog": {
              "fresh": { "maxMs": 1800000 },
              "resume": { "maxMs": 600000 }
            }
          }
        }
      }
    }
  }
}
```

**⚠️ 不要在 `openclaw.json` 顶层添加 `backend` key** — config validation 会
报 `Unrecognized key: "backend"` 并拒绝启动。watchdog 配置必须嵌套在
`agents.defaults.cliBackends` 下，由 CLI runner 的 `pickWatchdogProfile()` 读取。

## 重要认知

1. **kimi-claw 使用 ACP 协议，watchdog 不适用** — `backend.reliability?.watchdog` 为 null，10 分钟 SIGKILL 不会触发
2. **CC 在 kimi-claw 下实际运行在 kimi 服务器侧** — 不是本地子进程，本地只做 ACP 代理转发
3. **Hermes gateway_timeout 是 inactivity 检测，不是固定超时** — 只要 CC 在执行 tool call/API 调用，Hermes 就不会杀它
4. **noOutputTimeout 仅适用于 registerCliBackend 后端** — kimi-claw 不走这条路
5. **kimi-claw terminal bridge 超时是 5 天** — 不是问题
6. **CC 的 API_TIMEOUT_MS=3000000 (50分钟)** — 足够
7. **修改 Hermes gateway_timeout 后需要重启 gateway 才能生效**
8. **Cursor 扩展 v2.1.114 使用 `abortController.abort()` 发 SIGTERM** — 这是 Cursor 特有的，与 OpenClaw 无关

## 超时链汇总（已验证）

| 组件 | 超时 | 值 | 是否影响 kimi-claw |
|------|------|-----|-------------------|
| Hermes gateway_timeout | inactivity | 7200s (2h) | ✅ 仅无活动时 |
| OpenClaw timeoutSeconds | agent turn | 7200s (2h) | ✅ |
| kimi-claw promptTimeoutMs | prompt | 1800000s (30min) | ✅ |
| CLI watchdog maxMs | no-output | 600000 (10min) | ❌ 不适用（ACP） |
| Cursor abortController | user cancel | N/A | N/A（Cursor 特有）|
