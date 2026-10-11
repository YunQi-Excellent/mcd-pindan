#!/usr/bin/env python3
"""根据示例数据生成 README 用的折线图（纯标准库输出 SVG，GitHub 可直接渲染）。

做法：取示例菜单里同一档次的餐品，把参与者从 1 人递增到 MAX_PEOPLE 人，
分别算出「一起拼一单」与「各点各的」的人均实付，画在同一张图上。

这张图想说两件事：
  1. 拼一单比各点各的便宜；
  2. 但人数到一定量之后人均会回升（券的减免额有上限，配送费早已摊薄）。

用法：
    python3 tools/make_chart.py

输出：docs/curve.svg
"""

from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from mcd_pindan.optimizer import Person, build_planner  # noqa: E402

W, H = 780, 400
PAD_L, PAD_R, PAD_T, PAD_B = 66, 112, 58, 60
Y_MIN, Y_MAX = 28.0, 50.0
MAX_PEOPLE = 8
FONT = 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'

GREEN = "#3b6d11"
GREEN_SOFT = "#eaf3de"
GRAY = "#888780"
GRID = "#e8e6e0"


def sx(i: int, n: int) -> float:
    if n <= 1:
        return PAD_L + (W - PAD_L - PAD_R) / 2
    return PAD_L + (W - PAD_L - PAD_R) * i / (n - 1)


def sy(value: float) -> float:
    ratio = (value - Y_MIN) / (Y_MAX - Y_MIN)
    return H - PAD_B - ratio * (H - PAD_T - PAD_B)


def polyline(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def text(x: float, y: float, content: str, *, anchor: str = "middle",
         size: float = 12, fill: str = "#5f5e5a", weight: str = "400") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" font-family=\'{FONT}\'>{content}</text>'
    )


def build_svg(labels: list[str], pinned: list[float], baseline: list[float]) -> str:
    n = len(labels)
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="折线图：一起拼一单时，人均实付随人数先下降，'
        f'{labels[pinned.index(min(pinned))]} 时最低，之后回升；各点各的始终不变">'
    )
    parts.append("<title>人数与人均实付</title>")
    parts.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

    # 横向网格与 Y 轴刻度
    value = 30
    while value <= 50:
        y = sy(value)
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(text(PAD_L - 10, y + 4, str(value), anchor="end", fill=GRAY))
        value += 5

    parts.append(text(PAD_L - 10, PAD_T - 20, "元/人", anchor="end", fill=GRAY))

    # X 轴刻度
    for i, label in enumerate(labels):
        parts.append(text(sx(i, n), H - PAD_B + 24, label, fill="#5f5e5a"))

    # 各点各的（灰虚线，恒定）
    base_points = [(sx(i, n), sy(v)) for i, v in enumerate(baseline)]
    parts.append(
        f'<polyline points="{polyline(base_points)}" fill="none" stroke="{GRAY}" '
        f'stroke-width="2" stroke-dasharray="6 5"/>'
    )

    # 一起拼一单（绿实线）
    pin_points = [(sx(i, n), sy(v)) for i, v in enumerate(pinned)]
    parts.append(
        f'<polygon points="{polyline(pin_points)}'
        + f' {sx(n - 1, n):.1f},{sy(baseline[-1]):.1f} {PAD_L},{sy(baseline[0]):.1f}" '
        f'fill="{GREEN_SOFT}" stroke="none"/>'
    )
    parts.append(
        f'<polyline points="{polyline(pin_points)}" fill="none" stroke="{GREEN}" '
        f'stroke-width="2.5" stroke-linejoin="round"/>'
    )

    # 数据点与数值
    for i, (value, x) in enumerate(zip(pinned, [sx(k, n) for k in range(n)])):
        y = sy(value)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="{GREEN}"/>')
        anchor = "middle" if 0 < i < n - 1 else ("start" if i == 0 else "end")
        offset = 0 if 0 < i < n - 1 else (9 if i == 0 else -9)
        parts.append(
            text(x + offset, y - 13, f"{value:.2f}", anchor=anchor,
                 size=12.5, fill="#27500a", weight="500")
        )

    # 最低点标注
    low = pinned.index(min(pinned))
    lx, ly = pin_points[low]
    parts.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="7" fill="none" stroke="{GREEN}" stroke-width="2"/>')
    low_ratio = pinned[low] / baseline[low] * 10
    parts.append(
        text(lx, ly + 28, f"最低 {pinned[low]:.2f} 元 · {low_ratio:.1f} 折",
             anchor="middle", size=12.5, fill=GREEN, weight="500")
    )

    # 拐点之后回升的说明（放在曲线下方的空白处，避免贴边）
    if low < n - 1:
        parts.append(
            text(sx(low, n) + 150, sy(min(pinned)) + 28, "→ 人再多，人均回升",
                 anchor="start", size=12, fill="#8a5a10", weight="500")
        )

    # 图例
    legend_y = PAD_T - 20
    parts.append(f'<line x1="{W - PAD_R - 132}" y1="{legend_y}" x2="{W - PAD_R - 108}" y2="{legend_y}" stroke="{GREEN}" stroke-width="2.5"/>')
    parts.append(text(W - PAD_R - 102, legend_y + 4, "一起拼一单", anchor="start"))
    parts.append(f'<line x1="{W - PAD_R - 132}" y1="{legend_y + 20}" x2="{W - PAD_R - 108}" y2="{legend_y + 20}" stroke="{GRAY}" stroke-width="2" stroke-dasharray="6 5"/>')
    parts.append(text(W - PAD_R - 102, legend_y + 24, "各点各的", anchor="start"))

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    with open(os.path.join(ROOT, "examples", "demo_input.json"), encoding="utf-8") as handle:
        payload = json.load(handle)
    planner, _ = build_planner(payload)

    # 固定同一档次餐品，只改变人数，用于单独观察「人数」这一个变量
    first = payload["people"][0]
    meal_codes = tuple(first["meals"])
    allow_swap = bool(first.get("allow_swap", True))
    people = [Person(f"成员{i + 1}", meal_codes, allow_swap) for i in range(MAX_PEOPLE)]

    labels: list[str] = []
    pinned: list[float] = []
    baseline: list[float] = []
    print(f"{'人数':>4}  {'拼一单人均':>10}  {'各点各的人均':>12}  {'折扣':>6}")
    print("-" * 44)
    for size in range(1, MAX_PEOPLE + 1):
        subset = people[:size]
        plan = planner.plan(subset, allow_swap=True)
        base = planner.baseline(subset)
        labels.append(f"{size} 人")
        pinned.append(plan.per_capita)
        baseline.append(base.per_capita)
        print(f"{size:>4}  {plan.per_capita:>10.2f}  {base.per_capita:>12.2f}  {plan.ratio_vs(base) * 10:>5.1f} 折")

    out_dir = os.path.join(ROOT, "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "curve.svg")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(build_svg(labels, pinned, baseline) + "\n")
    print(f"\n已生成 {os.path.relpath(out_path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
