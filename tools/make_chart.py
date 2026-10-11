#!/usr/bin/env python3
"""根据示例数据生成 README 用的折线图（纯标准库输出 SVG，GitHub 可直接渲染）。

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

from mcd_pindan.optimizer import build_planner  # noqa: E402

W, H = 760, 380
PAD_L, PAD_R, PAD_T, PAD_B = 68, 96, 48, 52
Y_MIN, Y_MAX = 24.0, 50.0
FONT = 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif'


def sx(i: int, n: int) -> float:
    if n <= 1:
        return PAD_L + (W - PAD_L - PAD_R) / 2
    return PAD_L + (W - PAD_L - PAD_R) * i / (n - 1)


def sy(value: float) -> float:
    ratio = (value - Y_MIN) / (Y_MAX - Y_MIN)
    return H - PAD_B - ratio * (H - PAD_T - PAD_B)


def polyline(points: list[tuple[float, float]]) -> str:
    return " ".join(f"{x:.1f},{y:.1f}" for x, y in points)


def build_svg(labels: list[str], pinned: list[float], baseline: list[float]) -> str:
    n = len(labels)
    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" '
        f'aria-label="折线图：拼单人数增加时，拼单人均实付下降，各点各的人均实付保持不变">'
    )
    parts.append("<title>人数与人均实付</title>")
    parts.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

    # 横向网格与 Y 轴刻度
    value = 25
    while value <= 50:
        y = sy(value)
        parts.append(
            f'<line x1="{PAD_L}" y1="{y:.1f}" x2="{W - PAD_R}" y2="{y:.1f}" '
            f'stroke="#e8e6e0" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{PAD_L - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="12" '
            f'fill="#888780" font-family=\'{FONT}\'>{value}</text>'
        )
        value += 5

    # 轴标题
    parts.append(
        f'<text x="{PAD_L - 10}" y="{PAD_T - 18}" text-anchor="end" font-size="12" '
        f'fill="#888780" font-family=\'{FONT}\'>元/人</text>'
    )

    # X 轴刻度
    for i, label in enumerate(labels):
        x = sx(i, n)
        parts.append(
            f'<text x="{x:.1f}" y="{H - PAD_B + 24}" text-anchor="middle" font-size="12" '
            f'fill="#5f5e5a" font-family=\'{FONT}\'>{label}</text>'
        )

    # 各点各的（灰虚线，恒定）
    base_points = [(sx(i, n), sy(v)) for i, v in enumerate(baseline)]
    parts.append(
        f'<polyline points="{polyline(base_points)}" fill="none" stroke="#888780" '
        f'stroke-width="2" stroke-dasharray="6 5"/>'
    )

    # 拼单一单（绿实线，下降）
    pin_points = [(sx(i, n), sy(v)) for i, v in enumerate(pinned)]
    parts.append(
        f'<polygon points="{PAD_L},{sy(pinned[0]):.1f} '
        + polyline(pin_points)
        + f' {sx(n - 1, n):.1f},{sy(baseline[-1]):.1f} {PAD_L},{sy(baseline[0]):.1f}" '
        f'fill="#eaf3de" stroke="none"/>'
    )
    parts.append(
        f'<polyline points="{polyline(pin_points)}" fill="none" stroke="#3b6d11" '
        f'stroke-width="2.5" stroke-linejoin="round"/>'
    )

    # 数据点与数值
    for i, (value, x) in enumerate(zip(pinned, [sx(k, n) for k in range(n)])):
        y = sy(value)
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4.5" fill="#3b6d11"/>')
        anchor = "middle" if 0 < i < n - 1 else ("start" if i == 0 else "end")
        offset = 0 if 0 < i < n - 1 else (8 if i == 0 else -8)
        parts.append(
            f'<text x="{x + offset:.1f}" y="{y - 12:.1f}" text-anchor="{anchor}" font-size="12.5" '
            f'font-weight="500" fill="#27500a" font-family=\'{FONT}\'>{value:.2f}</text>'
        )

    # 末点折扣标注
    last_x, last_y = pin_points[-1]
    parts.append(
        f'<text x="{last_x + 12:.1f}" y="{last_y + 22:.1f}" font-size="13" font-weight="500" '
        f'fill="#3b6d11" font-family=\'{FONT}\'>6.7 折</text>'
    )

    # 图例
    legend_y = PAD_T - 18
    parts.append(
        f'<line x1="{W - PAD_R - 120}" y1="{legend_y}" x2="{W - PAD_R - 96}" y2="{legend_y}" '
        f'stroke="#3b6d11" stroke-width="2.5"/>'
    )
    parts.append(
        f'<text x="{W - PAD_R - 90}" y="{legend_y + 4}" font-size="12" fill="#5f5e5a" '
        f'font-family=\'{FONT}\'>一起拼一单</text>'
    )
    parts.append(
        f'<line x1="{W - PAD_R - 120}" y1="{legend_y + 20}" x2="{W - PAD_R - 96}" y2="{legend_y + 20}" '
        f'stroke="#888780" stroke-width="2" stroke-dasharray="6 5"/>'
    )
    parts.append(
        f'<text x="{W - PAD_R - 90}" y="{legend_y + 24}" font-size="12" fill="#5f5e5a" '
        f'font-family=\'{FONT}\'>各点各的</text>'
    )

    parts.append("</svg>")
    return "\n".join(parts)


def main() -> int:
    with open(os.path.join(ROOT, "examples", "demo_input.json"), encoding="utf-8") as handle:
        payload = json.load(handle)
    planner, people = build_planner(payload)

    labels: list[str] = []
    pinned: list[float] = []
    baseline: list[float] = []
    for k, plan in planner.curve(people):
        labels.append(f"{k} 人")
        pinned.append(plan.per_capita)
        baseline.append(planner.baseline(list(people[:k])).per_capita)

    out_dir = os.path.join(ROOT, "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "curve.svg")
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(build_svg(labels, pinned, baseline) + "\n")
    print(f"已生成 {os.path.relpath(out_path, ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
