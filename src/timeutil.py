"""时间与范围匹配工具。"""

from datetime import datetime


def parse_dt(value) -> datetime:
    """ISO 8601 -> aware datetime；裸时间按东八区处理。"""
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        from datetime import timezone, timedelta

        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
    return dt


def scope_match(scope: dict | None, *, region: str, subject: str, matter_code: str) -> bool:
    """事件 scope 与办件的地区/主体/事项是否相交；缺省字段视为不限。

    支持 "*" 通配；region 支持前缀（区级编码匹配市级编码，如 330106 命中 330100）。
    """
    if not scope:
        return True

    regions = scope.get("regions")
    if regions:
        if "*" not in regions and not any(_region_hit(region, r) for r in regions):
            return False

    subjects = scope.get("subjects")
    if subjects and "*" not in subjects and subject not in subjects:
        return False

    matters = scope.get("matter_codes")
    if matters and "*" not in matters and matter_code not in matters:
        return False

    return True


def _region_hit(case_region: str, scope_region: str) -> bool:
    if case_region == scope_region:
        return True
    # 行政区划前缀：330100（市）覆盖 3301xx（区），330000（省）覆盖 33xxxx
    if scope_region.endswith("00"):
        return case_region.startswith(scope_region.rstrip("0"))
    return False
