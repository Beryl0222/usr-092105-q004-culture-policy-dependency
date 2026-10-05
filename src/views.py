"""双口径投影：申请方补正说明（脱敏白名单）与经办页面（全量内部信息）。

另提供关系网络导出，保证条款版本、依赖、意见、决定之间可回溯。
"""

from .propagation import assess_case_edges

# 申请方补正说明允许出现的材料字段白名单；其余一律过滤。
_APPLICANT_MATERIAL_FIELDS = ("code", "name", "requirement", "source", "status")


def _impact_index(state) -> dict[str, list]:
    idx: dict[str, list] = {}
    for im in assess_case_edges(state):
        idx.setdefault(im.case_id, []).append(im)
    return idx


# ============================================================ 申请方口径

def applicant_notice(state, case_id: str) -> dict:
    """生成给申请方的补正/过渡说明。

    硬性规则：只取白名单字段；部门意见、会商冲突、内部备注、复核键、触发事件 ID
    等内部信息在此函数内根本不被读取，杜绝夹带。
    """
    case = state.cases.get(case_id)
    if case is None:
        return {"case_id": case_id, "found": False}

    notice = {
        "found": True,
        "case_id": case_id,
        "project_name": case.get("project_name"),
        "matter_code": case.get("matter_code"),
        "items": [],
        "messages": [],
    }

    # 1) 正式发出的补正请求（部门事实）：只取可公开字段
    for req in case.get("supplements", []):
        resubmitted = set(req.get("resubmitted") or [])
        for code in req["material_codes"]:
            if code in resubmitted:
                continue
            mat = case["materials"].get(code, {})
            notice["items"].append(_public_material(code, mat))
        if req.get("deadline"):
            notice["messages"].append(f"请于 {req['deadline']} 前补齐材料。")
        if req.get("public_reason"):
            notice["messages"].append(req["public_reason"])

    # 2) 协调引擎识别出的过渡/新版本补正：只使用材料白名单与公开说明
    for im in assess_case_edges(state):
        if im.case_id != case_id or not im.is_real:
            continue
        if im.action in ("SUPPLEMENT_UNDER_NEW", "REVIEW_NEW_VERSION"):
            for m in im.missing_materials:
                notice["items"].append({
                    "code": m["code"], "name": m.get("name"),
                    "requirement": m.get("requirement"), "source": m.get("source"),
                })
            t = im.transition or {}
            if t.get("public_note"):
                notice["messages"].append(t["public_note"])
            elif im.scheduled and im.effective_at:
                notice["messages"].append(
                    f"您的事项所依据的办法将于 {im.effective_at} 起按修订后的要求办理，"
                    "请提前对照修订后的材料清单做好准备，正式补正通知将由受理部门另行发出。"
                )
            else:
                notice["messages"].append("因所依据的办法已修订，请按修订后的要求补齐材料。")
        elif im.action == "CONTINUE_OLD_RULES":
            t = im.transition or {}
            msg = t.get("public_note") or "您的事项符合过渡安排，按原办法继续办理。"
            if t.get("cutoff_date"):
                msg += f"（办理截止 {t['cutoff_date']}）"
            notice["messages"].append(msg)
        elif im.action == "REVIEW_NO_SUCCESSOR":
            notice["messages"].append("您的事项所依据的规定已调整，受理部门将另行告知后续安排。")
        # REVIEW_DECIDED_BASIS / TRANSITION_REVIEW_BEFORE_DATE 等内部复核不直接对申请方措辞

    # 去重
    seen = set()
    deduped = []
    for it in notice["items"]:
        if it["code"] not in seen:
            seen.add(it["code"])
            deduped.append({k: v for k, v in it.items() if k in _APPLICANT_MATERIAL_FIELDS})
    notice["items"] = deduped
    notice["messages"] = list(dict.fromkeys(notice["messages"]))
    return notice


def _public_material(code: str, mat: dict) -> dict:
    return {
        "code": code,
        "name": mat.get("name"),
        "requirement": mat.get("requirement"),
        "status": "PENDING" if mat.get("status", "RECEIVED") != "RECEIVED" else "RECEIVED",
    }


# ============================================================ 经办口径

def staff_workbench(state, case_id: str) -> dict:
    """经办页面：直接指出责任部门、所缺条件、冲突意见与复核路径（内部全量）。"""
    case = state.cases.get(case_id)
    if case is None:
        return {"case_id": case_id, "found": False}

    impacts = [im for im in assess_case_edges(state) if im.case_id == case_id and im.is_real]
    edges = [e for e in state.edges.values() if e.get("case_id") == case_id and e["kind"] == "CASE_CLAUSE"]

    missing = []
    for code, mat in case["materials"].items():
        if mat.get("status") != "RECEIVED":
            missing.append({
                "code": code, "name": mat.get("name"), "requirement": mat.get("requirement"),
                "responsible_agency": mat.get("responsible_agency"),
                "reason": "受理材料未提交",
            })
    for im in impacts:
        for m in im.missing_materials:
            if not any(x["code"] == m["code"] for x in missing):
                missing.append({
                    "code": m["code"], "name": m.get("name"), "requirement": m.get("requirement"),
                    "responsible_agency": m.get("responsible_agency"),
                    "reason": m.get("source", "依据修订后新增条件"),
                })

    # 责任部门归并：边声明部门、条款主管部门、各材料责任部门
    agencies = set()
    basis = []
    for edge in edges:
        if edge.get("responsible_agency"):
            agencies.add(edge["responsible_agency"])
        ver = state.clause_version(edge["clause_id"], edge["clause_version"])
        if ver:
            agencies.add(ver["owner_agency"])
            basis.append({
                "clause_id": edge["clause_id"], "version": edge["clause_version"],
                "document_no": ver.get("document_no"), "title": ver.get("title"),
                "status": ver["status"],
                "repealed_at": (ver.get("repeal") or {}).get("at") if ver.get("repeal") else None,
                "successor": (ver.get("repeal") or {}).get("successor") if ver.get("repeal") else None,
                "expires_at": ver.get("expires_at"),
            })
    for m in missing:
        if m.get("responsible_agency"):
            agencies.add(m["responsible_agency"])

    related_conflicts = _related_conflicts(state, case, edges, impacts)

    decision = None
    if case.get("decision"):
        decision = dict(case["decision"])
        decision_review = [
            {
                "review_key": im.review_key, "reason": im.reason, "action": im.action,
                "resolved": im.review_resolved,
                "successor_version": im.successor_version,
            }
            for im in impacts if im.action in ("REVIEW_DECIDED_BASIS", "TRANSITION_REVIEW_BEFORE_DATE")
        ]
        decision["reviews"] = decision_review
        decision["basis_preserved"] = True  # 原依据快照永不改写

    return {
        "found": True,
        "case_id": case_id,
        "applicant": case.get("applicant"),
        "project_name": case.get("project_name"),
        "region": case.get("region"),
        "subject_type": case.get("subject_type"),
        "matter_code": case.get("matter_code"),
        "case_status": case["status"],
        "responsible_agencies": sorted(a for a in agencies if a),
        "basis": basis,
        "missing_conditions": missing,
        "impacts": [
            {
                "kind": im.kind, "action": im.action, "reason": im.reason,
                "review_key": im.review_key, "review_resolved": im.review_resolved,
                "scheduled": im.scheduled, "effective_at": im.effective_at,
                "successor_version": im.successor_version,
                "transition_rule": im.transition["rule"] if im.transition else None,
                "cutoff_date": im.transition.get("cutoff_date") if im.transition else None,
                "internal_note": im.transition.get("note") if im.transition else (im.repeal or {}).get("note"),
                "trigger_event_id": im.trigger_event_id,
            }
            for im in impacts
        ],
        "conflicts": related_conflicts,
        "transition_applied": case.get("transition_applied"),
        "decision": decision,
    }


def _related_conflicts(state, case, edges, impacts) -> list[dict]:
    # 同一条款的任一版本（含后继新版本）上的会商冲突，都与该办件相关
    clause_family = {e["clause_id"] for e in edges}
    for im in impacts:
        clause_family.add(im.clause_id)
    out = []
    for topic, conf in state.conflicts.items():
        opinions = [state.opinions[oid] for oid in conf["opinions"] if oid in state.opinions]
        relevant = any(
            op.get("clause_id") in clause_family or op.get("case_id") == case["case_id"]
            for op in opinions
        )
        if not relevant:
            continue
        out.append({
            "topic": topic,
            "resolved": conf.get("resolution") is not None,
            "resolution": conf.get("resolution"),
            "raised_by": conf.get("raised_by"),
            "note": conf.get("note"),
            "opinions": [
                {"agency": op["agency"], "position": op["position"], "content": op["content"]}
                for op in opinions
            ],
        })
    return out


# ============================================================ 关系网络

def network(state) -> dict:
    """导出可回溯关系网络：条款版本节点、办件节点、部门意见节点与各类边。"""
    nodes = []
    edges_out = []

    for cid, clause in state.clauses.items():
        for vno, ver in clause["versions"].items():
            nodes.append({
                "id": f"clause:{cid}:v{vno}", "type": "policy_clause_version",
                "label": f"{ver.get('title') or cid} v{vno}",
                "document_no": ver.get("document_no"), "status": ver["status"],
                "effective_at": ver.get("effective_at"), "expires_at": ver.get("expires_at"),
                "owner_agency": ver.get("owner_agency"),
                "regions": ver.get("regions"), "matter_codes": ver.get("matter_codes"),
            })
            if ver.get("supersedes_version"):
                edges_out.append({"from": f"clause:{cid}:v{vno}", "to": f"clause:{cid}:v{ver['supersedes_version']}",
                                  "type": "SUPERSEDES"})
            if ver.get("repeal") and ver["repeal"].get("successor"):
                succ = ver["repeal"]["successor"]
                edges_out.append({"from": f"clause:{succ.get('clause_id', cid)}:v{succ['version']}",
                                  "to": f"clause:{cid}:v{vno}", "type": "SUCCEEDS_REPEALED"})
            for t in ver.get("transitions", []):
                if t.get("successor_version"):
                    edges_out.append({"from": f"clause:{cid}:v{vno}",
                                      "to": f"clause:{cid}:v{t['successor_version']}",
                                      "type": "TRANSITION_TO", "rule": t["rule"]})

    for case_id, case in state.cases.items():
        nodes.append({
            "id": f"case:{case_id}", "type": "application_case",
            "label": case.get("project_name") or case_id,
            "status": case["status"], "region": case.get("region"),
            "matter_code": case.get("matter_code"),
        })
        if case.get("decision"):
            for b in case["decision"]["basis"]:
                edges_out.append({"from": f"case:{case_id}",
                                  "to": f"clause:{b['clause_id']}:v{b['version']}",
                                  "type": "DECIDED_ON", "outcome": case["decision"]["outcome"]})

    for edge in state.edges.values():
        rel = {"from": f"case:{edge['case_id']}", "to": f"clause:{edge['clause_id']}:v{edge['clause_version']}",
               "type": edge["kind"], "edge_id": edge["edge_id"],
               "responsible_agency": edge.get("responsible_agency")}
        edges_out.append(rel)

    for oid, op in state.opinions.items():
        nodes.append({"id": f"opinion:{oid}", "type": "agency_opinion",
                      "label": f"{op['agency']}·{op['position']}", "visibility": op.get("visibility")})
        if op.get("clause_id") and op.get("clause_version") is not None:
            edges_out.append({"from": f"opinion:{oid}",
                              "to": f"clause:{op['clause_id']}:v{op['clause_version']}", "type": "OPINES_ON"})
        if op.get("case_id"):
            edges_out.append({"from": f"opinion:{oid}", "to": f"case:{op['case_id']}", "type": "OPINES_ON"})

    return {"nodes": nodes, "edges": edges_out,
            "counts": {"nodes": len(nodes), "edges": len(edges_out)}}
