"""把事实事件流重放为四个聚合的当前状态快照。

只消费部门事实事件（origin != "coordinator"）；协调服务派生的复核/过渡事件是
传播引擎的输出，不参与投影，因此迟到事件到达后可以整体安全重建。
"""

from dataclasses import dataclass, field

from .timeutil import parse_dt


@dataclass
class State:
    clauses: dict = field(default_factory=dict)        # clause_id -> 条款聚合
    edges: dict = field(default_factory=dict)          # edge_id -> 依赖边
    cases: dict = field(default_factory=dict)          # case_id -> 受理事项
    opinions: dict = field(default_factory=dict)       # opinion_id -> 部门意见
    conflicts: dict = field(default_factory=dict)      # topic -> 冲突
    events_by_id: dict = field(default_factory=dict)

    @classmethod
    def replay(cls, events: list[dict]) -> "State":
        state = cls()
        for e in sorted(
            (e for e in events if e.get("origin") != "coordinator"),
            key=lambda x: (x["occurred_at"], x.get("_seq", 0)),
        ):
            state._apply(e)
            state.events_by_id[e["event_id"]] = e
        return state

    # ---------- 条款 ----------
    def _apply(self, e: dict) -> None:
        p = e.get("payload") or {}
        et, at = e["event_type"], e["aggregate_type"]
        if at == "policy_clause":
            self._apply_clause(e, p)
        elif at == "dependency_edge":
            self._apply_edge(e, p)
        elif at == "application_case":
            self._apply_case(e, p)
        elif at == "agency_opinion":
            self._apply_opinion(et, p)

    def _apply_clause(self, e: dict, p: dict) -> None:
        et = e["event_type"]
        if et == "POLICY_PUBLISHED":
            cid = p["clause_id"]
            clause = self.clauses.setdefault(cid, {"clause_id": cid, "versions": {}})
            v = int(p["version"])
            clause["versions"][v] = {
                "document_no": p.get("document_no"),
                "title": p.get("title"),
                "owner_agency": p.get("owner_agency"),
                "version": v,
                "published_at": e["occurred_at"],
                "effective_at": p["effective_at"],
                "expires_at": p.get("expires_at"),
                "regions": list(p.get("regions") or []),
                "subjects": list(p.get("subjects") or []),
                "matter_codes": list(p.get("matter_codes") or []),
                "required_materials": list(p.get("required_materials") or []),
                "supersedes_version": p.get("supersedes_version"),
                "status": "EFFECTIVE",
                "repeal": None,
                "transitions": [],
                "extensions": [],
            }
            latest = clause.get("latest_version", 0)
            clause["latest_version"] = max(latest, v)
            return

        cid = e["aggregate_id"]
        clause = self.clauses.get(cid)
        if clause is None:
            return  # 未知条款的后继事件暂挂（理论上校验已拦截）
        if et == "POLICY_EXTENDED":
            ver = clause["versions"].get(int(p["base_version"]))
            if ver:
                ver["expires_at"] = p["new_expires_at"]
                ver["extensions"].append(
                    {"at": e["occurred_at"], "new_expires_at": p["new_expires_at"], "reason": p.get("reason")}
                )
        elif et == "POLICY_REPEALED":
            targets = clause["versions"].keys() if "*" in p["target_versions"] else [int(x) for x in p["target_versions"]]
            for vno in targets:
                ver = clause["versions"].get(vno)
                if ver:
                    ver["status"] = "REPEALED"
                    ver["repeal"] = {
                        "at": p["effective_at"],
                        "scope": p.get("scope"),
                        "successor": p.get("successor"),
                        "decisions_review": p.get("decisions_review", "GRANDFATHER"),
                        "event_id": e["event_id"],
                        "note": p.get("note"),
                    }
        elif et == "POLICY_TRANSITION_DECLARED":
            targets = clause["versions"].keys() if "*" in p["target_versions"] else [int(x) for x in p["target_versions"]]
            for vno in targets:
                ver = clause["versions"].get(vno)
                if ver:
                    ver["transitions"].append(
                        {
                            "at": e["occurred_at"],
                            "rule": p["rule"],
                            "scope": p.get("scope"),
                            "accepted_before": p.get("accepted_before"),
                            "cutoff_date": p.get("cutoff_date"),
                            "successor_version": p.get("successor_version"),
                            "decisions_policy": p.get("decisions_policy", "GRANDFATHER"),
                            "public_note": p.get("public_note"),
                            "note": p.get("note"),
                            "event_id": e["event_id"],
                        }
                    )

    # ---------- 依赖边 ----------
    def _apply_edge(self, e: dict, p: dict) -> None:
        et = e["event_type"]
        if et == "DEPENDENCY_DECLARED":
            self.edges[p["edge_id"]] = {
                "edge_id": p["edge_id"],
                "kind": p["kind"],
                "case_id": p.get("case_id"),
                "matter_code": p.get("matter_code"),
                "clause_id": p["clause_id"],
                "clause_version": int(p["clause_version"]),
                "required_material_codes": list(p.get("required_material_codes") or []),
                "responsible_agency": p.get("responsible_agency"),
                "active": True,
                "declared_at": e["occurred_at"],
                "history": [],
            }
            return
        edge = self.edges.get(e["aggregate_id"])
        if edge is None:
            return
        if et == "DEPENDENCY_REBASED":
            edge["history"].append(
                {"at": e["occurred_at"], "from": [edge["clause_id"], edge["clause_version"]],
                 "to": [p["new_clause_id"], int(p["new_version"])]}
            )
            edge["clause_id"] = p["new_clause_id"]
            edge["clause_version"] = int(p["new_version"])

    # ---------- 受理事项 ----------
    def _apply_case(self, e: dict, p: dict) -> None:
        et = e["event_type"]
        if et == "CASE_ACCEPTED":
            materials = {}
            for m in p.get("materials") or []:
                materials[m["code"]] = dict(m, status=m.get("status", "RECEIVED"))
            self.cases[p["case_id"]] = {
                "case_id": p["case_id"],
                "applicant": p.get("applicant"),
                "project_name": p.get("project_name"),
                "region": p.get("region"),
                "subject_type": p.get("subject_type"),
                "matter_code": p.get("matter_code"),
                "tags": list(p.get("tags") or []),
                "accepted_at": p["accepted_at"],
                "materials": materials,
                "basis": [dict(b) for b in (p.get("basis") or [])],
                "status": "ACCEPTED",
                "supplements": [],
                "decision": None,
                "external_reviews": [],
                "review_resolutions": [],
                "transition_applied": None,
            }
            return

        case = self.cases.get(e["aggregate_id"])
        if case is None:
            return
        if et == "CASE_SUPPLEMENT_REQUESTED":
            case["status"] = "PENDING_SUPPLEMENT"
            req = {
                "request_id": p["request_id"],
                "at": e["occurred_at"],
                "material_codes": list(p["material_codes"]),
                "deadline": p.get("deadline"),
                "public_reason": p.get("public_reason"),
                "handler_agency": p.get("handler_agency"),
                "resubmitted": [],
            }
            case["supplements"].append(req)
            for code in p["material_codes"]:
                if code in case["materials"]:
                    case["materials"][code]["status"] = "PENDING"
                else:
                    case["materials"][code] = {"code": code, "status": "PENDING", "name": None, "responsible_agency": p.get("handler_agency")}
        elif et == "CASE_MATERIAL_RESUBMITTED":
            for req in reversed(case["supplements"]):
                if req["request_id"] == p["request_id"]:
                    req["resubmitted"] = list(p["material_codes"])
                    break
            for code in p["material_codes"]:
                if code in case["materials"]:
                    case["materials"][code]["status"] = "RECEIVED"
            if case["status"] == "PENDING_SUPPLEMENT" and all(
                m["status"] == "RECEIVED" for m in case["materials"].values()
            ):
                case["status"] = "ACCEPTED"
        elif et == "CASE_DECIDED":
            case["status"] = "DECIDED"
            case["decision"] = {
                "outcome": p["outcome"],
                "decided_at": p["decided_at"],
                "basis": [dict(b) for b in (p.get("basis") or [])],
                "note": p.get("note"),
            }
        elif et == "CASE_REVIEWED":
            case["external_reviews"].append(
                {"review_key": p["review_key"], "at": e["occurred_at"], "reason": p["reason"],
                 "detail": {k: v for k, v in p.items() if k not in ("review_key", "reason")}}
            )
        elif et == "CASE_REVIEW_RESOLVED":
            case["review_resolutions"].append(
                {"review_key": p["review_key"], "at": e["occurred_at"], "resolution": p["resolution"],
                 "note": p.get("note")}
            )
        elif et == "CASE_TRANSITION_APPLIED":
            case["transition_applied"] = {
                "at": e["occurred_at"], "clause_id": p["clause_id"],
                "clause_version": p["clause_version"], "rule": p["rule"],
                "note": p.get("note"),
            }

    # ---------- 部门意见 / 冲突 ----------
    def _apply_opinion(self, et: str, p: dict) -> None:
        if et == "OPINION_ISSUED":
            self.opinions[p["opinion_id"]] = {
                "opinion_id": p["opinion_id"],
                "agency": p.get("agency"),
                "position": p.get("position"),
                "clause_id": p.get("clause_id"),
                "clause_version": p.get("clause_version"),
                "case_id": p.get("case_id"),
                "content": p.get("content"),
                "visibility": p.get("visibility", "INTERNAL"),
            }
        elif et == "CONFLICT_RAISED":
            self.conflicts[p["topic"]] = {
                "topic": p["topic"], "opinions": list(p.get("opinions") or []),
                "raised_by": p.get("raised_by"), "note": p.get("note"),
                "resolution": None,
            }
        elif et == "CONFLICT_CLEARED":
            if p["topic"] in self.conflicts:
                self.conflicts[p["topic"]]["resolution"] = p["resolution"]

    # ---------- 查询辅助 ----------
    def clause_version(self, clause_id: str, version: int):
        clause = self.clauses.get(clause_id)
        return clause["versions"].get(int(version)) if clause else None

    def effective_versions(self, clause_id: str, at=None) -> list[int]:
        """某条款在指定时刻（默认当前）处于有效期内且未废止的版本。"""
        from datetime import datetime, timezone
        clause = self.clauses.get(clause_id)
        if not clause:
            return []
        now = at or datetime.now(timezone.utc)
        out = []
        for vno, v in clause["versions"].items():
            if parse_dt(v["effective_at"]) <= now and (not v["expires_at"] or parse_dt(v["expires_at"]) > now) and v["status"] == "EFFECTIVE":
                out.append(vno)
        return sorted(out)
