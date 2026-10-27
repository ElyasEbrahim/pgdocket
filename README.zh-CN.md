[English](README.md) | **简体中文**

# pgdocket

[![CI](https://github.com/ElyasEbrahim/pgdocket/actions/workflows/ci.yml/badge.svg)](https://github.com/ElyasEbrahim/pgdocket/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**为 Postgres 提供可持久保存、可版本化、经过校准的 AI 判断。** 你只需定义一次语义判断（例如 *"这条投诉是否与催收有关？"*），pgdocket 会把结果作为数据保存下来：带版本、用你自己的标注校准，并如实给出不确定性。它适用于任何 Postgres，也适用于任何判断模型。

> **状态：第 1 阶段进行中。** 已可用：[`pgdocket check`](pgdocket/README.md) 衡量 AI 判断在*你自己的*数据表上有多可靠；[判断记录表（ledger）](docs/phase1/ledger.md) 把判断保存在 Postgres 中，并在数据行变化时自动保持最新；[`pgdocket estimate`](docs/phase1/counts.md) 给出带置信区间的计数；[`pgdocket compare`](docs/phase1/versions.md) 在切换之前，把修改后的判断与当前版本进行对比。尚未开发：其他后端的适配器、正式发布。本仓库还包含设计文档（[REPORT.md](REPORT.md)、[架构决策记录](docs/adr/)）和第 0 阶段的基准测试（[实验结论](docs/phase0/FINDINGS-jev-1.13.md)）。进度通过 [Issues](https://github.com/ElyasEbrahim/pgdocket/issues) 和 [Milestones](https://github.com/ElyasEbrahim/pgdocket/milestones) 跟踪。

> 注：除本 README 外，其余文档目前仅有英文版。

## 要解决的问题

[pg-jev](https://github.com/realZachi/pg-jev) 让你用自然语言筛选数据行，例如 `WHERE jev(tickets, 'the customer threatens to cancel')`，背后由 TypeSafe 的 Jev 判断模型完成。这很适合探索。但要在生产中依赖这些结果，它们必须稳定、可比较、可信，而现在还做不到：

- 数据库会话结束后结果就消失，每个新会话都要把整张表重新判断一遍；
- 有人新增一个标签选项、换模型或换后端时，结果会变；
- 只是再问一次，或者把这一行和不同的行放在同一批里发送，结果也会变；
- `0.7` 的分数并不代表 70%，基于 AI 标签的 `count(*)` 也没有误差范围；
- 无法在 Supabase、Neon、RDS 等托管数据库上运行。

如今模型调用的成本大约只有**每百万行 6–25 美元**（取决于文本长度和每次请求的行数），瓶颈已经不是成本，而是这些结果能否被信任、能否被审计。

## 第 0 阶段测到了什么

用 2,000 条真实的消费者投诉文本，通过 pg-jev 交给 jev-1.13.0 判断，API 费用合计约 0.30 美元。

| 发现 | 结果 |
| --- | --- |
| 分数过于自信 | 9 选 1 分类的 ECE 为 **0.089**；是/否判断的 ECE 为 **0.24–0.38** |
| 约 100 条标注就能修正大部分偏差 | 用 100 条标注校准，可消除 **45–85%** 的误差 |
| 再问一次，答案会变 | 3 次完全相同的运行中，**4.8%** 的行的首选答案发生变化 |
| 同批的"邻居"会改变答案 | 同样的行换一种分批方式，约 **16.5%** 的答案改变（约为随机波动的 5 倍） |
| 每次只判断一行更好 | 逐行判断：准确率 **+2.4 个百分点**，macro-F1 +0.05，token 用量为 1.8 倍 |
| 新增选项会带走答案 | 新增一个"其他"类选项后，**2.5%** 的答案转到了它 |
| 分数很粗糙 | 概率以 0.01 为刻度，并且有大量恰好为 0 的值 |

面向一般读者的文章：[*Same row, different answer*](docs/notes/2026-10-same-row-different-answer.md)（英文）。详细结果、方法和局限：[docs/phase0/FINDINGS-jev-1.13.md](docs/phase0/FINDINGS-jev-1.13.md)（英文）。

## 安装

```bash
pip install "git+https://github.com/ElyasEbrahim/pgdocket"      # 需要 Python 3.10+；直接从 GitHub 安装
pgdocket --version
```

也可以使用容器：在本仓库中运行 `docker build -t pgdocket .`。**第一次使用？** [5 分钟快速上手](examples/quickstart/README.md)（英文）会用 Docker 启动 Postgres、worker 和 60 条合成的客服工单，一直演示到校准，以及在切换之前先试用一项修改。API 费用远低于 0.01 美元。

## 在你自己的数据上试用：`pgdocket check`

指定一张表、要发送的文本列、一个问题和一些标注（已有的标注列，或你手工标注的 CSV），它会报告：与标注的一致程度、校准情况、一个带有明确精确率保证的阈值、答案的稳定性以及成本。全程只读，报告中不包含任何行的文本内容。

```bash
pgdocket check --table public.tickets --key id --text subject,body \
  --kind choice --question "Which team should handle this ticket?" \
  --options billing,technical,security,sales --label-column team --yes
```

在两年的 Kubernetes issue 上，把 issue 分派给 10 个团队（SIG）的结果与人工标注的一致率为 **88.8%**；**90% 的 issue 可以自动分派，并以 95% 的置信度保证精确率 ≥ 90%**（若要求精确率 ≥ 95%，则为 56%）（[说明](docs/phase1/kubernetes-triage.md)、[报告](results/check-k8s-sig/report.md)，均为英文）。完整用法：[pgdocket/README.md](pgdocket/README.md)（英文）。

## pgdocket 能做什么，以及将要做什么

1. **持久保存的判断。** *已实现。* 结果写入只追加的记录表（ledger），并提供一个可以 join、可以建索引的视图。新增或修改的行会被自动判断，每次请求只判断一行。在 10 万行的表上读取单行只需 0.14 毫秒；在实时数据流中，从插入到得出判断的中位时间为 0.7 秒（[详情](docs/phase1/ledger.md)，英文）。
2. **用你自己的标注来校准。** *已实现。* 每个判断只需约 100–200 条人工标注，就能把原始分数变成名副其实的概率。视图中会显示校准后的置信度和 `auto` 标记。
3. **如实的数字。** *已实现。* 阈值附带明确的保证（如"在 95% 置信度下精确率 ≥ 90%"）。达不到目标的答案会被逐一标出，`--per-answer` 还能为每个答案分别给出保证。对于是/否类判断，还可以设定召回率目标：在保留例如 90% 真实“是”的前提下，自动把其余行判为“否”（[详情](docs/phase1/recall.md)，英文）。`pgdocket plan` 会告诉你需要人工复核多少行，才能让整列的错误率不超过某个上限，例如 5%（[详情](docs/phase1/review-planning.md)，英文）。计数附带置信区间：`pgdocket estimate` 把模型对每一行的判断与一小部分随机抽样的人工标注结合起来（预测驱动推断，prediction-powered inference）。在真实的 Kubernetes issue 上，其区间有 98–100% 的情况覆盖了真实值，并且比只用标注的区间窄 30–50%（[详情](docs/phase1/counts.md)，英文）。
4. **版本管理。** *已实现。* 修改问题、选项或模型会产生一个影子版本（shadow version）。`pgdocket compare` 让两个版本判断同一批抽样行，报告哪些结果的变化超出了重复请求本身的波动。它还会报告哪个版本更准确；为此只需标注两个版本结论不同的那些行。在 Kubernetes issue 分派上，去掉选项说明可节省 14% 的 token，但有 3.3% 的结果发生了超出波动的变化。标注这 13 处分歧后发现，选项说明值得保留（v1 判对 8 次，v2 判对 2 次）。在使用全部标注时，保留选项说明还能让 95% 的 issue 在保证 90% 精确率的前提下自动分派，而去掉后只有 90%。`promote` 会为新版本重新校准，因此自动化判断不会中断（[详情](docs/phase1/versions.md)，英文）。
5. **漂移检查。** *已实现。* `pgdocket drift` 检查所有保证背后的两个前提。其一，最近的行是否仍与较早的行相似：这项检查免费，直接使用已存储的判断。其二，模型对已标注的行是否仍给出与当初相同的判断：这项检查会把一组固定的已标注行重新提交给模型。多次重复检查共用同一份误报预算。在已存储的 Jev 判断上，误报率保持在 0–2.6%，CFPB 和 Kubernetes 标注中 13–17% 的真实变化都被发现；去掉选项说明这一变化也通过模型分数被检测出来（[详情](docs/phase1/drift.md)，英文）。`pgdocket audit` 直接检查被自动采用的答案：每批由人工在看不到 AI 答案的情况下标注少量自动采用的行，一旦精确率明显低于目标，随时有效（anytime-valid）的检验就会报警；每批之后都可以查看结果，而不会提高误报率。在真实数据流上它没有误报，精确率降到 70% 时，93% 的审计发现了问题（[详情](docs/phase1/audit.md)，英文）。
6. **试图操纵模型的行。** *已实现。* 当数据行由外部人员撰写时，他们可以在其中写入针对模型的文字。在 300 个 Kubernetes issue 上，issue 作者附加一段伪造的分派规则，就让 98.7% 的 issue 被分派到作者指定的团队，其中 93.3% 被 `auto` 自动采用：被操纵的答案同样显得非常有把握。
   - `pgdocket check --steer` 可以在你自己的表上测量这一点。
   - `--untrusted` 会让模型忽略数据行中的指令；加上它之后，同样的伪造规则只操纵了 1.3% 的 issue。
   - `calibrate --harden` 让一行只有在通过“擦除并检查”（erase-and-check）之后才会被标为 `auto`：去掉任意一段文字，答案都不能改变。它拦下了所有本会被 `auto` 采用的被操纵行，包括上述那句话挡不住的误导性说法。代价是 7% 的正常自动采用行会被转交人工复核。
   - 两者一起使用时，我们尝试的每种攻击最多只让 0.3% 的 issue 通过，唯一的例外是写了两遍的误导性说法（7.3%）；自动采用的比例从 95% 降到 86%（[详情](docs/phase1/steering.md)，英文）。
7. **随处可运行。** *已实现，并在 PostgreSQL 14–18 上以非超级用户身份测试通过。* 纯 SQL 加一个外部 worker，不需要不受信任的扩展。尚未在 Supabase、Neon、RDS 上实际测试。

架构与 SQL 草图：[REPORT.md §4](REPORT.md#4-the-recommended-project-docket)。已做出的架构决策：[docs/adr/](docs/adr/)。

## 快速开始：第 0 阶段测试工具

需要 Docker，所有组件都在容器中运行。

```bash
make up            # Postgres 16 + pg-jev v0.2.1、pg-jev 的模拟 API、一个模拟后端
make test          # 测试工具的单元测试
make smoke         # 用模拟 API 运行 pg-jev README 中的示例查询
make fetch load    # 2,000 条 CFPB 投诉文本（CC0 镜像）→ Postgres
make bench         # 在模拟后端上运行全部实验 → results/<run_id>/report.md
```

如果要调用真实的 Jev API，把 `.env.example` 复制为 `.env`，填入你的 TypeSafe 密钥，然后运行：

```bash
make bench BACKEND=jev ARGS=--yes   # 会先显示预估费用；超过 1 美元会拒绝执行
```

更多命令（重复性检验、标注学习曲线、分批影响）：[harness/README.md](harness/README.md)（英文）。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| [REPORT.md](REPORT.md) | 项目报告：pg-jev 剖析、前沿研究综述、候选方案、设计、评估计划、风险 |
| [ROADMAP.md](ROADMAP.md) | 分阶段计划及每阶段的完成标准 |
| [CHANGELOG.md](CHANGELOG.md) | 按日期记录的变更 |
| [docs/adr/](docs/adr/) | 架构决策记录 |
| [docs/phase0/](docs/phase0/) | 第 0 阶段的结论、状态、待提交给上游的问题草稿 |
| [docs/notes/](docs/notes/) | 面向公众的文章 |
| [docs/phase1/](docs/phase1/) | 第 1 阶段结果：判断记录表（ledger）、带误差范围的计数、版本对比、Kubernetes issue 分派 |
| [pgdocket/](pgdocket/) | 产品本身：`pgdocket check`、判断记录表与 worker、`estimate`、`compare` |
| [examples/](examples/) | [5 分钟快速上手](examples/quickstart/README.md)，以及示例检查所用的选项文件 |
| `pyproject.toml`、`Dockerfile` | 打包：`pgdocket` 命令和 worker 镜像（[发布流程](docs/RELEASING.md)，英文） |
| [harness/](harness/) | 基准测试工具：`jevbench` 命令行、模拟后端、测试 |
| [results/](results/) | 结论中引用的各次运行报告 |
| `docker-compose.yml`、`Makefile` | 测试环境及其命令 |

## 参考来源

pgdocket 基于 [realZachi/pg-jev](https://github.com/realZachi/pg-jev)（PostgreSQL 许可证）和 TypeSafe 的 Jev 模型。每项功能都采用了已发表的方法：
- **带保证的阈值：**
  - 单侧 Clopper–Pearson 界与固定顺序检验（Learn then Test：Angelopoulos、Bates、Candès、Jordan 和 Lei，2021）；
  - 按答案分别给出的保证参照按类别的共形预测（Ding 等，2023）；
  - 温度缩放（Guo 等，2017）与 Platt 缩放。
- **带误差范围的计数：** 预测驱动推断（Angelopoulos、Bates、Fannjiang、Jordan 和 Zrnic，2023）与 PPI++（Angelopoulos、Duchi 和 Zrnic，2023）。
- **人工复核规划：** 概率近似正确标注（Candès、Ilyas 和 Zrnic，2025）。
- **召回率目标：** SUPG（Kang 等，2020）与 BARGAIN（Zeighami、Shankar 和 Parameswaran，2026）。
- **版本比较：** 针对分歧行的精确符号检验（Dixon 和 Mood，1946）与自助法（Efron，1979）。
- **漂移检查：**
  - 基于模型自身输出的双样本检验（Failing Loudly：Rabanser、Günnemann 和 Lipton，2019）；
  - 重新提问一组固定的已标注行（Li，2026）。
- **审计：** 有害分布变化追踪（Podkopaev 和 Ramdas，2022），结合基于下注的 e-process（Waudby-Smith 和 Ramdas，2024）与 Ville 不等式。
- **试图操纵模型的行：**
  - Datadog Security Labs、Check Point、HiddenLayer（2026）以及 Wu 和 Lim（2026）报告的攻击；
  - 擦除并检查（Kumar 等，2024）与 spotlighting（Hines 等，2024）。
- **设计：** winkrun，《不用向量索引，Postgres 也能做语义过滤：pg-jev 上手》（2026 年 10 月 4 日）。文中列出的 pg-jev 注意事项直接影响了本项目的设计；本仓库不转载原文。

**全部来源、链接以及各自的用途：[REFERENCES.md](REFERENCES.md)（英文）。**

## 参与贡献

本项目由一人维护。欢迎提交 Issue 和问题；目前不接受 Pull Request。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

[Apache-2.0](LICENSE)。Copyright 2026 ElyasEbrahim。
