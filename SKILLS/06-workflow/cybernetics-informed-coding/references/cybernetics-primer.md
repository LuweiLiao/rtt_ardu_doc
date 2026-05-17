# Engineering Cybernetics Primer for AI Coding Agents
# 工程控制论 — AI编码Agent速查手册

> Source: Tsien, H.S. *Engineering Cybernetics*, 1954 (钱学森《工程控制论》)
> This primer extracts actionable principles for coding from each chapter.

## Core Philosophy
**Control theory is about organization, not energy.** Focus on information flow, feedback loops, and system behavior — not computational cost.

**Feedback replaces precise knowledge.** Instead of modeling everything perfectly, build systems that learn from errors.

## 7 Actionable Principles

1. **Every change must be verified** (Ch.4 Feedback) — No open-loop operations
2. **Small steps, fast iterations** (Ch.8 Time Delay) — When feedback delay is large, change one thing at a time
3. **Define progress metrics** (Ch.11 Lyapunov) — Ensure V(t) is always decreasing
4. **Adapt to environment** (Ch.12 Time-Varying) — Adjust strategy when conditions change
5. **Explore actively** (Ch.15 Extremum Seeking) — Don't stay at local optima
6. **Tolerate noise** (Ch.9/16 Filtering) — Optimize statistically, not per-instance
7. **Redundancy for reliability** (Ch.18 Error Control) — Multiple fallback strategies

## Diagnostic Framework

| Problem | Cybernetics Chapter | Action |
|---------|-------------------|--------|
| Compile error | Ch.4 Feedback Servo | Use error as signal, fix, verify |
| Same code changed repeatedly | Ch.10/11 Limit Cycle | Break oscillation with strategy switch |
| Fix A breaks B | Ch.5 Decoupling | Orthogonal design, independent verification |
| Progress stalled | Ch.15 Extremum Seeking | Add perturbation, try different approach |
| Uncertain what to do | Ch.17 Ultrastability | Random strategy, lock when stable |

## Code Comment Convention
```
[Cybernetics Ch.X] Brief description
```

## Key Mathematical Concepts
- Transfer function G(s) = Y(s)/X(s) — system "DNA"
- Feedback: e(t) = r(t) - y(t) — error drives correction
- Stability: poles in left half plane → system converges
- Lyapunov: dV/dt < 0 → system always improving
- Extremum seeking: perturb → measure → adjust direction
- Ultrastability: fail → random switch → lock stable mode
