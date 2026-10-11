#!/usr/bin/env python3
"""麦麦拼单 · 零依赖启动器。

不需要 pip install，也不依赖任何第三方库：

    python3 pindan.py --input examples/demo_input.json
    python3 pindan.py --live --people "我:BIGMAC_SET" --people "小王:MCCHICKEN_SET" --delivery-fee 9
    python3 pindan.py --list-tools
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from mcd_pindan.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
