# 麦当劳 MCP 接入说明

本文档说明「麦麦拼单」实际使用了哪些麦当劳 MCP 能力、如何调用、以及这些调用带来的业务价值。

---

## 1. MCP Server 接入信息

| 项 | 值 |
|---|---|
| 服务地址 | `https://mcp.mcd.cn` |
| 传输协议 | Streamable HTTP（JSON-RPC 2.0） |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCD_MCP_TOKEN>` |
| 协议版本 | `2025-06-18` |
| 客户端实现 | `src/mcd_pindan/mcp_client.py`（仅用 Python 标准库，无第三方依赖） |
| 限流 | 单 Token 每分钟最多 600 次请求，超出返回 `429` |
| 脱敏配置示例 | [`mcp-config.example.json`](mcp-config.example.json) |
| 代理开关 | 环境变量 `MCD_MCP_NO_PROXY=1` 可绕过系统代理（见下文） |

本项目的 MCP 客户端完整实现了 `initialize` → `notifications/initialized` → `tools/list` → `tools/call`
的调用序列，并同时兼容 `application/json` 与 `text/event-stream`（SSE）两种响应封装。

### 1.1 代理与环境注意事项（实测）

Python 标准库 `urllib` 会**自动读取** `HTTP_PROXY` / `HTTPS_PROXY` 环境变量。
开发机上常见的 Clash、Surge、公司代理一旦不放行 `mcp.mcd.cn`，请求就会以
`Tunnel connection failed: 502 Bad Gateway` 失败，看上去像是「服务不可用」。

本项目提供两个应对手段：

1. **`MCD_MCP_NO_PROXY=1`** —— 让客户端绕过系统代理直连；
2. **`tools/probe.py --smoke`** —— 自动先试代理、再试直连，直接告诉你哪个能通。

此外，macOS 上偶发「`dig mcp.mcd.cn` 能解析、但 Python 报
`[Errno 8] nodename nor servname provided`」的情况，属于 Python 的 DNS 通道问题，
`--smoke` 同样会识别并给出 hosts 修复命令。

完整的排障与校准流程见 [`docs/CALIBRATION.md`](docs/CALIBRATION.md)。

---

## 2. 使用的 MCP Tool 清单

### 2.1 数据拉取类（方案计算前）

| Tool | 调用时机 | 在项目中的作用 | 业务价值 |
|---|---|---|---|
| `query-nearby-stores` | 用户提供地址后 | 定位门店，拿到 `storeCode` | 门店决定了可售菜单与**门店券**，是整个计算的前提 |
| `delivery-query-stores` | 外送场景 | 查询收货地址附近可配送门店 | 外送与到店取餐的可用券、配送费不同，必须区分 |
| `query-meals` | 确定门店后 | 拉取可售餐品、编码、价格 | 提供**真实的单品价格**，是引擎唯一的价格来源 |
| `query-meal-detail` | 对每个涉及到的**套餐**逐条调用 | 获取套餐内可替换的商品组合与差价 | **本项目的核心省钱杠杆之一**：把不需要的贵单品换掉 |
| `query-my-coupons` | 拉完菜单后 | 查询账户下所有可用优惠券 | 提供券的门槛与面额，是第二个省钱杠杆 |
| `query-store-coupons` | 同上 | 查询当前门店下可用的券 | 门店维度券最容易被忽略，单独调用以免漏券 |
| `auto-bind-coupons` | 计算前先调用一次 | 自动领取麦麦省所有可领券 | 避免「明明有券没领」导致的方案失真 |
| `available-coupons` | 同上 | 查询麦麦省可领取的券列表 | 补充券池，与 `auto-bind-coupons` 配合 |
| `campaign-calendar` | 计算前调用 | 查询当月营销活动日历 | 当期活动（如第二杯半价）会改变最优组合 |
| `now-time-info` | 需要判断活动是否在有效期内时 | 获取当前时间 | 活动日历需按当天日期过滤 |

### 2.2 结算与下单类（用户确认方案后）

| Tool | 调用时机 | 在项目中的作用 |
|---|---|---|
| `calculate-price` | 用户确认方案后、下单前 | 用真实价格接口**复算**一次，校验引擎算出的金额，杜绝本地计算与实际结算不一致 |
| `create-order` | 复算一致且用户确认后 | 由**发起人本人**创建订单，返回订单详情与支付链接 |
| `query-order` | 下单后 | 查询订单状态与取餐信息，回写给发起人 |

> **说明**：`calculate-price` 与 `create-order` 只由发起人本人发起。本项目在架构上
> 是「本地计算 + 单人下单」，不代他人下单，也不修改任何价格。

---

## 3. 调用流程

```
① 定位门店
   delivery-query-stores / query-nearby-stores  →  storeCode
        │
② 拉菜单
   query-meals                                  →  餐品编码 / 名称 / 价格
        │
③ 查套餐可替换项（对每个套餐调用一次）
   query-meal-detail                            →  可替换商品 + 差价
        │
④ 收集券（先领后查，避免漏券）
   auto-bind-coupons  →  available-coupons
   query-my-coupons   →  query-store-coupons
        │
⑤ 当期活动
   now-time-info  +  campaign-calendar          →  进行中的活动
        │
        ▼
   ⑥ 本地最优化求解（不产生 MCP 调用）
      枚举「每人换什么配置」×「用哪张券」
      目标：人均实付最低
        │
⑦ 复算校验（用户确认方案后）
   calculate-price                              →  真实价格
        │
⑧ 发起人下单
   create-order  →  query-order
```

**MCP 调用次数估算**：一次 5 人拼单方案约需 10 ~ 15 次 `tools/call`，
远低于每分钟 600 次的限流上限。

---

## 4. 数据结构映射

MCP 各工具的返回字段名以官方实时返回为准。本项目把所有字段映射集中在
`src/mcd_pindan/mcp_client.py` 的**响应适配层**（`adapt_meals` / `adapt_meal_groups` /
`adapt_coupons`），并用别名表 `_pick()` 做容错。若某个工具的实际字段名有差异，
**只需修改适配层，不需要改动最优化引擎**。

| 引擎字段 | 来源 Tool | 说明 |
|---|---|---|
| `meals[].code` / `name` / `price` | `query-meals` | 餐品编码、名称、价格 |
| `meals[].groups[].name` | `query-meal-detail` | 可替换维度，如「副食」「饮品」 |
| `meals[].groups[].choices[].delta` | `query-meal-detail` | 相对套餐基础价的差价，可为负数（可省钱） |
| `coupons[].threshold` / `discount` | `query-my-coupons` / `query-store-coupons` / `available-coupons` | 券门槛与减免额，`source` 标记券来源 |
| `delivery_fee` | `delivery-query-stores` / `calculate-price` | 配送费按单收取 |
| `store` / `store_code` | `query-nearby-stores` | 门店信息 |

---

## 5. 业务价值

### 对用户

1. **真金白银的省钱**：示例场景下 5 人拼单人均从 46.00 元降到 30.80 元（6.7 折），
   省下来的每一分钱都能在报告里逐项追溯到来源。
2. **把算不清的事算清**：满减门槛、门店券、套餐替换、配送费分摊四个变量叠加时，
   人脑几乎不可能找到最优解。
3. **解决 AA 尴尬**：直接给出每个人应付多少，减少「谁点得多谁点得少」的扯皮。

### 对麦当劳 MCP 生态

1. **用到了长期被冷落的能力**：`query-meal-detail` 的套餐替换选项是一个
   「可换商品组合」能力，实际很少被调用；本项目把它变成了可量化的省钱手段。
2. **把分散的工具串成一条完整业务链**：从定位门店、拉菜单、查券、算价到下单，
   一条链路打通，展示了 MCP 在真实生活场景中的组合价值。
3. **合规示范**：证明了「在真实规则内做最优化」既能带来真实优惠，
   又不需要平台补贴、不涉及价格篡改、不需要实时后端。

---

## 6. 未使用的能力及原因

为保证方案可信，以下能力**有意未纳入**：

| 未使用 | 原因 |
|---|---|
| `draw-lottery` / `mall-create-order` 等积分与商城能力 | 与「点餐凑单」场景不直接相关，纳入会分散方案重心 |
| `query-meal-assistance`（企业团餐助餐） | 需要企业团餐资质，个人账号场景下不适用；未来可作为 B 端扩展方向 |
| `party-order-create`（主题活动下单） | 属于预约场景，与即时拼单的成本模型不同 |

---

## 7. 安全与隐私

| 措施 | 实现 |
|---|---|
| Token 不落盘 | 仅从环境变量 `MCD_MCP_TOKEN` 读取，不写入任何文件、不出现在输出中 |
| 配置脱敏 | `mcp-config.example.json` 只使用 `${MCD_MCP_TOKEN}` 占位符 |
| 实时数据不提交 | `.gitignore` 屏蔽 `data/live/`、`*.live.json` |
| 探测输出自动脱敏 | `tools/probe.py` 默认把手机号、地址、订单号等字段替换为 `***` |
| 不收集他人信息 | 不索取、不存储他人的 Token、手机号、地址、订单信息 |
| 错误处理 | `401` 提示 Token 无效；`429` 提示降频；代理/DNS 异常给出可执行的修复建议 |

---

## 8. 免责声明

本项目为麦当劳程序员创意开发大赛参赛作品，由参赛者独立开发，**非麦当劳官方产品**。
项目输出仅供参考，不构成任何形式的承诺；餐品信息、价格及供应状态以麦当劳官方渠道的
实时结果为准。本项目不修改任何价格，不代他人下单，不涉及商业售卖或引流变现。
