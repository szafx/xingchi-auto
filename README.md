# 星驰公益站 自动签到 / 转盘 / 盲盒

每天自动领签到额度、把消费攒的抽奖券转掉、开盲盒，并把结果写进 KV 供查询。
跑在 Cloudflare Worker 上，Cron 每天北京时间 00:05 执行。

## 实测到的接口（全部真调通过）

| 功能 | 方法 | 路径 | 返回要点 |
|---|---|---|---|
| 签到状态 | GET | `/api/v1/user/checkin` | `enabled` `amount`(300) `claimed_today` `next_claim_at` |
| 签到 | POST | `/api/v1/user/checkin` | 409 `CHECKIN_ALREADY_CLAIMED` = 今天已领 |
| 抽奖状态 | GET | `/api/v1/lottery` | `available_tickets` `consume_per_ticket`(500) `daily_limit` `reset_at` |
| 抽一次 | POST | `/api/v1/lottery/draw` | 400 `LOTTERY_NO_TICKET` = 没券 |
| 盲盒状态 | GET | `/api/v1/lottery/blind-box` | `available_blind_boxes` + 奖品表 |
| 开盲盒 | POST | `/api/v1/lottery/blind-box/open` | 同上 |
| 消费返利 | GET | `/api/v1/user/welfare` | 阶梯：消费 600→返 300 … 15000→返 20000 |
| 余额 | GET | `/v1/usage`（**用 sk Key，不需要控制台登录**） | `balance` `remaining` `unit` `daily_usage[]` `model_stats[]` |
| 刷新令牌 | POST | `/api/v1/auth/refresh` | 旋转式：`access_token` + **新的** `refresh_token`，`expires_in=86400` |

鉴权：控制台接口用 `Authorization: Bearer <JWT>`；中转接口用 `Authorization: Bearer sk-...`。

## 目录

```
local_run.py            本地执行器（Python，逻辑与 Worker 一致，可直接跑）
worker/src/index.js     Cloudflare Worker
worker/wrangler.toml    Cron + KV 绑定
scripts/deploy-cf.ps1   一条命令部署到 CF
state.json              最近一次运行结果（本地）
```

## 本地先跑一遍

```powershell
$env:XC_API_KEY = "sk-...."
python local_run.py
```

输出示例（2026-11-23 实跑）：

```
== refresh => 200 {"expires_in": 86400}
== checkin.status => 200 {"claimed_today": true, "amount": 300, "next_claim_at": "2026-11-24T00:00:00+08:00"}
== checkin.claim => 0 {"skipped": "already claimed today"}
== lottery.status => 200 {"available_tickets": 0, "consume_per_ticket": 500}
== balance => 200 {"balance": 3599.984726, "unit": "USD"}
```

## 部署到 Cloudflare

```powershell
wrangler login            # 一次性
powershell -ExecutionPolicy Bypass -File scripts\deploy-cf.ps1
```

脚本会：建 KV → 写 `wrangler.toml` → 塞 4 个 Secret → `wrangler deploy` → 给出验证 URL。

Secret 说明：

| 名称 | 用途 |
|---|---|
| `XC_SESSION` | 首次的 access token |
| `XC_REFRESH` | refresh token（Worker 会把它轮换后的新值写回 KV） |
| `XC_API_KEY` | sk- 中转 Key，只为查余额 |
| `STATUS_TOKEN` | 访问 `/status`、`/run` 的口令 |

## Worker 路由

| 路径 | 说明 |
|---|---|
| `GET /` | 健康检查 |
| `GET /status?token=...` | 最近一次运行结果 |
| `GET /run?token=...` | 立刻执行一遍 |
| `GET /balance?token=...` | 只查余额 |

Cron：`5 16 * * *`（UTC）= 北京 00:05。

## 本地开发

```powershell
cd worker
# .dev.vars 里放 XC_SESSION / XC_REFRESH / XC_API_KEY / STATUS_TOKEN
wrangler dev --port 8787 --local
curl "http://127.0.0.1:8787/run?token=localdev"
```

已验证（2026-11-23）：refresh 200 → checkin 状态 200（今日已领）→ lottery 状态 200 → welfare 200 → balance 3599.98。

## 令牌过期怎么办

- access 24 小时过期，但 Worker 每次运行都会用 refresh 换新的，并写回 KV —— 只要 refresh token 一直有效，就不用管。
- 一旦 refresh 失效（比如在别处登录把它轮换了），重新跑一次 `login_cdp.py` 拿新令牌，再 `wrangler secret put XC_SESSION / XC_REFRESH` 覆盖即可。
