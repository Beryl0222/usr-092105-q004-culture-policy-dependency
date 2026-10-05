# 领域事件目录

所有跨部门交换的事实都封装在 `contracts/domain.schema.json` 的事件信封中，业务字段放在 `payload`。
约定：

- `event_id` 全局唯一；来源系统重试必须沿用原标识，服务按原标识幂等接收。
- `version` 在**同一聚合内**从 1 开始递增；迟到补录的事件沿用其真实发生时取得的版本号，服务按 `occurred_at` 重放，不按入库顺序。
- `occurred_at` 填事实发生时间；`recorded_at` 由服务入库时填写。
- 协调服务自身派生的标记事件（复核、过渡适用）带 `"origin": "coordinator"`，可在迟到事件重放后安全重建；部门事实事件不带该字段，不会被服务删除。

## policy_clause 政策条款

### POLICY_PUBLISHED 发布（含新版本）
```json
{
  "clause_id": "CL-TRADE",
  "document_no": "杭文旅贸〔2024〕3号",
  "title": "地方文化贸易促进办法·数字展演支持条款",
  "owner_agency": "WHJ",
  "version": 1,
  "effective_at": "2024-03-01T00:00:00+08:00",
  "expires_at": "2026-12-31T00:00:00+08:00",
  "regions": ["330100"],
  "subjects": ["文化企业"],
  "matter_codes": ["MT-DIGITAL-EXHIBITION"],
  "required_materials": [
    {"code": "M-LICENSE", "name": "营业执照", "responsible_agency": "SCJRG", "requirement": "有效营业执照复印件"},
    {"code": "M-PLAN", "name": "数字展演方案", "responsible_agency": "WHJ", "requirement": "含技术路线与预算"}
  ],
  "supersedes_version": null
}
```
新版本再次 POLICY_PUBLISHED 时 `version` 递增、`supersedes_version` 指向旧版本；`regions` 支持 `"*"` 表示全域。

### POLICY_EXTENDED 延期
`payload`：`{"base_version": 1, "new_expires_at": "...", "reason": "..."}`。延期只影响有效期窗口，本身不产生补正/复核传播。

### POLICY_REPEALED 废止
`payload`：
```json
{
  "target_versions": [1],
  "effective_at": "2026-11-01T00:00:00+08:00",
  "scope": {"regions": ["330100"], "subjects": ["文化企业"], "matter_codes": ["MT-DIGITAL-EXHIBITION"]},
  "successor": {"clause_id": "CL-TRADE", "version": 2},
  "decisions_review": "GRANDFATHER",
  "note": "内部备注，不进入申请方视图"
}
```
`target_versions` 可取 `["*"]`；无后继时 `successor` 为 null。`decisions_review`：`GRANDFATHER`（已生效决定保留原依据、不待复核，默认）或 `REVIEW`（原依据废止且需复核）。

### POLICY_TRANSITION_DECLARED 过渡安排
```json
{
  "target_versions": [1],
  "rule": "CONTINUE_OLD_RULES",
  "scope": {"regions": ["330100"], "subjects": ["文化企业"], "matter_codes": ["MT-DIGITAL-EXHIBITION"]},
  "accepted_before": "2026-09-30T23:59:59+08:00",
  "cutoff_date": "2026-12-31T23:59:59+08:00",
  "successor_version": 2,
  "decisions_policy": "GRANDFATHER",
  "note": "会商口径，内部可见",
  "public_note": "2026年9月30日前已受理的数字展演项目，可于2026年底前按原办法办理。"
}
```
`rule`：`CONTINUE_OLD_RULES`（按旧规则办理）、`SUPPLEMENT_UNDER_NEW`（按新版本补材料）、`REVIEW_BEFORE_DATE`（截止日前完成复核）。

## dependency_edge 引用依赖

### DEPENDENCY_DECLARED
`payload`：
```json
{
  "edge_id": "EDGE-1001-TRADE",
  "kind": "CASE_CLAUSE",
  "case_id": "CASE-1001",
  "matter_code": "MT-DIGITAL-EXHIBITION",
  "clause_id": "CL-TRADE",
  "clause_version": 1,
  "required_material_codes": ["M-LICENSE", "M-PLAN", "M-TECH-PROOF"],
  "responsible_agency": "WHJ"
}
```
`kind`：`CASE_CLAUSE`（在办事项对条款的适用依赖，传播的主路径）、`CLAUSE_CLAUSE`（条款间引用，仅用于关系网络展示，不直接触发对办件的传播）。

### DEPENDENCY_REBASED 依赖改挂
`payload`：`{"new_clause_id": "CL-TRADE", "new_version": 2, "reason": "..."}`。

## application_case 受理事项

- CASE_ACCEPTED：`applicant/project_name/region/subject_type/matter_code/tags/accepted_at/materials[{code,name,status,responsible_agency}]/basis[{clause_id,version}]`
- CASE_SUPPLEMENT_REQUESTED：`request_id/material_codes/deadline/public_reason/handler_agency`（申请方视图只取白名单字段，payload 中夹带的 `note`、`internal_*` 一律不下发）
- CASE_MATERIAL_RESUBMITTED：`request_id/material_codes`
- CASE_DECIDED：`outcome/decided_at/basis[{clause_id,version,document_no,title}]/note`，决定快照固化原依据
- CASE_REVIEWED / CASE_TRANSITION_APPLIED：通常由协调服务派生（`origin: "coordinator"`），也允许部门手工发起
- CASE_REVIEW_RESOLVED：`review_key/resolution`，外部事实，优先级高于派生状态

## agency_opinion 部门意见与会商冲突

- OPINION_ISSUED：`opinion_id/agency/position（AGREE|OBJECT|INTERPRETATION）/clause_id/clause_version/case_id?/content/visibility`
- CONFLICT_RAISED：`topic/opinions[opinion_id]/raised_by/note`
- CONFLICT_CLEARED：`topic/resolution`

意见与冲突默认 `INTERNAL`，**只进经办页面，永不进入申请方补正说明**。
