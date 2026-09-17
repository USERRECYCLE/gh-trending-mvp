# duckpipe

声明式数据管道编排工具。把管道定义从调度状态中分离出来，使管道本身可以像普通代码
一样被版本控制、被 review、被单元测试。

## 为什么做这个

大多数调度器把「管道定义」和「运行状态」混在一起存进同一个元数据库，结果是：

- 管道定义无法被 CI 检查，改一个字段要等下一次调度才知道对不对
- 本地无法完整重现线上行为，只能连线上元数据库调试
- 回滚一次定义变更要靠手工改数据库

duckpipe 的做法是：管道定义就是普通的 Python 函数或 YAML 文件，调度状态单独存。
定义可以被 import、被 pytest 直接调用，不需要起任何服务。

## 快速开始

    pip install duckpipe
    duckpipe init my_pipeline
    cd my_pipeline && duckpipe run

本地默认用 DuckDB 作为执行引擎，不需要任何外部依赖。

## 核心概念

**Pipeline**：一组任务与它们之间的依赖关系，用装饰器或 YAML 声明。

**Task**：一个可调用对象，接收上游产物、返回本次产物。无副作用，可重复执行。

**Store**：调度状态与运行历史的存储层，支持 SQLite、PostgreSQL。

## 支持的数据源

PostgreSQL、MySQL、BigQuery、Snowflake、S3、本地文件（CSV / Parquet / JSON）。

## 增量同步

每个 Task 可以声明自己的增量游标。duckpipe 负责持久化游标并在重跑时从中断点恢复，
失败任务可以精确重放而不影响已成功的下游。

## 测试

    pytest

管道定义本身是纯函数，可以直接断言输入输出，不需要 mock 调度器。

## 状态

alpha 阶段，API 仍可能变化。生产环境请谨慎评估。

## License

Apache-2.0
