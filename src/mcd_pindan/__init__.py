"""麦麦拼单 (mcd-pindan) —— 基于麦当劳 MCP 的多人拼单凑单最优化工具。"""

from .optimizer import (
    Choice,
    Coupon,
    Meal,
    Menu,
    OptionGroup,
    Person,
    PersonPick,
    Plan,
    Planner,
    Variant,
    build_planner,
    render_markdown,
)

__version__ = "0.1.0"

__all__ = [
    "Choice",
    "Coupon",
    "Meal",
    "Menu",
    "OptionGroup",
    "Person",
    "PersonPick",
    "Plan",
    "Planner",
    "Variant",
    "build_planner",
    "render_markdown",
    "__version__",
]
