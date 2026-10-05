# 文化政策依赖协调服务

面向规划执行办公室的跨部门政策依赖协调。系统**不替部门作法律解释**，只做一件事：
把政策条款的版本、发布/生效时间、地区与主体范围、前置材料、引用依赖、冲突意见、
受理事项与最终决定保存为**可回溯的关系网络**，并在政策变动时只沿真实依赖传播后果。

## 解决的问题

- 修订稿到达后，在办项目是**补材料**还是**按旧规则继续办理**，由过渡安排 + 真实依赖自动判定，不再靠群聊逐个问部门。
- 文件废止时，哪些地区、哪些在办事项、哪些既有决定受影响，`simulate` 在发布前给出清单，而不是等投诉后人工翻文件。
- 迟到版本、延期、废止、补录事件按真实发生时间重放，传播结论幂等重建，不重复不遗漏。
- 已生效决定的原依据快照永不改写；是否待复核有明确标记（GRANDFATHER / REVIEW）。
- 给申请方的补正说明与给经办人的页面是两套投影：申请方口径白名单脱敏，内部会商绝不夹带；经办页面直接给出责任部门、所缺条件与冲突意见。

## 模型与事件流

四个聚合沿用既有约定：`policy_clause`、`dependency_edge`、`application_case`、`agency_opinion`。
事件信封与 16 种事件见 [`contracts/domain.schema.json`](contracts/domain.schema.json) 与
[`docs/event-catalog.md`](docs/event-catalog.md)。

```
部门事实事件（POLICY_PUBLISHED / _EXTENDED / _REPEALED / _TRANSITION_DECLARED,
  DEPENDENCY_DECLARED, CASE_*, OPINION_ISSUED, CONFLICT_*）
        │  幂等追加（event_id 重试沿用；version 聚合内递增；occurred_at 真实时间）
        ▼
   EventStore（JSONL 持久化）
        │  只重放事实事件（origin != coordinator）
        ▼
     State 快照 ──► assess_case_edges 传播引擎（scope/时限/边匹配的纯函数）
        │                        │
        │                        ├─► applicant_notice  申请方补正说明（白名单脱敏）
        │                        ├─► staff_workbench   经办页面（责任部门/所缺条件/冲突）
        │                        └─► network           条款版本-办件-意见关系网络
        ▼
 协调派生事件（CASE_TRANSITION_APPLIED / CASE_REVIEWED, origin=coordinator）
   —— 只是结论不是事实，每次 reconcile 确定性重建；迟到补录后可安全重算
```

### 传播规则（要点）

1. **只沿 `CASE_CLAUSE` 依赖边传播**；`CLAUSE_CLAUSE` 只用于网络展示。
2. 过渡安排必须同时匹配 scope（地区/主体/事项，支持行政区划前缀与 `*`）与受理时限；
   `CONTINUE_OLD_RULES` → 按旧规则办理；`SUPPLEMENT_UNDER_NEW` → 仅列出新版本新增且未交的材料；`REVIEW_BEFORE_DATE` → 限期复核。
3. 废止且无适用过渡：在办件进入复核；废止尚未到生效时点时标记为「待生效预警」（`scheduled=true`）。
4. 仅发布新版本或仅延期，旧版仍有效期间**不产生任何传播**。
5. 已决定事项：决定快照保留原依据；仅当废止/过渡明确 `REVIEW` 时才待复核，默认 GRANDFATHER 免复核。
6. 部门可发 `CASE_REVIEW_RESOLVED` 收口复核；事实优先于派生标记。

## 使用

```python
from src.service import CoordinationService
from src import views

svc = CoordinationService("data/store.jsonl")
svc.ingest_many(factual_events)

# 发布前模拟（不落库）
report = svc.simulate([new_version_event, repeal_event, transition_event])["report"]
# -> affected_regions / pending_cases_for_review / decisions_for_review /
#    cases_continue_under_transition / counts

svc.ingest_many(revision_events)          # 正式发布，自动对账
views.applicant_notice(svc.state(), "CASE-1001")   # 申请方口径
views.staff_workbench(svc.state(), "CASE-1001")    # 经办口径
views.network(svc.state())                          # 关系网络
```

## 目录

- `contracts/domain.schema.json`：事件信封与事件类型契约（向后兼容，payload 为扩展字段）。
- `docs/event-catalog.md`：各事件 payload 字段与口径约定。
- `src/validator.py`：信封与 payload 校验（中文错误）。
- `src/store.py`：幂等事件存储、版本冲突检测、JSONL 持久化、派生事件重建。
- `src/state.py`：事实事件重放为四聚合快照。
- `src/propagation.py`：依赖匹配与传播规则（纯函数）、派生事件物化。
- `src/service.py`：接收/对账/发布前模拟。
- `src/views.py`：申请方说明、经办页面、关系网络。
- `data/scenario.jsonl`：数字展演项目完整场景（4 个办件，含跨地区隔离、已决定件、内部冲突夹带）。
- `data/revision_candidates.jsonl`：修订稿三件套（新版发布/废止/过渡）。
- `src/demo.py`：端到端演示，`python3 -m src.demo`。

## 本地检查

```bash
python3 -m unittest discover -s tests
```

关键测试覆盖：幂等重试、版本冲突、发布前模拟不落库、真实依赖范围隔离（宁波件不传播）、
过渡新旧规则分流、已生效决定保留原依据、申请方视图无内部会商泄漏、经办页面定位三个责任部门、
迟到过渡补录后传播自动校正、纯延期不传播、复核收口、关系网络可回溯。
