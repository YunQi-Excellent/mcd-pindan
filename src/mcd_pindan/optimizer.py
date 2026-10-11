"""麦麦拼单 (mcd-pindan) —— 凑单最优化引擎。

设计原则（重要，也是本项目的合规底线）：

1. 本引擎**不发明任何价格或折扣**。所有餐品单价、券门槛与券面额
   都来自麦当劳 MCP 的实时返回（query-meals / query-meal-detail /
   query-my-coupons / query-store-coupons / available-coupons）。
2. 引擎只做一件事：在**真实可用规则**内，穷举“怎么点”，
   找出人均实付最低的那个组合。
3. “人越多越便宜”来自三个真实杠杆，而非虚构折扣：
   - 配送费按单收取，多人拼单时人均分摊额下降；
   - 满减券有门槛，单人小单够不着，拼单能凑到；
   - 套餐内商品可替换（query-meal-detail），把不需要的贵单品换掉。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "Choice",
    "OptionGroup",
    "Meal",
    "Coupon",
    "Person",
    "Menu",
    "Variant",
    "PersonPick",
    "Plan",
    "Planner",
    "build_planner",
    "render_markdown",
]

# 单人候选配置上限 / 总搜索空间上限，防止组合爆炸
MAX_VARIANTS_PER_PERSON = 6
MAX_COMBINATIONS = 120_000
EPS = 1e-9


# --------------------------------------------------------------------------
# 数据模型
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Choice:
    """套餐内某个可选商品的选项，delta 为相对套餐基础价的差价。"""

    name: str
    delta: float = 0.0


@dataclass(frozen=True)
class OptionGroup:
    """套餐内的一个可替换维度，例如“饮品”“副食”。"""

    name: str
    choices: tuple[Choice, ...]

    @property
    def default(self) -> Choice:
        """默认选项：取差价为 0 的项，找不到则取第一项。"""
        for c in self.choices:
            if abs(c.delta) < EPS:
                return c
        return self.choices[0]


@dataclass(frozen=True)
class Meal:
    """单个餐品或套餐。groups 为空表示不可替换配置的单品。"""

    code: str
    name: str
    price: float
    groups: tuple[OptionGroup, ...] = ()

    @property
    def is_bundle(self) -> bool:
        return bool(self.groups)


@dataclass(frozen=True)
class Coupon:
    """一张优惠券。threshold 为门槛金额，discount 为减免金额。"""

    code: str
    name: str
    threshold: float
    discount: float
    source: str = "my"  # my | store | maimaisheng | campaign

    def applies_to(self, subtotal: float) -> bool:
        return subtotal + EPS >= self.threshold

    @property
    def source_label(self) -> str:
        return {
            "my": "我的券",
            "store": "门店券",
            "maimaisheng": "麦麦省",
            "campaign": "当期活动",
        }.get(self.source, self.source)


@dataclass
class Person:
    """一位拼单参与者。meals 为餐品 code 列表（第一项通常为主餐）。"""

    name: str
    meals: tuple[str, ...]
    allow_swap: bool = True  # 是否接受替换套餐内商品


@dataclass(frozen=True)
class Variant:
    """某位参与者的一个候选配置。"""

    delta: float
    label: str = ""

    @property
    def is_default(self) -> bool:
        return abs(self.delta) < EPS


class Menu:
    """餐品字典，带门店信息。"""

    def __init__(self, meals: Iterable[Meal], store: str = "", store_code: str = "") -> None:
        self._meals: dict[str, Meal] = {m.code: m for m in meals}
        self.store = store
        self.store_code = store_code

    def get(self, code: str) -> Meal:
        try:
            return self._meals[code]
        except KeyError as exc:  # pragma: no cover - 数据错误时给出清晰提示
            raise KeyError(f"菜单中不存在餐品 code: {code}") from exc

    def __contains__(self, code: str) -> bool:
        return code in self._meals

    def __iter__(self):
        return iter(self._meals.values())

    @property
    def meals(self) -> tuple[Meal, ...]:
        return tuple(self._meals.values())


# --------------------------------------------------------------------------
# 结果模型
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PersonPick:
    """单人分摊结果。"""

    name: str
    items: tuple[str, ...]
    config_label: str
    items_subtotal: float
    due: float


@dataclass
class Plan:
    """一个完整的拼单方案。"""

    people: tuple[Person, ...]
    picks: tuple[PersonPick, ...]
    subtotal: float
    delivery_fee: float
    coupon: Coupon | None
    total: float
    per_capita: float

    @property
    def discount_total(self) -> float:
        return self.coupon.discount if self.coupon else 0.0

    def ratio_vs(self, baseline: "Plan") -> float:
        """相对基准方案的价格比例，0.75 即约 7.5 折。"""
        if baseline.per_capita <= EPS:
            return 1.0
        return self.per_capita / baseline.per_capita


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------


class Planner:
    """凑单最优化引擎。

    baseline_coupon_mode:
        - "shared" : 基准方案（各点各的）也使用同一券池，对基准更有利，
                     因此本项目声称的省额是**保守**的。默认值。
        - "none"   : 基准方案不使用任何券。
    """

    def __init__(
        self,
        menu: Menu,
        coupons: Sequence[Coupon] = (),
        delivery_fee: float = 0.0,
        baseline_coupon_mode: str = "shared",
        max_coupons_per_order: int = 1,
    ) -> None:
        self.menu = menu
        self.coupons: tuple[Coupon, ...] = tuple(coupons)
        self.delivery_fee = float(delivery_fee)
        self.baseline_coupon_mode = baseline_coupon_mode
        self.max_coupons_per_order = max_coupons_per_order

    # ---------------- 券 ----------------

    def best_coupon(self, subtotal: float) -> Coupon | None:
        """在门槛满足的券中取减免额最大的一张。

        简化假设：同一订单只使用一张券（max_coupons_per_order = 1）。
        真实叠加规则以麦当劳 MCP 返回的券面说明为准。
        """
        usable = [c for c in self.coupons if c.applies_to(subtotal)]
        if not usable:
            return None
        return max(usable, key=lambda c: (c.discount, -c.threshold))

    def settle(self, subtotal: float, use_coupon: bool = True) -> tuple[float, Coupon | None]:
        """结算：小计 + 配送费 - 券。返回 (应付总额, 使用的券)。"""
        total = subtotal + self.delivery_fee
        coupon = self.best_coupon(subtotal) if use_coupon else None
        if coupon:
            total -= coupon.discount
        return round(total, 2), coupon

    # ---------------- 候选配置 ----------------

    def _variants_for(self, person: Person, allow_swap: bool) -> list[Variant]:
        """列出该参与者的候选配置，按价格升序、去重、截断。"""
        if not (allow_swap and person.allow_swap):
            return [Variant(0.0, "")]

        group_lists: list[tuple[Meal, OptionGroup]] = []
        for code in person.meals:
            meal = self.menu.get(code)
            for group in meal.groups:
                group_lists.append((meal, group))

        if not group_lists:
            return [Variant(0.0, "")]

        choices_per_group = [g.choices for _, g in group_lists]
        merged: dict[float, Variant] = {}
        for combo in itertools.product(*choices_per_group):
            delta = round(sum(c.delta for c in combo), 2)
            changes: list[str] = []
            for (_, group), choice in zip(group_lists, combo):
                default = group.default
                if choice.name != default.name:
                    changes.append(f"{group.name} {default.name}→{choice.name}")
            label = "；".join(changes)
            prev = merged.get(delta)
            if prev is None or len(label) < len(prev.label):
                merged[delta] = Variant(delta, label)

        variants = sorted(merged.values(), key=lambda v: (v.delta, len(v.label)))
        return variants[:MAX_VARIANTS_PER_PERSON]

    def _plan_for(self, people: Sequence[Person], allow_swap: bool) -> Plan:
        """对给定参与者集合求人均实付最低的方案。"""
        n = len(people)
        if n == 0:
            raise ValueError("参与者不能为空")

        variant_lists = [self._variants_for(p, allow_swap) for p in people]

        # 组合爆炸保护：按人均摊系数收缩每人的候选数
        budget = max(1, int(MAX_COMBINATIONS ** (1.0 / n)))
        variant_lists = [v[: max(1, min(len(v), budget))] for v in variant_lists]

        base_subtotal = round(
            sum(self.menu.get(code).price for p in people for code in p.meals), 2
        )

        best: Plan | None = None
        for combo in itertools.product(*variant_lists):
            subtotal = round(base_subtotal + sum(v.delta for v in combo), 2)
            total, coupon = self.settle(subtotal)
            per_capita = total / n

            if best is not None and not self._is_better(per_capita, coupon, subtotal, best):
                continue

            picks = self._allocate(people, combo, subtotal, total)
            best = Plan(
                people=tuple(people),
                picks=picks,
                subtotal=subtotal,
                delivery_fee=self.delivery_fee,
                coupon=coupon,
                total=total,
                per_capita=round(per_capita, 2),
            )

        assert best is not None
        return best

    @staticmethod
    def _is_better(
        per_capita: float, coupon: Coupon | None, subtotal: float, best: Plan
    ) -> bool:
        """排序规则：人均实付最低 → 券更优 → 餐品小计更低（不打折凑单）。"""
        if per_capita < best.per_capita - EPS:
            return True
        if abs(per_capita - best.per_capita) > EPS:
            return False
        new_discount = coupon.discount if coupon else 0.0
        if new_discount > best.discount_total + EPS:
            return True
        if abs(new_discount - best.discount_total) > EPS:
            return False
        return subtotal < best.subtotal - EPS

    def _allocate(
        self,
        people: Sequence[Person],
        combo: Sequence[Variant],
        subtotal: float,
        total: float,
    ) -> tuple[PersonPick, ...]:
        """按各自餐品小计占比分摊总价，尾差落在分摊额最大的人身上。"""
        items_subtotals = [
            round(sum(self.menu.get(code).price for code in p.meals) + v.delta, 2)
            for p, v in zip(people, combo)
        ]
        denom = round(sum(items_subtotals), 2) or 1.0

        dues = [round(total * s / denom, 2) for s in items_subtotals]
        residual = round(total - sum(dues), 2)
        if abs(residual) >= 0.01:
            idx = max(range(len(dues)), key=lambda i: dues[i])
            dues[idx] = round(dues[idx] + residual, 2)

        picks = []
        for person, variant, items_subtotal, due in zip(people, combo, items_subtotals, dues):
            picks.append(
                PersonPick(
                    name=person.name,
                    items=tuple(self.menu.get(code).name for code in person.meals),
                    config_label=variant.label,
                    items_subtotal=items_subtotal,
                    due=due,
                )
            )
        return tuple(picks)

    # ---------------- 对外入口 ----------------

    def baseline(self, people: Sequence[Person]) -> Plan:
        """基准方案：每人单独下一单，各自承担一次配送费、各自用券。"""
        use_coupon = self.baseline_coupon_mode != "none"
        picks: list[PersonPick] = []
        subtotal = 0.0
        total = 0.0

        for person in people:
            items_subtotal = round(sum(self.menu.get(c).price for c in person.meals), 2)
            person_total, _ = self.settle(items_subtotal, use_coupon=use_coupon)
            subtotal += items_subtotal
            total += person_total
            picks.append(
                PersonPick(
                    name=person.name,
                    items=tuple(self.menu.get(c).name for c in person.meals),
                    config_label="",
                    items_subtotal=items_subtotal,
                    due=person_total,
                )
            )

        total = round(total, 2)
        subtotal = round(subtotal, 2)
        return Plan(
            people=tuple(people),
            picks=tuple(picks),
            subtotal=subtotal,
            delivery_fee=round(self.delivery_fee * len(people), 2),
            coupon=None,
            total=total,
            per_capita=round(total / len(people), 2),
        )

    def plan(self, people: Sequence[Person], allow_swap: bool = True) -> Plan:
        """推荐方案：所有人拼成一单，由发起人下单，其他人分摊。"""
        return self._plan_for(people, allow_swap)

    def curve(self, people: Sequence[Person], allow_swap: bool = True) -> list[tuple[int, Plan]]:
        """人数—人均实付曲线：取前 k 个人分别求解，用于展示“人越多越便宜”。"""
        out: list[tuple[int, Plan]] = []
        for k in range(1, len(people) + 1):
            subset = list(people[:k])
            out.append((k, self._plan_for(subset, allow_swap)))
        return out


# --------------------------------------------------------------------------
# 便捷构造
# --------------------------------------------------------------------------


def build_planner(payload: dict) -> tuple[Planner, list[Person]]:
    """从（MCP 抓取或示例的）字典构造引擎与参与者列表。"""
    meals = []
    for raw in payload.get("meals", []):
        groups = tuple(
            OptionGroup(
                name=g["name"],
                choices=tuple(Choice(name=c["name"], delta=float(c.get("delta", 0))) for c in g["choices"]),
            )
            for g in raw.get("groups", [])
        )
        meals.append(Meal(code=raw["code"], name=raw["name"], price=float(raw["price"]), groups=groups))

    menu = Menu(
        meals,
        store=payload.get("store", ""),
        store_code=payload.get("store_code", ""),
    )
    coupons = [
        Coupon(
            code=c["code"],
            name=c["name"],
            threshold=float(c.get("threshold", 0)),
            discount=float(c.get("discount", 0)),
            source=c.get("source", "my"),
        )
        for c in payload.get("coupons", [])
    ]
    planner = Planner(
        menu=menu,
        coupons=coupons,
        delivery_fee=float(payload.get("delivery_fee", 0)),
        baseline_coupon_mode=payload.get("baseline_coupon_mode", "shared"),
    )
    people = [
        Person(
            name=p["name"],
            meals=tuple(p["meals"]),
            allow_swap=bool(p.get("allow_swap", True)),
        )
        for p in payload.get("people", [])
    ]
    return planner, people


# --------------------------------------------------------------------------
# 报告
# --------------------------------------------------------------------------


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _zhe(ratio: float) -> str:
    """把价格比例写成中文“折”的说法，0.67 → 6.7 折。"""
    return f"{ratio * 10:.1f} 折"


def render_markdown(
    planner: Planner,
    people: Sequence[Person],
    best: Plan,
    baseline: Plan | None = None,
    curve: Sequence[tuple[int, Plan]] | None = None,
    title: str = "麦麦拼单方案",
) -> str:
    """把方案渲染成可直接贴进聊天窗口的 Markdown 报告。"""
    baseline = baseline or planner.baseline(people)
    saved = round(baseline.per_capita - best.per_capita, 2)
    ratio = best.ratio_vs(baseline)

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    if planner.menu.store:
        code = f"（{planner.menu.store_code}）" if planner.menu.store_code else ""
        lines.append(f"门店：**{planner.menu.store}**{code}　人数：**{len(people)} 人**")
        lines.append("")

    lines.append("## 结论")
    lines.append("")
    if saved > 0.009:
        lines.append(
            f"**{len(people)} 人拼一单，人均实付 {_fmt(best.per_capita)} 元，"
            f"比各点各的省 {_fmt(saved)} 元/人，相当于 {_zhe(ratio)}。**"
        )
    else:
        lines.append(
            f"**{len(people)} 人拼一单，人均实付 {_fmt(best.per_capita)} 元，"
            f"与各点各的持平（{_zhe(ratio)}）。**"
        )
    lines.append("")

    lines.append("## 方案对比")
    lines.append("")
    lines.append("| 方案 | 餐品小计 | 配送费 | 用券 | 应付总额 | 人均实付 | 折扣 |")
    lines.append("|---|---:|---:|---|---:|---:|---:|")
    lines.append(
        f"| 各点各的（基准） | {_fmt(baseline.subtotal)} | {_fmt(baseline.delivery_fee)} | 各自用券 |"
        f" {_fmt(baseline.total)} | {_fmt(baseline.per_capita)} | 原价 |"
    )
    coupon_name = best.coupon.name if best.coupon else "无可用券"
    lines.append(
        f"| **一起拼一单（推荐）** | {_fmt(best.subtotal)} | {_fmt(best.delivery_fee)} | {coupon_name} |"
        f" {_fmt(best.total)} | **{_fmt(best.per_capita)}** | **{_zhe(ratio)}** |"
    )
    lines.append("")

    lines.append("## 省在哪")
    lines.append("")
    per_delivery_saved = round(
        (baseline.delivery_fee - best.delivery_fee) / len(people), 2
    )
    if per_delivery_saved > 0:
        lines.append(
            f"- **配送费分摊**：拼单只收一笔配送费 {_fmt(best.delivery_fee)} 元，"
            f"人均比各自承担一单少付 {_fmt(per_delivery_saved)} 元"
        )
    if best.coupon:
        lines.append(
            f"- **满减券命中**：凑到 {_fmt(best.coupon.threshold)} 元门槛，"
            f"用上「{best.coupon.name}」减免 {_fmt(best.coupon.discount)} 元"
            f"（{best.coupon.source_label}）"
        )
    swapped = [p for p in best.picks if p.config_label]
    if swapped:
        names = "、".join(p.name for p in swapped)
        lines.append(f"- **套餐换配置**：{names} 替换了套餐内商品（见下方明细）")
    lines.append("")

    lines.append("## 最优组合明细")
    lines.append("")
    lines.append("| 成员 | 餐品 | 配置调整 | 餐品小计 | 应付 |")
    lines.append("|---|---|---|---:|---:|")
    for pick in best.picks:
        lines.append(
            f"| {pick.name} | {'、'.join(pick.items)} | {pick.config_label or '—'} |"
            f" {_fmt(pick.items_subtotal)} | **{_fmt(pick.due)}** |"
        )
    lines.append(f"| **合计** | — | — | {_fmt(best.subtotal)} | **{_fmt(best.total)}** |")
    lines.append("")

    if curve:
        lines.append("## 人数—人均实付")
        lines.append("")
        lines.append("| 人数 | 订单数 | 人均实付 | 折扣 |")
        lines.append("|---:|---:|---:|---:|")
        for k, plan_k in curve:
            base_k = planner.baseline(list(people[:k]))
            orders = "1 单" if k == 1 else f"{k} 单 → 1 单"
            lines.append(
                f"| {k} 人 | {orders} | {_fmt(plan_k.per_capita)} | {_zhe(plan_k.ratio_vs(base_k))} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "> 下单说明：本方案由**发起人用自己的麦当劳账号下这一单**（订单、支付链接、取餐码"
        "均归属发起人账号），其余成员按上表金额向发起人转账。本项目不代他人下单，"
        "也不修改任何价格。"
    )
    lines.append("")
    lines.append(
        "> 数据说明：餐品价格与优惠券均来自麦当劳 MCP 实时返回；实际以结算页为准。"
    )
    return "\n".join(lines)
