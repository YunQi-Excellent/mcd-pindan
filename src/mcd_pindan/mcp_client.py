"""麦当劳 MCP 客户端（Streamable HTTP + JSON-RPC 2.0）。

只依赖 Python 标准库，便于评审直接运行。

用法::

    export MCD_MCP_TOKEN="你的 Token"
    python3 pindan.py --list-tools
    python3 pindan.py --live --people "我:BIGMAC_SET" --people "小王:MCCHICKEN_SET"

安全说明：Token 只从环境变量读取，不会写入任何文件，也不会出现在输出中。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Iterable

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "mcd-pindan"
CLIENT_VERSION = "0.1.0"


class McpError(RuntimeError):
    """MCP 调用失败。"""


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


DOTENV_NAME = ".env"


def load_dotenv(path: str | None = None) -> str | None:
    """从 .env 文件读取配置，**只填充当前环境里还没有的变量**。

    查找顺序：显式指定的 path → 当前工作目录 → 项目根目录。

    已有环境变量永不被覆盖，因此优先级是：
        命令行前缀 / export  >  .env 文件

    返回实际读取到的文件路径；没找到返回 None。
    """
    if path:
        candidates = [path]
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidates = [
            os.path.join(os.getcwd(), DOTENV_NAME),
            os.path.join(project_root, DOTENV_NAME),
        ]

    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        try:
            with open(candidate, "r", encoding="utf-8") as handle:
                for raw in handle:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            continue
        return candidate
    return None


class McdMcpClient:
    """一个极简的 MCP Streamable HTTP 客户端。

    use_proxy:
        - None（默认）：读环境变量 MCD_MCP_NO_PROXY 决定，未设置则走系统代理；
        - False：绕过 HTTP_PROXY / HTTPS_PROXY 直连。

    为什么需要这个开关：Python 标准库 urllib 会**自动读取** HTTP_PROXY / HTTPS_PROXY
    环境变量。不少开发机（Clash、Surge、公司代理）会设置这两个变量，一旦该代理
    不放行 mcp.mcd.cn，请求就会以 ``Tunnel connection failed: 502`` 之类的错误失败，
    看起来像是「服务不可用」，实际只需要绕过代理。先跑 ``tools/probe.py --smoke``
    可以自动判断到底要不要绕过。
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        timeout: float = 30.0,
        use_proxy: bool | None = None,
    ) -> None:
        self.dotenv = load_dotenv()
        self.url = url or os.environ.get("MCD_MCP_URL", DEFAULT_URL)
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        if not self.token:
            raise McpError(
                "未找到 MCP Token。三种提供方式（任选其一）：\n"
                "  1) 当前终端临时设置：export MCD_MCP_TOKEN=\"你的 Token\"\n"
                "  2) 单次命令前缀（最安全，不留在 shell 历史之外的地方）：\n"
                "     MCD_MCP_TOKEN=\"你的 Token\" python3 tools/probe.py --smoke\n"
                "  3) 项目根目录建 .env 文件，写入一行 MCD_MCP_TOKEN=你的 Token\n"
                "     可参考仓库里的 .env.example（.env 已被 .gitignore 屏蔽）\n"
                "申请 Token：https://open.mcd.cn/mcp"
            )
        self.timeout = timeout
        self.use_proxy = (not _truthy(os.environ.get("MCD_MCP_NO_PROXY"))) if use_proxy is None else use_proxy
        # ProxyHandler({}) 为空表示不使用任何代理
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}) if not self.use_proxy else urllib.request.ProxyHandler()
        )
        self._session_id: str | None = None
        self._next_id = 1

    # ---------------- 传输 ----------------

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=data, headers=self._headers(), method="POST"
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:  # 401 / 429 等
            body = exc.read().decode("utf-8", "replace")
            hint = {
                401: "Token 无效、已过期或未提供，请检查 MCD_MCP_TOKEN。",
                429: "触发限流（每分钟 600 次），请降低请求频率。",
            }.get(exc.code, "")
            raise McpError(f"HTTP {exc.code} {hint} 原始响应：{body[:400]}") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if self.use_proxy and ("Tunnel" in reason or "proxy" in reason.lower()):
                raise McpError(
                    f"网络错误：{reason}　→　这通常说明系统代理不放行 mcp.mcd.cn。"
                    "请设置环境变量 MCD_MCP_NO_PROXY=1 后重试。"
                ) from exc
            raise McpError(f"网络错误：{reason}") from exc

        if not raw.strip():
            return None
        return self._parse_body(raw)

    @staticmethod
    def _parse_body(raw: str) -> Any:
        """响应可能是纯 JSON，也可能是 SSE（text/event-stream）。"""
        text = raw.strip()
        if text.startswith("{") or text.startswith("["):
            return json.loads(text)
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue
        raise McpError(f"无法解析响应：{text[:400]}")

    def _rpc(self, method: str, params: Any = None, notify: bool = False) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        if not notify:
            payload["id"] = self._next_id
            self._next_id += 1

        obj = self._post(payload)
        if notify:
            return None
        if obj is None:
            raise McpError(f"{method} 返回空响应")
        if isinstance(obj, dict) and "error" in obj:
            err = obj["error"] or {}
            raise McpError(f"{method} 失败 [{err.get('code')}] {err.get('message')}")
        return obj.get("result") if isinstance(obj, dict) else obj

    # ---------------- MCP 方法 ----------------

    def initialize(self) -> Any:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        self._rpc("notifications/initialized", {}, notify=True)
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._rpc("tools/list") or {}
        return result.get("tools", []) if isinstance(result, dict) else []

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result is None:
            return None
        if isinstance(result, dict) and result.get("isError"):
            raise McpError(f"工具 {name} 返回错误：{_extract_text(result)[:400]}")
        return _loads_maybe(_extract_text(result))


def _extract_text(result: dict[str, Any]) -> str:
    parts = []
    for item in result.get("content", []) or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(item.get("text", ""))
    return "\n".join(parts)


def _loads_maybe(text: str) -> Any:
    text = (text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


# --------------------------------------------------------------------------
# 响应适配层
#
# 说明：MCP 各工具的返回字段名以官方实时返回为准。这里把所有字段映射集中
# 放在本层，一旦某个工具的实际字段有差异，只需修改下面的 _pick_* 别名表，
# 不需要改动最优化引擎。
# --------------------------------------------------------------------------

_RECORD_KEYS = ("data", "result", "list", "items", "records", "rows", "meals", "products")


def iter_records(raw: Any) -> list[dict[str, Any]]:
    """把 MCP 返回的嵌套结构拍平成记录列表。"""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, dict)]
    if isinstance(raw, dict):
        for key in _RECORD_KEYS:
            value = raw.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
            if isinstance(value, dict):
                nested = iter_records(value)
                if nested:
                    return nested
        return [raw]
    return []


def _pick(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in record and record[name] not in (None, ""):
            return record[name]
    return default


def _to_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace("¥", "").replace("元", "").replace(",", "")
        try:
            return float(cleaned)
        except ValueError:
            return default
    return default


def adapt_meals(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把 query-meals 的返回适配成引擎的 meals 结构。"""
    meals: list[dict[str, Any]] = []
    for record in records:
        code = _pick(record, "code", "mealCode", "productCode", "skuCode", "id")
        name = _pick(record, "name", "mealName", "productName", "title")
        if code is None or name is None:
            continue
        price = _to_float(_pick(record, "price", "salePrice", "currentPrice", "amount", default=0))
        meals.append({"code": str(code), "name": str(name), "price": price, "groups": []})
    return meals


def adapt_meal_groups(detail_records: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """把 query-meal-detail 的返回适配成 {meal_code: groups}。"""
    mapping: dict[str, list[dict[str, Any]]] = {}
    for record in detail_records:
        code = _pick(record, "code", "mealCode", "productCode", "packageCode", "id")
        if code is None:
            continue
        raw_groups = _pick(record, "groups", "optionGroups", "replaceGroups", "swapOptions", default=[])
        groups: list[dict[str, Any]] = []
        if isinstance(raw_groups, list):
            for raw_group in raw_groups:
                if not isinstance(raw_group, dict):
                    continue
                group_name = _pick(raw_group, "name", "groupName", "title", default="可选")
                raw_choices = _pick(raw_group, "choices", "options", "items", "details", default=[])
                choices = []
                for raw_choice in raw_choices or []:
                    if not isinstance(raw_choice, dict):
                        continue
                    choices.append(
                        {
                            "name": str(_pick(raw_choice, "name", "itemName", "title", default="")),
                            "delta": _to_float(
                                _pick(raw_choice, "delta", "priceDelta", "diffPrice", "extraPrice", default=0)
                            ),
                        }
                    )
                if choices:
                    groups.append({"name": str(group_name), "choices": choices})
        if groups:
            mapping[str(code)] = groups
    return mapping


def adapt_coupons(records: Iterable[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """把各类券工具的返回适配成引擎的 coupons 结构。"""
    coupons: list[dict[str, Any]] = []
    for record in records:
        code = _pick(record, "code", "couponCode", "couponId", "id")
        name = _pick(record, "name", "couponName", "title", "desc")
        if code is None:
            continue
        threshold = _to_float(
            _pick(record, "threshold", "minAmount", "limitAmount", "conditionAmount", default=0)
        )
        discount = _to_float(
            _pick(record, "discount", "discountAmount", "reduceAmount", "faceValue", "value", default=0)
        )
        if discount <= 0:
            continue
        coupons.append(
            {
                "code": str(code),
                "name": str(name or f"满{threshold:.0f}减{discount:.0f}"),
                "threshold": threshold,
                "discount": discount,
                "source": source,
            }
        )
    return coupons


def collect_input(
    client: McdMcpClient,
    people_spec: list[tuple[str, list[str]]],
    store_code: str | None = None,
    delivery_fee: float = 0.0,
    verbose: bool = False,
) -> dict[str, Any]:
    """通过 MCP 抓取真实数据，组装成引擎输入。

    people_spec: [(参与者名称, [餐品 code, ...]), ...]
    """
    def log(message: str) -> None:
        if verbose:
            print(f"[mcp] {message}")

    log("initialize")
    client.initialize()

    log("query-meals")
    menu_raw = client.call_tool("query-meals", {"storeCode": store_code} if store_code else {})
    meals = adapt_meals(iter_records(menu_raw))
    log(f"拉到 {len(meals)} 个餐品")

    # 逐个查询需要换配置的套餐详情
    groups_by_meal: dict[str, list[dict[str, Any]]] = {}
    wanted = {code for _, codes in people_spec for code in codes}
    for code in sorted(wanted):
        try:
            log(f"query-meal-detail {code}")
            detail_raw = client.call_tool("query-meal-detail", {"mealCode": code})
            groups_by_meal.update(adapt_meal_groups(iter_records(detail_raw)))
        except McpError as exc:
            log(f"餐品 {code} 详情获取失败，跳过换配置：{exc}")

    for meal in meals:
        if meal["code"] in groups_by_meal:
            meal["groups"] = groups_by_meal[meal["code"]]

    coupons: list[dict[str, Any]] = []
    for tool, source in (
        ("query-my-coupons", "my"),
        ("query-store-coupons", "store"),
        ("available-coupons", "maimaisheng"),
    ):
        try:
            log(tool)
            raw = client.call_tool(tool, {"storeCode": store_code} if store_code else {})
            found = adapt_coupons(iter_records(raw), source)
            log(f"{tool} → {len(found)} 张券")
            coupons.extend(found)
        except McpError as exc:
            log(f"{tool} 获取失败，跳过：{exc}")

    return {
        "store": store_code or "",
        "store_code": store_code or "",
        "delivery_fee": delivery_fee,
        "baseline_coupon_mode": "shared",
        "meals": meals,
        "coupons": coupons,
        "people": [
            {"name": name, "meals": codes, "allow_swap": True} for name, codes in people_spec
        ],
    }
