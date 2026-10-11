"""麦麦拼单核心引擎单元测试（仅依赖标准库 unittest）。

运行：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from mcd_pindan.optimizer import (  # noqa: E402
    Choice,
    Coupon,
    Meal,
    Menu,
    OptionGroup,
    Person,
    Planner,
    build_planner,
    render_markdown,
)


def simple_menu() -> Menu:
    """两人份最小菜单：A 套餐 40 元（副食可换成 -5 的小份），B 单品 20 元。"""
    bundle = Meal(
        code="SET_A",
        name="A套餐",
        price=40.0,
        groups=(
            OptionGroup("副食", (Choice("中薯条", 0.0), Choice("小薯条", -5.0))),
        ),
    )
    return Menu([bundle, Meal(code="ITEM_B", name="B单品", price=20.0)])


class TestCouponEngine(unittest.TestCase):
    def test_threshold_gating(self):
        """券只能用门槛满足的那张。"""
        planner = Planner(
            menu=simple_menu(),
            coupons=[Coupon("C30", "满30减5", 30, 5), Coupon("C100", "满100减30", 100, 30)],
            delivery_fee=0.0,
        )
        self.assertEqual(planner.best_coupon(29.9), None)
        self.assertEqual(planner.best_coupon(30.0).code, "C30")
        self.assertEqual(planner.best_coupon(100.0).code, "C100")

    def test_delivery_fee_charged_once(self):
        """拼单只产生一笔配送费；各点各的按人数各收一次。"""
        planner = Planner(menu=simple_menu(), coupons=[], delivery_fee=9.0)
        people = [Person("甲", ("ITEM_B",)), Person("乙", ("ITEM_B",))]

        pinned = planner.plan(people)
        baseline = planner.baseline(people)

        self.assertEqual(pinned.delivery_fee, 9.0)
        self.assertEqual(baseline.delivery_fee, 18.0)
        self.assertLess(pinned.total, baseline.total)

    def test_swap_only_when_allowed(self):
        """allow_swap=False 时不得替换套餐内商品。"""
        planner = Planner(menu=simple_menu(), coupons=[], delivery_fee=0.0)
        people = [Person("甲", ("SET_A",), allow_swap=False)]
        plan = planner.plan(people, allow_swap=False)
        self.assertEqual(plan.subtotal, 40.0)

        plan_swap = planner.plan([Person("甲", ("SET_A",))], allow_swap=True)
        self.assertEqual(plan_swap.subtotal, 35.0)

    def test_never_worse_than_baseline(self):
        """推荐方案的人均实付不得高于基准方案（引擎的正确性下限）。"""
        planner = Planner(
            menu=simple_menu(),
            coupons=[Coupon("C50", "满50减10", 50, 10)],
            delivery_fee=9.0,
        )
        people = [Person(n, ("SET_A",)) for n in ("甲", "乙", "丙")]
        plan = planner.plan(people)
        baseline = planner.baseline(people)
        self.assertLessEqual(plan.per_capita, baseline.per_capita + 1e-9)

    def test_allocation_sums_to_total(self):
        """人均分摊金额之和必须精确等于应付总额。"""
        planner = Planner(
            menu=simple_menu(),
            coupons=[Coupon("C50", "满50减10", 50, 10)],
            delivery_fee=9.0,
        )
        people = [Person(n, ("SET_A", "ITEM_B")) for n in ("甲", "乙", "丙")]
        plan = planner.plan(people)
        self.assertAlmostEqual(sum(p.due for p in plan.picks), plan.total, places=2)

    def test_no_negative_allocation(self):
        """任何人的分摊额都不应为负数。"""
        planner = Planner(
            menu=simple_menu(),
            coupons=[Coupon("C100", "满100减90", 100, 90)],
            delivery_fee=0.0,
        )
        people = [Person(n, ("SET_A", "ITEM_B")) for n in ("甲", "乙")]
        plan = planner.plan(people)
        self.assertTrue(all(p.due >= 0 for p in plan.picks))


class TestDemoInput(unittest.TestCase):
    """对仓库内示例数据做端到端校验。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(ROOT, "examples", "demo_input.json")
        with open(path, "r", encoding="utf-8") as handle:
            cls.payload = json.load(handle)
        cls.planner, cls.people = build_planner(cls.payload)

    def test_more_people_lower_per_capita(self):
        """在“同一档次餐品”的前提下，人越多人均实付越低。"""
        curve = self.planner.curve(self.people)
        per_capita = [plan.per_capita for _, plan in curve]
        for earlier, later in zip(per_capita, per_capita[1:]):
            self.assertLess(later, earlier, f"人均实付未随人数下降：{per_capita}")

    def test_discount_ratio_improves(self):
        """相对“各点各的”的折扣率随人数单调改善。"""
        ratios = []
        for k, plan in self.planner.curve(self.people):
            baseline = self.planner.baseline(list(self.people[:k]))
            ratios.append(plan.ratio_vs(baseline))
        for earlier, later in zip(ratios, ratios[1:]):
            self.assertLess(later, earlier, f"折扣率未随人数改善：{ratios}")

    def test_plan_is_materially_cheaper(self):
        """推荐方案应比基准方案明显便宜（>25%），否则演示数据失去意义。"""
        plan = self.planner.plan(self.people)
        baseline = self.planner.baseline(self.people)
        self.assertLess(plan.ratio_vs(baseline), 0.75)

    def test_report_contains_key_sections(self):
        """报告包含结论、对比、省在哪、明细与下单说明。"""
        plan = self.planner.plan(self.people)
        report = render_markdown(
            self.planner,
            self.people,
            plan,
            baseline=self.planner.baseline(self.people),
            curve=self.planner.curve(self.people),
        )
        for keyword in ("## 结论", "## 方案对比", "## 省在哪", "## 最优组合明细", "发起人"):
            self.assertIn(keyword, report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
