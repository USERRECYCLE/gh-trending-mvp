# GitHub 热榜 · AI 解读

把 GitHub Trending 从「一份裸榜单」变成「一份带中文技术解读的决策看板」：不用逐个点开仓库读英文 README，就能在单页里判断一个项目是干什么的、值不值得看。

**线上站点**: https://userrecycle.github.io/gh-trending-mvp/

全自动运行：每 6 小时抓一次榜单、为新增项目生成中文解读、发布静态页面、把数据集提交回仓库。用户访问时不触发任何第三方 API。

---

## 它做什么

### 榜单

| 维度 | 取值 |
|---|---|
| 时间窗口 | 日榜 / 周榜 / 月榜 |
| 语言维度 | 全部、Python、JavaScript、TypeScript、Go、Rust、Java |
| 视图总数 | 3 × 7 = **21 个** |

视图用 hash 表达（`#/daily/python`），任一组合都是可直接分享、可回访的链接。

### AI 解读

每个仓库生成一份结构化中文解读：一句话总结、3–5 条核心功能、技术栈、亮点、适合人群，以及创新性／实用性／学习价值三维评分（1–5）。

**解读由 AI 生成，仅供参考**，不保证准确。榜单顺序严格遵循 GitHub 原始排名——**AI 评分不影响排序**，避免模型判断凌驾于真实热度信号之上。

---

## 它是怎么工作的

```
GitHub Trending HTML ──抓取解析──┐
                                 ├──► 缓存命中率 → 配额判定 ──► README（仅未命中者）
本地 21 份快照（fixture）────────┘                                      │
                                                                      ▼
                                                        DeepSeek API ──► 结构校验
                                                                      │
   data/ ◄──── 写回数据（榜单 + 解读缓存 + 配额状态）◄──────────────────┘
     │
     └──► dist/（静态页面 + 前端 JSON）──► GitHub Pages
```

四个 Actions job 并行独立，`deploy` 只依赖 `build`：

| job | 作用 |
|---|---|
| `test` | 离线单元测试 |
| `online-smoke` | 用冻结快照对上游做结构校验，**不阻塞发布** |
| `build` | 跑完整链路 → 守护密钥泄漏 → 提交 `data/` → 上传 Pages 产物 |
| `deploy` | 发布到 GitHub Pages |

### 模块

依赖只有 `requests` 和 `beautifulsoup4` 两个，无框架、无构建工具。

| 文件 | 职责 | 纯度 |
|---|---|---|
| `scripts/main.py` | 入口：取数、编排、异常处理 | 命令式外壳 |
| `scripts/smoke.py` | 第二个入口：上游漂移校验与快照引导 | 命令式外壳 |
| `scripts/config.py` | 常量 + 跨模块纯规则 | — |
| `scripts/net.py` | **全部出网调用**，GET/POST 共用一套重试逻辑，传输层可注入 | 网络 |
| `scripts/fetch.py` | Trending HTML 纯解析、结构不变量断言、候选汇总 | **纯函数** |
| `scripts/analyze.py` | prompt 构建与模型输出解析校验 | **纯函数** |
| `scripts/quota.py` | 配额、Bootstrap 判定、滞回、熔断 | **纯函数** |
| `scripts/cache.py` | `data/` 与 `tests/fixtures/` 的**唯一读写入口** | I/O |
| `scripts/render.py` | 读 `data/` 出 `dist/` | I/O |

### 缓存与配额

去重键是 `owner/repo`。同一个仓库命中多个榜单时**共享一份解读**，不会重复消耗额度。

判定顺序（先命中者生效）：缓存不存在 → 提示词版本不匹配 → **当日额度已用完** → 距上次超过 7 天 → Star 变动超过 5000 或 50% → 复用缓存。

配额不是固定值，而由**缓存命中率自动决定**，无需人工开关：

| 生效配额 | 触发条件 |
|---|---|
| 稳态 50 次/日 | 命中率 ≥ 90% |
| Bootstrap 400 次/日（单轮 ≤ 200） | 命中率 < 60% |
| 维持上一轮 | 命中率落在 60%–90% |

进入与退出阈值刻意错开（滞回），避免命中率在阈值附近抖动导致配额反复跳变。

**熔断**：连续 3 日处于 Bootstrap 则强制回落稳态并告警。这是为了兜住对称风险——若命中率长期为 0（缓存丢失、上游改版），自动判定会从「忘记关闭」变成「关不掉」。

额度不足时按「日榜 > 周榜 > 月榜」、榜内按 GitHub 原始名次决定先分析谁。失败的仓库**不写缓存、不计配额**，下一轮自动重试。

---

## 本地开发

### 环境要求

Python 3.11+（Actions 用 3.11）。`pip install -r requirements.txt`。

### 跑一遍

```bash
# 离线跑通全链路：用冻结快照替代网络、用固定响应替代模型
INPUT_SOURCE=fixture python scripts/main.py
```

这会写 `data/`（榜单、解读缓存、配额状态）并渲染 `dist/`。用任意静态服务器打开 `dist/` 即可。

### 跑测试

```bash
python -m unittest discover -s tests
```

227 项离线测试，数十秒（实测 27–47 秒，随机器负载波动）。另有 9 项浏览器验收（E2/E3/E4），需要 Chromium 系浏览器，找不到会自动跳过并说明原因而非伪装通过：

```bash
SKIP_BROWSER_TESTS=1 python -m unittest discover -s tests   # 跳过浏览器验收
BROWSER_BIN=/path/to/chrome python -m unittest discover -s tests
```

### 网络受限时

`github.com` 在部分网络环境下不可达（本项目所在环境即如此）。这不影响线上运行——Actions 跑在 GitHub 自己机房。但**本地无法真实抓取**，所以：

- 全链路支持 `INPUT_SOURCE=fixture`：从 `tests/fixtures/` 读快照，与网络模式共用同一套解析代码
- 纯函数模块（解析、校验、配额）都可用 fixture 完全离线测试

---

## 目录结构

```
scripts/    逻辑（Python，零框架）
web/        前端源码（原生 HTML/CSS/JS，无构建步骤）
data/       数据集，入库（榜单、解读缓存、配额状态）
dist/       构建产物，不入库（上传为 Pages 产物）
tests/      测试与 fixture
```

**两条硬边界**：

- `cache.py` 是 `data/` 与 `tests/fixtures/` 的唯一读写入口。其他模块不得自行拼路径或 `open()`——未来把 `data/` 换成数据库或远端存储，只需替换这一个文件。
- 渲染阶段**只读 `data/`、只写 `dist/`**，可重复执行且不污染数据。

---

## 配置

### 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `INPUT_SOURCE` | `network` | `network` 走真实抓取；`fixture` 读本地快照 |
| `DEEPSEEK_API_KEY` | — | 必需（`network` 模式）。**只放在 Actions Secrets 里** |
| `REFRESH_FIXTURES` | 空 | 手动触发时覆盖已有快照，见下节 |
| `GITHUB_STEP_SUMMARY` | — | Actions 自动注入，本地运行时留空即可 |

### 关键阈值

都在 `scripts/config.py` 里，改常量即可：缓存 TTL 7 天、Star 变动阈值 5000 / 50%、配额与滞回阈值、README 截断 8000 字符、HTTP 重试 2 次与 20 秒超时、`prompt_version`。

**改动 prompt 必须同步递增 `prompt_version`**，否则缓存不会失效、旧解读会一直被复用。

---

## 运维

### 上游改版了怎么办

`tests/fixtures/` 里的 21 份 HTML 快照是**引导产物，只写一次**。此后每轮 `online-smoke` 只做校验、绝不覆盖——这样上游漂移会表现为**红灯**（smoke 失败），而不是被悄悄吸收进本地测试。

校验的是**结构不变量**而非内容一致（榜单内容每小时都在变）：条数 > 0、条数 ≤ 25、关键字段缺失率在容差内、URL 形态正确。其中**字段缺失率**比「条数 > 0」更敏感——上游只挪动某个字段的 DOM 位置时，条数可能仍然正常。

修复上游改版的正确动作是**两件事一起做**：

1. 改 `scripts/fetch.py` 里的选择器。描述与语言各配了 3 套后备选择器、周期徽章另有一层文本正则兜底；其余字段是单套，通常只有它们全失效时才需要动手
2. 刷新快照：手动触发 workflow 并勾选 `refresh_fixtures`

只做第一件不做第二件，会导致新选择器与旧快照不匹配、**本地测试永久红灯**。这不是缺陷——它强制选择器演进与快照更新绑定。

### 观察运行

```bash
gh run list   --repo USERRECYCLE/gh-trending-mvp
gh run view   --repo USERRECYCLE/gh-trending-mvp <run-id> --log
```

失败时 GitHub 会按平台默认行为通知仓库所有者；同时结构化诊断会写入 Step Summary——**仅在榜单失败时生成**，包含：哪个榜单、HTTP 状态、解析条数、各字段缺失率、HTML 中 `article` 标签数与解析出的条数、以及四个选择器探针（star 链接／fork 链接／语言 itemprop／周期徽章）各自命中与否。有了「未命中哪几个」，就能直接判断上游改的是哪一处。

---

## 设计取舍

几条刻意的决定，改代码前值得先读：

- **不按 AI 评分重排榜单**。评分只是展示信息。
- **长度类偏差截断，不拒绝**。模型偶尔给出 6 条功能或 84 字总结，内容是好的；为此丢弃一次已付费的调用不划算，更糟的是——若按长度拒绝，某个稳定超长的仓库会**永远进不了缓存、在站点上永久缺卡**。
- **校验用「容忍上界 + 归一化」而非贴合写作要求**。写作要求写进 prompt，校验只拦真正跑偏的输出。
- **DOM 解析用 BeautifulSoup 而非正则**，且每个字段多套选择器。正则对标签属性顺序和空白变化零防御。
- **前端禁用 `innerHTML`**，一律 `createElement` + `textContent`。仓库描述与 AI 解读都是外部文本，拼接 HTML 会形成注入面。
- **前端所有数据读取经由 `web/js/adapter.js`**。迁移到自有服务器时只换 adapter 实现，页面与渲染逻辑零改动。
- **语言色点取自页面自带的 `style`**，不维护硬编码色表。
- **中文检查用字符绝对数而非占比**。分析结果天然嵌满专有名词（Tauri、Kubernetes、vLLM），占比度量的是专有名词密度而不是回答语言。

---

## 已知未验证项

不假装已通过：

| 项 | 原因 |
|---|---|
| 首屏性能 | 无头浏览器的虚拟时间会扭曲 `performance.now()`，用它测只会得到必然通过的假数字。需 CI 引入 Lighthouse 或 CDP 计时探针 |
| 定时间隔 | cron 已配置，手动触发已走通全流程，但尚未观察到自然触发的 6 小时间隔 |

## 外部依赖与风险

- **GitHub Trending 页面无 API、无 SLA、无版本承诺**。任何一次改版都可能使其失效——已用结构不变量断言 + 独立的 `online-smoke` job 兜底，但只能让失败**可见**，不能**防止**。
- **DeepSeek API 是付费外部依赖**，有速率与余额限制。缓存与配额机制把成本控制在可预期范围。
- AI 生成内容可能存在错误，展示时未做事实核查。

## 许可

尚未指定。使用前请先确认。
