#!/usr/bin/env python3
"""麦麦拼单 · MCP 联调探测器。

联调要解决的问题不是「能不能连上」，而是「真接口返回的字段名到底是什么」。
本脚本逐个调用麦当劳 MCP 工具、把原始返回落盘，并生成一份**字段探测报告**，
直接告诉你适配层（src/mcd_pindan/mcp_client.py 的 _pick 别名表）有没有猜错。

用法::

    # 0. 先确认连通与协议（不需要真实 Token 也能跑，会返回 401）
    python3 tools/probe.py --smoke

    # 1. 列出服务端可用工具
    python3 tools/probe.py --list

    # 2. 单独调一个工具，看原始返回长什么样
    python3 tools/probe.py --call query-meals

    # 3. 按标准链路逐个调用，落盘 + 生成字段报告（联调主流程）
    export MCD_MCP_TOKEN="你的 Token"
    python3 tools/probe.py --sequence --people "我:BIGMAC_SET"

输出目录（默认 data/live/，已在 .gitignore 中屏蔽）：
    00-initialize.json          握手结果（协议版本、服务端信息）
    01-tools-list.json          工具清单
    02-<tool>.json              各工具原始返回
    FIELD_REPORT.md             字段探测报告 ← 联调时看这份
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.parse
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from mcd_pindan.mcp_client import McdMcpClient, McpError, iter_records, load_dotenv  # noqa: E402

DEFAULT_OUT = os.path.join(ROOT, "data", "live")

# --------------------------------------------------------------------------
# 适配层期望的字段名（与 mcp_client.py 的 _pick() 调用保持一致）
# 联调报告会把「服务端实际返回的字段」与这份清单对比，指出需要补的别名。
# --------------------------------------------------------------------------

EXPECTED_ALIASES: dict[str, list[str]] = {
    "query-meals（餐品）": [
        "code", "mealCode", "productCode", "skuCode", "id",
        "name", "mealName", "productName", "title",
        "price", "salePrice", "currentPrice", "amount",
    ],
    "query-meal-detail（套餐可替换项）": [
        "code", "mealCode", "productCode", "packageCode", "id",
        "groups", "optionGroups", "replaceGroups", "swapOptions",
        "name", "groupName", "title",
        "choices", "options", "items", "details",
        "delta", "priceDelta", "diffPrice", "extraPrice", "itemName",
    ],
    "query-my-coupons / query-store-coupons / available-coupons（券）": [
        "code", "couponCode", "couponId", "id",
        "name", "couponName", "title", "desc",
        "threshold", "minAmount", "limitAmount", "conditionAmount",
        "discount", "discountAmount", "reduceAmount", "faceValue", "value",
    ],
}

# 明确属于个人隐私的字段，落盘前一律脱敏
SENSITIVE_KEYS = {
    "phone", "mobile", "tel", "telephone", "address", "addressdetail",
    "receivername", "contact", "email", "idcard", "token", "accesstoken",
    "openid", "unionid", "memberid", "accountid", "payurl", "qrcode",
    "deliveryaddress", "shippingaddress", "receivermobile",
}
PHONE_RE = re.compile(r"\b1[3-9]\d{9}\b")


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------


def redact(obj: Any, key: str = "") -> Any:
    """递归脱敏：命中敏感键名的值替换为 ***，手机号模式一并打码。"""
    if isinstance(obj, dict):
        return {k: redact(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v, key) for v in obj]
    if isinstance(obj, str):
        if key.lower() in SENSITIVE_KEYS:
            return "***"
        return PHONE_RE.sub("1**********", obj)
    if key.lower() in SENSITIVE_KEYS:
        return "***"
    return obj


# --------------------------------------------------------------------------
# 字段探测
# --------------------------------------------------------------------------


def collect_keys(obj: Any, prefix: str = "", out: dict[str, set[str]] | None = None) -> dict[str, set[str]]:
    """递归收集 JSON 中出现过的字段名（按路径层级归类）。"""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.setdefault(prefix, set()).add(str(key))
            child = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (dict, list)):
                collect_keys(value, child, out)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                collect_keys(item, prefix, out)
    return out


def observed_field_names(raw: Any) -> set[str]:
    """所有层级出现过的字段名，拍平成一个集合。"""
    names: set[str] = set()
    for keys in collect_keys(raw).values():
        names |= keys
    return names


def field_report(entries: list[tuple[str, str, Any]]) -> str:
    """生成字段探测报告。

    entries: [(报告分组名, 文件/工具名, 原始返回)]
    """
    lines: list[str] = ["# MCP 字段探测报告", ""]
    lines.append("由 `python3 tools/probe.py --sequence` 自动生成。")
    lines.append("")
    lines.append("用途：核对适配层 `src/mcd_pindan/mcp_client.py` 中的字段别名是否与服务端实际返回一致。")
    lines.append("")

    # 1. 各工具实际返回的字段
    lines.append("## 1. 各工具实际返回的字段")
    lines.append("")
    lines.append("| 工具 | 记录数 | 观察到的字段名 |")
    lines.append("|---|---:|---|")
    for _, name, raw in entries:
        records = iter_records(raw)
        names = sorted(observed_field_names(raw))
        shown = ", ".join(f"`{n}`" for n in names[:40]) or "（无）"
        if len(names) > 40:
            shown += f" … 共 {len(names)} 个"
        lines.append(f"| `{name}` | {len(records)} | {shown} |")
    lines.append("")

    # 2. 别名命中情况
    lines.append("## 2. 适配层别名命中情况")
    lines.append("")
    lines.append("「命中」= 服务端确实返回了该字段；「未命中」= 别名表里猜错/多余的候选，可保留亦可清理。")
    lines.append("")
    for group, aliases in EXPECTED_ALIASES.items():
        pool: set[str] = set()
        for _, _, raw in entries:
            pool |= {n.lower() for n in observed_field_names(raw)}
        hit = [a for a in aliases if a.lower() in pool]
        miss = [a for a in aliases if a.lower() not in pool]
        lines.append(f"### {group}")
        lines.append("")
        lines.append(f"- 命中（{len(hit)}）：{', '.join('`' + h + '`' for h in hit) or '（无）'}")
        lines.append(f"- 未命中（{len(miss)}）：{', '.join('`' + m + '`' for m in miss) or '（无）'}")
        if not hit:
            lines.append("")
            lines.append("> ⚠️ 该分组**没有命中任何已有别名**，说明字段名与预期差异较大，需要手工对照原始 JSON 补充别名。")
        lines.append("")

    # 3. 行动清单
    lines.append("## 3. 下一步怎么做")
    lines.append("")
    lines.append("1. 打开本目录下的 `02-*.json`，对着真实返回确认字段含义；")
    lines.append("2. 在 `src/mcd_pindan/mcp_client.py` 的 `adapt_meals` / `adapt_meal_groups` / "
                 "`adapt_coupons` 里，把你看到的字段名**加到 `_pick()` 的候选列表最前面**；")
    lines.append("3. 重新运行 `python3 tools/probe.py --sequence`，确认第 2 节变为全命中；")
    lines.append("4. 运行 `python3 pindan.py --live --people \"我:餐品code\" --delivery-fee 9` 打通完整链路。")
    lines.append("")
    lines.append("> 适配层是**唯一**需要改动的地方。最优化引擎 `optimizer.py` 与 MCP 字段无关，不需要动。")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
IPV4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def dns_diagnose(host: str) -> tuple[bool, list[str]]:
    """检查 Python 能否解析该域名；解析不了时尝试用系统工具找出真实 IP。

    返回 (是否解析成功, 要打印的行)。
    """
    lines: list[str] = []
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        lines.append(f"  Python 解析 {host} → {infos[0][4][0]}  OK")
        return True, lines
    except OSError as exc:
        lines.append(f"  Python 解析 {host} 失败：{exc}  ← 这就是连不上的根因")

    for tool, argv in (("dig", ["+short", host]), ("nslookup", [host]), ("host", [host])):
        if not shutil.which(tool):
            continue
        try:
            result = subprocess.run(argv and [tool, *argv], capture_output=True, text=True, timeout=6)
        except (OSError, subprocess.SubprocessError):
            continue
        ips = [ln.strip() for ln in (result.stdout or "").splitlines() if IPV4_RE.match(ln.strip())]
        if ips:
            lines.append(f"  但系统 {tool} 能解析 → {', '.join(dict.fromkeys(ips))}")
            lines.append(f"  → 说明是 Python 走的 DNS 通道有问题，不是网络不通")
            lines.append(f"  → 修复办法（任选其一）：")
            lines.append(f"       1) 把 hosts 记录写进去：sudo sh -c 'echo \"{ips[0]}  {host}\" >> /etc/hosts'")
            lines.append(f"       2) 换个网络（手机热点）后重试")
            lines.append(f"       3) 指定自建网关：export MCD_MCP_URL=...")
            break
    else:
        lines.append("  系统工具也无法解析，请检查网络连接或 DNS 设置")
    return False, lines


def smoke(args: argparse.Namespace) -> int:
    """连通性自检：自动判断到底要不要绕过系统代理。

    背景：Python 标准库 urllib 会自动读取 HTTP_PROXY / HTTPS_PROXY。
    开发机上常见的 Clash / Surge / 公司代理如果不放行 mcp.mcd.cn，
    请求会以 Tunnel connection failed 之类的错误失败，看起来像「服务挂了」。
    本函数会先试系统代理、再试绕过代理，直接告诉你哪个能通。
    """
    print("== 麦当劳 MCP 连通性自检 ==")
    print(f"目标地址：{os.environ.get('MCD_MCP_URL', 'https://mcp.mcd.cn')}")

    dotenv_path = load_dotenv()
    if dotenv_path:
        print(f"配置来源：.env → {dotenv_path}")
    real_token = os.environ.get("MCD_MCP_TOKEN", "")
    if real_token:
        print(f"Token：已设置（{len(real_token)} 个字符）")
    else:
        print("Token：未设置 → 本次为无鉴权探测，服务端返回 401 即视为通路正常")

    detected = {k: os.environ[k] for k in PROXY_VARS if os.environ.get(k)}
    print(f"检测到代理：{'、'.join(f'{k}={v}' for k, v in detected.items()) if detected else '无'}")

    target = os.environ.get("MCD_MCP_URL", "https://mcp.mcd.cn")
    host = urllib.parse.urlparse(target).hostname or "mcp.mcd.cn"
    print("\nDNS 检查：")
    dns_ok, dns_lines = dns_diagnose(host)
    for line in dns_lines:
        print(line)
    if not dns_ok:
        print("\n" + "=" * 58)
        print("结论：**域名解析失败，先修 DNS 再谈联调。**")
        return 2

    attempts = (
        [(True, "使用系统代理"), (False, "绕过代理直连")] if detected else [(False, "直连")]
    )

    worked: tuple[bool, str] | None = None
    for index, (use_proxy, label) in enumerate(attempts, start=1):
        print(f"\n[尝试 {index}/{len(attempts)}] {label} ...")
        try:
            client = McdMcpClient(
                token=real_token or "PROBE-NO-TOKEN",
                timeout=args.timeout,
                use_proxy=use_proxy,
            )
            result = client.initialize()
        except McpError as exc:
            message = str(exc)
            if "401" in message:
                print("  服务端返回 401（鉴权失败）→ 网络与协议通路正常 ✅")
                worked = (use_proxy, "auth")
                break
            print(f"  失败：{message}")
            continue

        print("  握手成功 ✅")
        worked = (use_proxy, "handshake")
        os.makedirs(args.out, exist_ok=True)
        dump(args.out, "00-initialize.json", result, args.no_redact)
        server = result.get("serverInfo", {}) or {}
        print(f"  协议版本：{result.get('protocolVersion', '?')}")
        print(f"  服务端：{server.get('name', '?')} {server.get('version', '')}")
        caps = result.get("capabilities", {}) or {}
        print(f"  能力：{', '.join(caps.keys()) or '（未声明）'}")
        break

    print("\n" + "=" * 58)
    if worked is None:
        print("结论：**无法连通**。排查顺序：")
        print("  ① 浏览器能否打开 https://open.mcd.cn/mcp")
        print("  ② 是否开着 VPN / 公司网络是否放行 mcp.mcd.cn")
        print("  ③ 手动指定代理：   export HTTPS_PROXY=http://127.0.0.1:7890")
        print("  ④ 指定自建网关：   export MCD_MCP_URL=...")
        return 2

    use_proxy, kind = worked
    if detected and not use_proxy:
        print("结论：网络可达，但**必须绕过系统代理**。")
        print("     请先执行：export MCD_MCP_NO_PROXY=1")
    elif detected and use_proxy:
        print("结论：网络可达，系统代理可用，无需额外设置。")
    else:
        print("结论：网络可达，直连正常。")

    if kind == "auth":
        print("\n下一步（当前还没有可用 Token）：")
        print("  ① 申请 Token：https://open.mcd.cn/mcp （手机号登录 → 控制台 → 激活）")
        print("  ② export MCD_MCP_TOKEN=\"你的 Token\"")
        print("  ③ python3 tools/probe.py --smoke    ← 应变成「握手成功」")
    else:
        print("\n下一步：python3 tools/probe.py --list")
    return 0


def parse_people(specs: list[str]) -> list[tuple[str, list[str]]]:
    people = []
    for spec in specs:
        if ":" not in spec:
            raise SystemExit(f"参与者格式应为「名称:餐品code」，收到：{spec}")
        name, codes = spec.split(":", 1)
        people.append((name.strip(), [c.strip() for c in codes.split(",") if c.strip()]))
    return people


def dump(out_dir: str, filename: str, data: Any, no_redact: bool) -> None:
    os.makedirs(out_dir, exist_ok=True)
    payload = data if no_redact else redact(data)
    with open(os.path.join(out_dir, filename), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="probe",
        description="麦当劳 MCP 联调探测器：摸清真实字段名，校准适配层",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true", help="只做握手，验证连通性与协议（无需 Token 也能跑）")
    mode.add_argument("--list", action="store_true", help="列出服务端可用工具")
    mode.add_argument("--call", metavar="TOOL", help="单独调用一个工具并打印原始返回")
    mode.add_argument("--sequence", action="store_true", help="按标准联调链路逐个调用并生成字段报告")

    parser.add_argument("--args", default="{}", help="配合 --call 使用的参数 JSON，如 '{\"storeCode\":\"XXX\"}'")
    parser.add_argument("--people", action="append", default=[], metavar="名称:餐品code", help="配合 --sequence")
    parser.add_argument("--store-code", help="门店编码")
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"落盘目录（默认 {DEFAULT_OUT}）")
    parser.add_argument("--no-redact", action="store_true", help="关闭脱敏（不建议，可能落盘个人地址等信息）")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次请求超时秒数")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # ---- 只看连通性 ----
    if args.smoke:
        return smoke(args)

    # ---- 需要 Token 的模式 ----
    try:
        client = McdMcpClient(timeout=args.timeout)
        client.initialize()
    except McpError as exc:
        print(f"连接麦当劳 MCP 失败：{exc}", file=sys.stderr)
        print("先跑 python3 tools/probe.py --smoke 定位问题。", file=sys.stderr)
        return 2

    # ---- 列出工具 ----
    if args.list:
        tools = client.list_tools()
        os.makedirs(args.out, exist_ok=True)
        dump(args.out, "01-tools-list.json", tools, args.no_redact)
        print(f"服务端共 {len(tools)} 个工具：\n")
        for tool in tools:
            schema = (tool.get("inputSchema") or {}).get("properties") or {}
            params = ", ".join(schema.keys()) or "—"
            print(f"  {tool.get('name', '?'):32s} 入参：{params}")
        print(f"\n完整 JSON（含参数 schema）已写入 {os.path.join(args.out, '01-tools-list.json')}")
        print("注意：入参名以这份 schema 为准，若与 CLI 中传参不一致，需同步修改 mcp_client.collect_input。")
        return 0

    # ---- 单独调用 ----
    if args.call:
        try:
            arguments = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"--args 不是合法 JSON：{exc}", file=sys.stderr)
            return 2
        try:
            raw = client.call_tool(args.call, arguments)
        except McpError as exc:
            print(f"调用 {args.call} 失败：{exc}", file=sys.stderr)
            return 2
        os.makedirs(args.out, exist_ok=True)
        filename = f"02-{args.call}.json"
        dump(args.out, filename, raw, args.no_redact)
        print(json.dumps(raw, ensure_ascii=False, indent=2)[:4000])
        print(f"\n原始返回已写入 {os.path.join(args.out, filename)}")
        print(f"观察到的字段：{', '.join(sorted(observed_field_names(raw))) or '（无）'}")
        return 0

    # ---- 标准链路 ----
    assert args.sequence
    people = parse_people(args.people)
    meal_codes = sorted({code for _, codes in people for code in codes})
    store_arg = {"storeCode": args.store_code} if args.store_code else {}

    plan: list[tuple[str, str, Any]] = [
        ("stores", "query-nearby-stores", dict(store_arg)),
        ("meals", "query-meals", dict(store_arg)),
    ]
    for code in meal_codes:
        plan.append(("meal_detail", "query-meal-detail", {"mealCode": code}))
    plan.extend([
        ("coupons", "auto-bind-coupons", dict(store_arg)),
        ("coupons", "query-my-coupons", dict(store_arg)),
        ("coupons", "query-store-coupons", dict(store_arg)),
        ("coupons", "available-coupons", dict(store_arg)),
        ("campaign", "campaign-calendar", {}),
    ])

    entries: list[tuple[str, str, Any]] = []
    os.makedirs(args.out, exist_ok=True)
    print(f"按标准链路探测 {len(plan)} 个工具，输出目录：{args.out}\n")

    for index, (_, tool, arguments) in enumerate(plan, start=2):
        label = f"{index:02d}-{tool}"
        if tool == "query-meal-detail":
            label += f"-{arguments.get('mealCode')}"
        try:
            raw = client.call_tool(tool, arguments)
        except McpError as exc:
            print(f"  [FAIL] {tool:28s} {exc}")
            continue
        dump(args.out, f"{label}.json", raw, args.no_redact)
        records = iter_records(raw)
        print(f"  [ OK ] {tool:28s} 记录数={len(records):<4} 字段={len(observed_field_names(raw))}")
        entries.append((tool, tool if tool != "query-meal-detail" else label, raw))

    report_path = os.path.join(args.out, "FIELD_REPORT.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(field_report(entries))

    print(f"\n字段探测报告：{report_path}")
    print("打开它对照第 2 节，把未命中的真实字段名补进 mcp_client.py 的 _pick() 别名表。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
