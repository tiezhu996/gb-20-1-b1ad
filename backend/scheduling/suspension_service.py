"""教室临时停用处置：受影响课程计算、替代教室匹配与一次性写入。"""
from collections import defaultdict
from datetime import date, timedelta

from django.utils import timezone

from core.models import Classroom
from .models import ScheduleEntry, ClassroomSuspension, ClassroomSuspensionItem


def parse_holidays(semester) -> set:
    """学期节假日兼容两种格式：['2025-10-01'] 或 [{'date': '2025-10-01', ...}]"""
    holidays = set()
    for item in (semester.holidays or []):
        value = item.get('date') if isinstance(item, dict) else item
        if isinstance(value, str):
            try:
                holidays.add(date.fromisoformat(value))
            except ValueError:
                continue
    return holidays


def affected_weekdays(suspension) -> set:
    """停用区间（与学期取交集、剔除节假日）内实际上课的星期几集合，周一=1"""
    semester = suspension.semester
    start = max(suspension.start_date, semester.start_date)
    end = min(suspension.end_date, semester.end_date)
    days = set()
    if start > end:
        return days
    holidays = parse_holidays(semester)
    current = start
    while current <= end:
        if current not in holidays:
            days.add(current.weekday() + 1)
        current += timedelta(days=1)
    return days


def get_affected_entries(suspension):
    """停用教室在停用期内的受影响课表条目（已停课的除外）"""
    days = affected_weekdays(suspension)
    if not days:
        return ScheduleEntry.objects.none()
    return (
        ScheduleEntry.objects
        .filter(
            semester=suspension.semester,
            classroom=suspension.classroom,
            day_of_week__in=days,
            is_suspended=False,
        )
        .select_related('class_id', 'course', 'teacher', 'classroom')
        .order_by('day_of_week', 'period', 'id')
    )


def _other_suspended_classroom_ids(suspension) -> set:
    """停用窗口与本单重叠的其他未恢复停用单所占用的教室"""
    return set(
        ClassroomSuspension.objects
        .exclude(status='restored')
        .exclude(id=suspension.id)
        .filter(
            start_date__lte=suspension.end_date,
            end_date__gte=suspension.start_date,
        )
        .values_list('classroom_id', flat=True)
    )


def compute_suspension_plan(suspension):
    """
    为停用单计算处置方案，返回 (plan_items, blocking_reasons)。

    plan_items: [{'entry', 'action', 'new_classroom', 'note'}]
    - 锁定课程不能改动 -> blocked，整批阻断
    - 同类型、容量足够且该时段无教室/教师/班级冲突 -> relocate
    - 无可用替代教室 -> suspend（停课）
    """
    semester = suspension.semester
    classroom = suspension.classroom
    entries = list(get_affected_entries(suspension))

    # 现有课表占用（排除本次受影响条目与已停课条目）
    affected_ids = {e.id for e in entries}
    room_usage = defaultdict(set)     # classroom_id -> {(day, period)}
    teacher_usage = defaultdict(set)  # teacher_id -> {(day, period)}
    class_usage = defaultdict(set)    # class_id -> {(day, period)}
    others = (
        ScheduleEntry.objects
        .filter(semester=semester, is_suspended=False)
        .exclude(id__in=affected_ids)
        .values('classroom_id', 'teacher_id', 'class_id', 'day_of_week', 'period')
    )
    for row in others:
        slot = (row['day_of_week'], row['period'])
        room_usage[row['classroom_id']].add(slot)
        teacher_usage[row['teacher_id']].add(slot)
        class_usage[row['class_id']].add(slot)

    suspended_room_ids = _other_suspended_classroom_ids(suspension)

    plan_items = []
    blocking = []
    planned_usage = defaultdict(set)  # 本批次内已计划占用的教室时段

    for entry in entries:
        slot = (entry.day_of_week, entry.period)
        label = (f"{entry.class_id.name}《{entry.course.name}》"
                 f"（周{entry.day_of_week}第{entry.period}节）")
        base = {'entry': entry, 'original_classroom': entry.classroom}

        if entry.is_locked:
            reason = f"{label} 为锁定课程，不能改动"
            blocking.append(reason)
            plan_items.append({
                **base, 'action': 'blocked',
                'new_classroom': None, 'note': reason,
            })
            continue

        if slot in teacher_usage[entry.teacher_id] or slot in class_usage[entry.class_id_id]:
            reason = f"{label} 的教师或班级在该时段另有课程，存在冲突，无法安置"
            blocking.append(reason)
            plan_items.append({
                **base, 'action': 'blocked',
                'new_classroom': None, 'note': reason,
            })
            continue

        required = entry.class_id.student_count or 0
        candidates = (
            Classroom.objects
            .filter(
                is_active=True,
                room_type=classroom.room_type,
                capacity__gte=required,
            )
            .exclude(id=classroom.id)
            .exclude(id__in=suspended_room_ids)
            .order_by('capacity', 'name')
        )
        chosen = None
        for cand in candidates:
            if slot in room_usage[cand.id] or slot in planned_usage[cand.id]:
                continue
            chosen = cand
            break

        if chosen is not None:
            planned_usage[chosen.id].add(slot)
            plan_items.append({
                **base, 'action': 'relocate',
                'new_classroom': chosen,
                'note': f"调整至替代教室 {chosen.name}",
            })
        else:
            plan_items.append({
                **base, 'action': 'suspend',
                'new_classroom': None,
                'note': '无同类型且容量足够的可用替代教室，安排停课',
            })

    return plan_items, blocking


def record_plan(suspension, plan_items):
    """重建处置明细记录（历史原因可追溯）"""
    suspension.items.all().delete()
    ClassroomSuspensionItem.objects.bulk_create([
        ClassroomSuspensionItem(
            suspension=suspension,
            entry=item['entry'],
            action=item['action'],
            original_classroom=item['original_classroom'],
            new_classroom=item['new_classroom'],
            note=item['note'],
        )
        for item in plan_items
    ])


def apply_suspension(suspension):
    """
    确认停用：重算方案并一次性写入替代安排或停课记录。
    任一课程无法安置时整批保持原课表并记录阻断原因。
    须在事务内调用，返回 (plan_items, blocking_reasons)。
    """
    plan_items, blocking = compute_suspension_plan(suspension)

    if blocking:
        suspension.status = 'blocked'
        suspension.blocking_reason = '；'.join(blocking)
        suspension.save(update_fields=['status', 'blocking_reason', 'updated_at'])
        record_plan(suspension, plan_items)
        return plan_items, blocking

    reason = suspension.reason
    for item in plan_items:
        entry = item['entry']
        if item['action'] == 'relocate':
            old_name = entry.classroom.name
            new_room = item['new_classroom']
            entry.classroom = new_room
            entry.schedule_note = f"教室临时停用（{reason}）：{old_name} → {new_room.name}"
            entry.save(update_fields=['classroom', 'schedule_note', 'updated_at'])
        elif item['action'] == 'suspend':
            entry.is_suspended = True
            entry.schedule_note = f"教室临时停用停课（{reason}）"
            entry.save(update_fields=['is_suspended', 'schedule_note', 'updated_at'])

    record_plan(suspension, plan_items)
    suspension.status = 'applied'
    suspension.blocking_reason = ''
    suspension.applied_at = timezone.now()
    suspension.save(update_fields=['status', 'blocking_reason', 'applied_at', 'updated_at'])
    return plan_items, blocking
