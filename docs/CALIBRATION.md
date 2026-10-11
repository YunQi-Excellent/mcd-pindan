# 真实 MCP 联调手册

本手册说明如何把「离线可跑」的麦麦拼单，接通到**真实的麦当劳 MCP**。

---

## 联调到底要解决什么

联调不是「把程序跑起来」那么简单。真实接入必须依次确认四件事，**任何一件没确认，后面都是空转**：

| # | 要确认的事 | 确认手段 | 不确认的后果 |
|---:|---|---|---|
| 1 | **网络能不能通**（代理 / DNS / VPN） | `probe.py --smoke` | 报「网络错误」，误以为服务挂了 |
| 2 | **握手能不能成**（协议版本、鉴权） | `probe.py --smoke`（需真 Token） | 后续所有调用都 401 |
| 3 | **返回的字段名是什么** | `probe.py --sequence` | 解析出空数据，方案全是 0 |
| 4 | **工具的入参名是什么** | `probe.py --list` | 传参被忽略，返回默认结果 |

第 3、4 项是本项目最大的不确定点：适配层里的字段别名是按经验写的，
**必须用真实返回校准**。

---

## 第 0 步 · 申请 Token

1. 打开 https://open.mcd.cn/mcp ，右上角【登录】，用手机号完成验证；
2. 点【控制台】→【激活】，申请 MCP Token；
3. 阅读并同意服务协议，复制 Token。

Token 绑定的是**你本人的麦当劳会员账号**。

### 需要配置系统级环境变量吗？

**不需要，也不建议。** 三种方式任选，程序都支持：

| 方式 | 写法 | 说明 |
|---|---|---|
| **A. 单次命令前缀**（最推荐） | `MCD_MCP_TOKEN="xxx" python3 tools/probe.py --smoke` | 只作用于这一条命令，不落任何文件 |
| **B. 当前终端临时设置** | `export MCD_MCP_TOKEN="xxx"` | 只对当前终端窗口有效，关掉终端即失效 |
| **C. `.env` 文件** | `cp .env.example .env` 再填入 | 一次配好，多个终端共用，省得重敲 |

**优先级：命令行前缀 / export &gt; `.env`。**

为什么建议不要写进 `~/.zshrc` 之类的系统级配置：Token 是**凭证**，写进 dotfile 后会被
各种备份、同步、截图、误提交带走，属于典型的信息泄露面。用 `.env`（已 gitignore）
或终端临时变量就够，而且更安全。

> 该项目**不读取** `mcp-config.example.json` 里的 Token——那个文件只是给 WorkBuddy
> 连接器用的脱敏配置示例。命令行工具走的是上面的三种方式。

---

## 第 1 步 · 连通性自检

```bash
python3 tools/probe.py --smoke
```

**没有 Token 也能跑**：服务端返回 `401` 就说明网络与协议通路是正常的。

### 已实测到的三个坑

本项目在真实环境中实测发现以下问题，`--smoke` 会自动识别并给出处置建议：

| 现象 | 真实原因 | 处置 |
|---|---|---|
| `Tunnel connection failed: 502 Bad Gateway` | 开发机设置了 `HTTP_PROXY` / `HTTPS_PROXY`（Clash、Surge、公司代理等），该代理不放行 `mcp.mcd.cn`。**Python 的 urllib 会自动读取这两个环境变量** | `export MCD_MCP_NO_PROXY=1` |
| `[Errno 8] nodename nor servname provided`，但 `dig mcp.mcd.cn` 能解析出 IP | Python 走的 DNS 通道解析失败（macOS 解析器与系统 DNS 不一致时会出现） | 加 hosts 记录：`sudo sh -c 'echo "101.226.95.153  mcp.mcd.cn" >> /etc/hosts'` |
| 浏览器能打开 open.mcd.cn，但脚本连不上 | VPN / 校园网 / 公司网络不放行 `mcp.mcd.cn` | 换网络，或 `export MCD_MCP_URL=` 指向自建网关 |

**成功的输出长这样：**

```
结论：网络可达，但**必须绕过系统代理**。
     请先执行：export MCD_MCP_NO_PROXY=1
```

或（已有有效 Token 时）：

```
[尝试 1/2] 使用系统代理 ...
  握手成功 ✅
  协议版本：2025-06-18
  服务端：xxx 1.0.0
```

---

## 第 2 步 · 核对工具与入参名

```bash
python3 tools/probe.py --list
```

输出每个工具及其**入参名**，同时把完整 JSON Schema（含参数类型、是否必填）
写入 `data/live/01-tools-list.json`。

> 若入参名与 `mcp_client.py` 的 `collect_input()` 不一致（例如传的是 `storeCode` 但实际要 `storeId`），
> 需要同步修改那里。本步骤就是专门用来发现这种问题的。

---

## 第 3 步 · 链路探测 + 字段报告（联调主流程）

```bash
python3 tools/probe.py --sequence --people "我:BIGMAC_SET" --store-code <门店编码>
```

它会按标准链路逐个调用工具：

```
query-nearby-stores → query-meals → query-meal-detail（每个套餐一次）
→ auto-bind-coupons → query-my-coupons → query-store-coupons
→ available-coupons → campaign-calendar
```

每个工具的**原始返回**落到 `data/live/02-*.json`，并生成一份
`data/live/FIELD_REPORT.md`，直接告诉你适配层的别名猜对没有：

```
## 2. 适配层别名命中情况

### query-meals（餐品）
- 命中（4）：`code`, `name`, `title`, `price`
- 未命中（9）：`mealCode`, `productCode`, ...
```

- **未命中不代表出错**，只说明那个候选别名没被用到（可以保留，也可以清理）；
- 真正要警惕的是某个分组里 **命中数为 0** —— 报告会打一个 ⚠️，
  说明字段名与预期完全不同，必须手工对照原始 JSON 补别名。

### 隐私保护

`query-my-coupons`、`query-my-account`、`order-list` 等工具会返回**你本人的
手机号、地址、订单**。探测器**默认自动脱敏**：

- 命中敏感键名（`phone`、`address`、`receiverName`、`token`、`payUrl` 等）的值替换为 `***`；
- 字符串中的手机号（`1[3-9]xxxxxxxxx`）一并打码。

如确需关闭：`--no-redact`（**不建议**，`data/live/` 已被 `.gitignore` 屏蔽，但请自行确认）。

---

## 第 4 步 · 校准适配层

**只需要改一个地方**：`src/mcd_pindan/mcp_client.py` 里的三个适配函数。

```python
# 校准前
code = _pick(record, "code", "mealCode", "productCode", "skuCode", "id")

# 假设真实返回用的是 "itemId" 和 "itemName"，把真实字段名加到候选列表最前面
code = _pick(record, "itemId", "code", "mealCode", "productCode", "skuCode", "id")
name = _pick(record, "itemName", "name", "mealName", "productName", "title")
```

三个适配函数与对应关系：

| 函数 | 校准哪个工具 | 产出 |
|---|---|---|
| `adapt_meals` | `query-meals` | 餐品编码 / 名称 / 价格 |
| `adapt_meal_groups` | `query-meal-detail` | 套餐可替换项与差价 |
| `adapt_coupons` | `query-my-coupons` / `query-store-coupons` / `available-coupons` | 券门槛与面额 |

改完重跑 `--sequence`，直到 `FIELD_REPORT.md` 里各分组都命中。

> **最优化引擎 `optimizer.py` 与 MCP 字段完全无关，不需要改动。**
> 这正是把字段映射单独抽成适配层的目的。

---

## 第 5 步 · 跑通完整链路

先落盘真实输入，便于反复复现：

```bash
python3 pindan.py --live \
  --people "我:BIGMAC_SET" --people "小王:MCCHICKEN_SET" \
  --store-code <门店编码> --delivery-fee 9 \
  --dump-input data/live/input.json
```

`--dump-input` 会把抓到的真实数据存成 JSON。之后就能**离线反复调试**，不再消耗接口：

```bash
python3 pindan.py --input data/live/input.json
```

确认方案合理后，再走一次完整实时流程。

---

## 第 6 步 · 金额复算校验（重要）

引擎算出的金额是本地计算的，**必须用服务端复算一次**：

1. 取最优方案的餐品列表；
2. 调用 `calculate-price`，传入商品列表与券；
3. 比对「服务端应付总额」与「引擎算出的总额」。

若不一致，说明落下了某个优惠规则（例如当期活动、或某类券不可叠加），
把差异记录下来并修正引擎的结算逻辑。

---

## 联调检查清单

```
[ ] 1. Token 已申请，export MCD_MCP_TOKEN 已生效
[ ] 2. probe.py --smoke 显示「握手成功」（不是 401）
[ ] 3. 如有代理/DNS 问题，已 export MCD_MCP_NO_PROXY=1 或加 hosts
[ ] 4. probe.py --list 已核对工具入参名与 collect_input() 一致
[ ] 5. probe.py --sequence 已生成 FIELD_REPORT.md
[ ] 6. FIELD_REPORT.md 各分组不再出现「命中数为 0」的 ⚠️
[ ] 7. 适配层已按真实字段名校准
[ ] 8. pindan.py --live 能输出完整报告
[ ] 9. calculate-price 复算金额与引擎一致
[ ] 10. data/live/ 未被提交进 Git（含个人地址/订单信息）
```

---

## 当前联调状态（诚实说明）

截至本项目提交时，**已验证**与**未验证**的部分如下：

| 项 | 状态 | 依据 |
|---|---|---|
| 端点 `mcp.mcd.cn` 真实可达 | ✅ 已验证 | 真实返回 `401 当前鉴权码不存在或已失效!` |
| TLS / SNI / 证书校验通路正常 | ✅ 已验证 | 用真实域名 + 系统解析出的 IP 完成了 HTTPS 交互 |
| 401 错误处理与中文错误信息解析 | ✅ 已验证 | 实际捕获并解析了服务端返回 |
| Streamable HTTP 客户端实现 | ✅ 已验证 | 请求成功送达并收到服务端响应 |
| 代理与 DNS 异常自动诊断 | ✅ 已验证 | 在本机实测复现并给出处置建议 |
| JSON-RPC `initialize` 载荷被服务端接受 | ⚠️ 待验证 | 需要真实有效 Token 才能越过鉴权 |
| 各工具返回的字段名 | ⚠️ 待验证 | 需执行第 3 步生成字段报告 |
| 各工具的入参名 | ⚠️ 待验证 | 需执行第 2 步核对 Schema |

> ⚠️ 三项待验证项都需要**你本人的麦当劳会员账号**才能完成，
> 这是账号绑定带来的客观限制，无法由第三方代办。
> 这正是本手册与 `tools/probe.py` 存在的意义——把这三项从「盲猜」变成「照单核对」。
