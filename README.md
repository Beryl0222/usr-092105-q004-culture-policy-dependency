# 文化政策依赖协调

本项目为规划执行办公室提供文化政策依赖协调服务：把政策条款的版本、发布与生效时间、地区与主体范围、前置材料、引用依赖、冲突意见、受理事项及最终决定保存为可回溯的关系网络。服务只协调事实与依赖，不替部门作法律解释。

## 领域资料

- `contracts/domain.schema.json`：事件信封与本领域允许的聚合、事件类型。
- `data/sample.json`：一条可用于本地联调的中文样例。
- `src/models.py`：条款、事项、决定、复核标记等状态模型。
- `src/service.py`：事件溯源的协调服务（接入、回放、传播、待复核清单）。
- `src/notice.py`：申请方补正说明与经办页面简报两类视图。
- `src/simulate.py`：修订发布前的影响模拟。
- `tests/`：信封约定与业务场景测试。

事件由 `event_id` 唯一标识，`aggregate_id` 指向业务对象，`version` 从 1 开始递增，`occurred_at` 保留真实发生时间。来源系统重试时必须沿用原事件标识，重复投递幂等。

## 事件接入约定

所有事件沿用公共信封，业务内容放在 `payload` 中：

| 事件类型 | 聚合 | payload.action | 关键字段 |
| --- | --- | --- | --- |
| POLICY_PUBLISHED | policy_clause | publish / amend | title、owning_agency、region_scope、subject_scope、materials、effective_from |
| POLICY_PUBLISHED | policy_clause | postpone | new_effective_from |
| POLICY_PUBLISHED | policy_clause | repeal | repealed_at、replaced_by（可选） |
| POLICY_PUBLISHED | policy_clause | transition | transition.cutoff、transition.note |
| DEPENDENCY_DECLARED | dependency_edge | declare / retract | from_clause、to_clause、kind（cites/requires/preempts） |
| OPINION_ISSUED | agency_opinion | — | agency、clause_id、text、visibility（public/internal） |
| CONFLICT_RAISED | agency_opinion | raise / resolve | clause_id、agencies、topic、resolution |
| CASE_REVIEWED | application_case | accept | applicant、region、subject、clause_ids、materials_provided、accepted_at |
| CASE_REVIEWED | application_case | decide / close | decision、decided_by、decided_at |

## 行为规则

- **迟到版本**：事件按 `occurred_at` 归位后整体重放，迟到修订会自动修正后续决定的依据版本与版本历史。
- **真实依赖传播**：修订或废止只对同时满足"依赖闭包 ∩ 地区与主体范围 ∩ 受理早于变动"的事项打复核标记；无依赖、范围不符的事项不受打扰。
- **已生效决定**：依据版本快照永久保留，条款变动只把决定标记为待复核，不改写原依据。
- **过渡安排**：受理时间早于过渡截止日的事项按旧规则办理，只收到过渡提醒，不进入复核。
- **延期**：生效时间延期只向真实依赖的在办事项发提醒，不产生复核标记。
- **两类视图**：`applicant_notice` 只含公开事实（所缺材料、公开意见、复核事由），不夹带内部会商与部门冲突；`caseworker_briefing` 直接列出责任部门、所缺条件、未决冲突与全部意见。
- **发布前模拟**：`simulate_revision(service, draft_events)` 在事件日志副本上回放假想的修订/废止/延期/过渡事件，返回受影响地区、将进入复核的在办事项、将待复核的已生效决定及受过渡保护的事项，正式状态不受影响。

## 本地检查

运行 `python3 -m unittest discover -s tests`。
