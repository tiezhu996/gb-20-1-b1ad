"""教室临时停用的核心业务逻辑：

- 计算停用日期区间覆盖的星期几及受影响课程；
- 为每条受影响课程匹配同类型、容量足够、且不产生教师/班级/教室冲突
  的替代教室；
- 确认时整批落库（任一条无法安置则整批回滚）；
- 向 CSP 排课器提供停用教室的禁用时段。
"""
from collections import defaultdict
from datetime import timedelta
from typing import Dict, List, Optional, Set, Tuple

from core.models import Classroom
from .csp_solver import TimeSlot
from .models import ClassroomSuspension, ClassroomSuspensionDisposition, ScheduleEntry


def suspension_weekdays(start_date, end_date) -> Set[int]:
    """停用区间内出现过的星期几（isoweekday：1=周一 ... 7=周日）。"""
    weekdays = set()
    current = start_date
    while current <= end_date:
        weekdays.add(current.isoweekday())
        current += timedelta(days=1)
    return weekdays


def get_affected_entries(suspension: ClassroomSuspension) -> List[ScheduleEntry]:
    """停用区间对应的星期几在该教室上课的全部排课条目。"""
    weekdays = suspension_weekdays(suspension.start_date, suspension.end_date)
    return list(
        ScheduleEntry.objects.filter(
            semester=suspension.semester,
            classroom=suspension.classroom,
            day_of_week__in=weekdays,
        )
        .select_related('class_id', 'course', 'teacher', 'classroom')
        .order_by('day_of_week', 'period', 'id')
    )


def classroom_is_suspended_on(
    classroom_id: int,
    semester_id: int,
    day_of_week: int,
    exclude_suspension_id: Optional[int] = None,
) -> Optional[ClassroomSuspension]:
    """该教室在指定学期的指定星期几是否存在生效中的停用。"""
    qs = ClassroomSuspension.objects.filter(
        classroom_id=classroom_id,
        semester_id=semester_id,
        status='applied',
    )
    if exclude_suspension_id is not None:
        qs = qs.exclude(id=exclude_suspension_id)
    for suspension in qs.select_related('classroom'):
        if day_of_week in suspension_weekdays(suspension.start_date, suspension.end_date):
            return suspension
    return None


def blocked_slots_for_semester(semester) -> Dict[int, Set[TimeSlot]]:
    """供 CSP 排课器使用：{教室ID: {停用时段}}。

    停用只影响区间覆盖到的星期几，其余星期几教室仍可排课；
    已恢复的停用记录不再产生禁用时段。
    """
    daily_periods = len(semester.daily_periods) if semester.daily_periods else 7
    blocked: Dict[int, Set[TimeSlot]] = defaultdict(set)
    suspensions = ClassroomSuspension.objects.filter(
        semester=semester, status='applied'
    )
    for suspension in suspensions:
        weekdays = suspension_weekdays(suspension.start_date, suspension.end_date)
        for day in weekdays:
            if day > semester.weekly_days:
                continue
            for period in range(1, daily_periods + 1):
                blocked[suspension.classroom_id].add(TimeSlot(day=day, period=period))
    return blocked


def _teacher_class_conflict(entry: ScheduleEntry, slot_entries: List[ScheduleEntry]) -> Optional[str]:
    """保持时间不变只换教室时，该条目是否已存在教师或班级冲突。"""
    teacher_conflict = any(
        other.id != entry.id and other.teacher_id == entry.teacher_id
        for other in slot_entries
    )
    class_conflict = any(
        other.id != entry.id and other.class_id_id == entry.class_id_id
        for other in slot_entries
    )
    if teacher_conflict:
        return f"教师 {entry.teacher.name} 在该时段已有其他课程"
    if class_conflict:
        return f"班级 {entry.class_id.name} 在该时段已有其他课程"
    return None


def candidate_classrooms_for(
    suspension: ClassroomSuspension,
    entry: ScheduleEntry,
    occupancy: Set[Tuple[int, int, int]],
    batch_usage: Set[Tuple[int, int, int]],
) -> List[Classroom]:
    """匹配替代教室：同类型、容量足够、启用、自身未停用、该时段空闲。"""
    required_capacity = entry.class_id.student_count or 0
    classrooms = (
        Classroom.objects.filter(
            is_active=True,
            room_type=suspension.classroom.room_type,
            capacity__gte=required_capacity,
        )
        .exclude(id=suspension.classroom_id)
        .order_by('capacity', 'name', 'id')
    )
    candidates = []
    for room in classrooms:
        if classroom_is_suspended_on(
            room.id, suspension.semester_id, entry.day_of_week,
            exclude_suspension_id=suspension.id,
        ):
            continue
        slot_key = (room.id, entry.day_of_week, entry.period)
        if slot_key in occupancy or slot_key in batch_usage:
            continue
        candidates.append(room)
    return candidates


def compute_plan(
    suspension: ClassroomSuspension,
    entries: Optional[List[ScheduleEntry]] = None,
    assignments: Optional[Dict[int, int]] = None,
) -> Tuple[List[dict], List[str]]:
    """计算整批安置方案（只读，不落库）。

    返回 (plan_items, blocking_reasons)：任一受影响课程无法安置时，
    blocking_reasons 非空，调用方应整批保持原课表。
    assignments 可指定 entry_id -> new_classroom_id 覆盖自动匹配结果。
    """
    assignments = assignments or {}
    if entries is None:
        entries = get_affected_entries(suspension)

    affected_ids = {entry.id for entry in entries}
    all_entries = list(
        ScheduleEntry.objects.filter(semester=suspension.semester)
        .select_related('teacher', 'class_id', 'course')
    )
    by_slot: Dict[Tuple[int, int], List[ScheduleEntry]] = defaultdict(list)
    for other in all_entries:
        by_slot[(other.day_of_week, other.period)].append(other)

    # 其他教室已有的占用（受影响条目都在停用教室内，故整体取集即可）
    occupancy = {
        (other.classroom_id, other.day_of_week, other.period)
        for other in all_entries
        if other.id not in affected_ids
    }

    batch_usage: Set[Tuple[int, int, int]] = set()
    plan_items: List[dict] = []
    blocking_reasons: List[str] = []

    ordered = sorted(entries, key=lambda e: (e.day_of_week, e.period, e.id))
    # 教务员显式选择的教室优先占位，其余条目自动匹配
    ordered.sort(key=lambda e: 0 if e.id in assignments else 1)

    for entry in ordered:
        item: dict = {
            'entry': entry,
            'candidates': [],
            'chosen_classroom_id': None,
            'block_reason': None,
        }

        if entry.is_locked:
            item['block_reason'] = (
                f"{entry.course.name}（{entry.class_id.name}，周{entry.day_of_week}"
                f"第{entry.period}节）为锁定课程，不能改动"
            )
            blocking_reasons.append(item['block_reason'])
            plan_items.append(item)
            continue

        conflict_reason = _teacher_class_conflict(
            entry, by_slot[(entry.day_of_week, entry.period)]
        )
        if conflict_reason:
            item['block_reason'] = (
                f"{entry.course.name}（{entry.class_id.name}，周{entry.day_of_week}"
                f"第{entry.period}节）无法安置：{conflict_reason}"
            )
            blocking_reasons.append(item['block_reason'])
            plan_items.append(item)
            continue

        candidates = candidate_classrooms_for(
            suspension, entry, occupancy, batch_usage
        )
        item['candidates'] = candidates

        chosen_id = assignments.get(entry.id)
        if chosen_id is not None:
            chosen_room = next((room for room in candidates if room.id == chosen_id), None)
            if chosen_room is None:
                item['block_reason'] = (
                    f"{entry.course.name}（{entry.class_id.name}）指定的替代教室不可用："
                    "需同类型、容量足够且该时段空闲"
                )
                blocking_reasons.append(item['block_reason'])
                plan_items.append(item)
                continue
            item['chosen_classroom_id'] = chosen_room.id
            batch_usage.add((chosen_room.id, entry.day_of_week, entry.period))
        elif candidates:
            item['chosen_classroom_id'] = candidates[0].id
            batch_usage.add((candidates[0].id, entry.day_of_week, entry.period))
        else:
            item['block_reason'] = (
                f"{entry.course.name}（{entry.class_id.name}，周{entry.day_of_week}"
                f"第{entry.period}节）无法安置：没有同类型且容量足够的空闲替代教室"
            )
            blocking_reasons.append(item['block_reason'])

        plan_items.append(item)

    # 恢复前端展示所需的稳定排序
    plan_items.sort(key=lambda item: (
        item['entry'].day_of_week, item['entry'].period, item['entry'].id
    ))
    return plan_items, blocking_reasons
