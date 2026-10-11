"""麦麦拼单命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .mcp_client import McdMcpClient, McpError, collect_input
from .optimizer import build_planner, render_markdown


def parse_people(specs: Sequence[str]) -> list[tuple[str, list[str]]]:
    """解析 --people "名称:餐品code,餐品code" 形式的参数。"""
    people: list[tuple[str, list[str]]] = []
    for spec in specs:
        if ":" not in spec:
            raise SystemExit(
                f"参与者格式应为「名称:餐品code,餐品code」，收到：{spec}"
            )
        name, codes = spec.split(":", 1)
        meal_codes = [c.strip() for c in codes.split(",") if c.strip()]
        if not name.strip() or not meal_codes:
            raise SystemExit(f"参与者信息不完整：{spec}")
        people.append((name.strip(), meal_codes))
    return people


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcd-pindan",
        description="麦麦拼单 · 在真实规则内试算人均实付最低的拼单组合",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  离线复现：python3 pindan.py --input examples/demo_input.json\n"
            "  实时抓取：export MCD_MCP_TOKEN=xxx && python3 pindan.py --live \\\n"
            "              --people \"我:BIGMAC_SET\" --people \"小王:MCCHICKEN_SET\" --delivery-fee 9\n"
            "  查看工具：python3 pindan.py --list-tools\n"
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", metavar="FILE", help="输入 JSON 文件（离线模式，便于评审复现）")
    source.add_argument("--live", action="store_true", help="通过麦当劳 MCP 抓取实时数据")
    source.add_argument("--list-tools", action="store_true", help="列出麦当劳 MCP 可用工具后退出")

    parser.add_argument(
        "--people", action="append", default=[], metavar="名称:餐品code,餐品code",
        help="参与者（可重复指定）",
    )
    parser.add_argument("--store-code", help="门店编码，用于定位菜单与门店券")
    parser.add_argument("--delivery-fee", type=float, default=0.0, help="配送费（外送场景）")
    parser.add_argument("--no-swap", action="store_true", help="不考虑替换套餐内商品")
    parser.add_argument("--dump-input", metavar="FILE", help="把抓取到的输入保存为 JSON")
    parser.add_argument("-o", "--output", metavar="FILE", help="把 Markdown 报告写入文件")
    parser.add_argument("-q", "--quiet", action="store_true", help="不打印抓取过程")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # ---- 仅列出工具 ----
    if args.list_tools:
        try:
            client = McdMcpClient()
            client.initialize()
            tools = client.list_tools()
        except McpError as exc:
            print(f"连接麦当劳 MCP 失败：{exc}", file=sys.stderr)
            return 2
        print(f"麦当劳 MCP 共 {len(tools)} 个工具：\n")
        for tool in tools:
            print(f"  {tool.get('name', '?'):34s} {tool.get('description', '')}")
        return 0

    # ---- 组装输入 ----
    if args.input:
        try:
            with open(args.input, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"读取输入文件失败：{exc}", file=sys.stderr)
            return 2
    else:
        if not args.people:
            print("--live 模式需要通过 --people 指定参与者。", file=sys.stderr)
            return 2
        people_spec = parse_people(args.people)
        try:
            client = McdMcpClient()
            payload = collect_input(
                client,
                people_spec=people_spec,
                store_code=args.store_code,
                delivery_fee=args.delivery_fee,
                verbose=not args.quiet,
            )
        except McpError as exc:
            print(f"抓取麦当劳 MCP 数据失败：{exc}", file=sys.stderr)
            return 2

    if args.dump_input:
        with open(args.dump_input, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"输入数据已保存到 {args.dump_input}", file=sys.stderr)

    # ---- 求解 ----
    try:
        planner, people = build_planner(payload)
    except (KeyError, ValueError) as exc:
        print(f"输入数据不完整：{exc}", file=sys.stderr)
        return 2

    if not people:
        print("输入数据中没有参与者（people 为空）。", file=sys.stderr)
        return 2

    allow_swap = not args.no_swap and not payload.get("no_swap", False)
    best = planner.plan(people, allow_swap=allow_swap)
    baseline = planner.baseline(people)
    curve = planner.curve(people, allow_swap=allow_swap)

    report = render_markdown(
        planner,
        people,
        best,
        baseline=baseline,
        curve=curve,
    )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(report + "\n")
        print(f"报告已写入 {args.output}", file=sys.stderr)
    else:
        print(report)

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
