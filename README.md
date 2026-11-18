# 多维分析实验平台

面向实验设计、指标计算、样本分析、效果评估与结论发布的数据分析实验平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 实验设计 | `experiment` | 实验方案 | 方案编号、实验名称、对照组说明 |
| 样本分群 | `sample_group` | 样本分群 | 分群编号、分群名称、筛选条件 |
| 指标定义 | `metric_def` | 指标定义 | 指标编号、指标名称、计算公式 |
| AB结果分析 | `ab_result` | 分析报告 | 报告编号、实验编号、分析指标 |
| 分群对比 | `segment_compare` | 对比任务 | 任务编号、分群A、分群B |
| 多维下钻 | `drill_down` | 下钻维度 | 维度编号、维度名称、下钻层级 |
| 数据筛选 | `data_filter` | 筛选规则 | 规则编号、数据来源、筛选字段 |
| 归因分析 | `attr_analysis` | 归因任务 | 任务编号、目标指标、候选因子 |
| 异常检测 | `anomaly_detect` | 检测规则 | 规则编号、监控指标、检测算法 |
| 预测模型 | `forecast_model` | 模型配置 | 模型编号、预测对象、历史窗口 |
| 数据导出 | `data_export` | 导出任务 | 任务编号、数据来源、导出格式 |
| 同期群追踪 | `cohort_track` | 同期群定义 | 群组编号、群组名称、首次行为 |
| 可视化配置 | `chart_config` | 图表配置 | 图表编号、图表类型、数据来源 |
| 洞察报告 | `insight_report` | 洞察报告 | 报告编号、分析主题、数据来源 |
| 数据新鲜度 | `data_freshness` | 新鲜度监控 | 监控编号、数据表名、最新分区 |
| 口径注册 | `metric_alias` | 口径版本 | 版本编号、指标编号、变更说明 |
| 实验因子 | `expr_factor` | 因子配置 | 因子编号、因子名称、因子层级 |
| 评估看板 | `evaluate_board` | 看板配置 | 看板编号、看板名称、关联实验 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## V014：数据导出分片清单与异常检测联动

数据导出按任务分片生成「导出文件清单」（`export_manifest`），每个分片是独立执行单元：

- **独立成败**：单个分片失败或取消不会中断同批其余分片，清单里分别显示成功、失败、已取消。
- **整批统一提交**：`POST /api/data_export/manifests/batch` 一次提交同一批次的多条清单，整组结果逐条记录。
- **原子落库**：分片完成标记与文件元数据（file_name/file_path/file_size/checksum/content_type）
  在同一事务内写入，缺任一元数据整批回滚，清单不会出现“已完成但无法下载”的条目。
- **批次重试**：`POST /api/data_export/tasks/{id}/retry` 只重置失败/取消/待处理分片，已完成文件永不覆盖。
- **单分片取消的两种策略**（`POST /api/data_export/manifests/{id}/cancel`）：
  - `keep_success`：保留成功分片，取消项单独记录；
  - `retry_batch`：整批转「待重试」、撤回已发布文件、保留原分片边界。
- **乐观锁**：分片动作可带 `expected_version`，版本冲突直接拒绝，避免并发覆盖。
- **未清残留**：清单处理发现“已规划未统一提交”的半成品时按 V014 回滚事务，并写入
  `v014_transaction_archive` 留档表（`GET /api/data_export/manifests/archive` 可核对）。
- **跨模块联动**：异常检测通过 `GET /api/anomaly_detect/signals` 读取信号表，
  `POST /api/anomaly_detect/reconcile/export/{task_id}` 读取清单中的信号编号，
  逐分片核对告警范围；数据导出侧 `POST /api/data_export/tasks/{id}/reconcile` 同口径复核。
- **前后端同数据**：工作台与详情抽屉共用 `useExportManifests` 这一份接口数据。

领域：数据工程
子方向：数据分析与数据应用
