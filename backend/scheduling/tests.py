from datetime import date

from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Classroom, Teacher, Class, Course, Semester
from .models import (
    ClassCourse, ScheduleEntry, ClassroomSuspension, ClassroomSuspensionItem
)
from .suspension_service import affected_weekdays, compute_suspension_plan


class SuspensionTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.semester = Semester.objects.create(
            name='2025秋季', start_date=date(2025, 9, 1),
            end_date=date(2026, 1, 30), is_active=True,
            daily_periods=[{'name': f'第{i}节', 'order': i} for i in range(1, 8)],
            weekly_days=5, holidays=[]
        )
        self.room_a = Classroom.objects.create(
            name='A101', capacity=50, room_type='normal'
        )
        self.room_b = Classroom.objects.create(
            name='B201', capacity=50, room_type='normal'
        )
        self.teacher = Teacher.objects.create(name='张老师', subject='数学')
        self.teacher2 = Teacher.objects.create(name='李老师', subject='语文')
        self.klass = Class.objects.create(grade=7, name='1班', student_count=45)
        self.klass2 = Class.objects.create(grade=7, name='2班', student_count=45)
        self.course = Course.objects.create(
            name='数学', weekly_hours=2, preferred_room_type='normal'
        )
        self.course2 = Course.objects.create(
            name='语文', weekly_hours=2, preferred_room_type='normal'
        )
        ClassCourse.objects.create(
            class_id=self.klass, course=self.course,
            teacher=self.teacher, semester=self.semester
        )
        # 2025-09-01 是周一
        self.entry = ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.klass, course=self.course,
            teacher=self.teacher, classroom=self.room_a,
            day_of_week=1, period=1
        )

    def _create_suspension(self, **overrides):
        payload = {
            'classroom': self.room_a.id,
            'semester': self.semester.id,
            'start_date': '2025-09-01',
            'end_date': '2025-09-07',
            'reason': '电路检修',
        }
        payload.update(overrides)
        return self.client.post('/api/classroom-suspensions/', payload, format='json')


class AffectedWeekdaysTest(SuspensionTestCase):
    def test_weekdays_in_range(self):
        suspension = ClassroomSuspension(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 3),
            reason='test'
        )
        self.assertEqual(affected_weekdays(suspension), {1, 2, 3})

    def test_holidays_excluded(self):
        self.semester.holidays = ['2025-09-01']
        self.semester.save()
        suspension = ClassroomSuspension(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 3),
            reason='test'
        )
        self.assertEqual(affected_weekdays(suspension), {2, 3})

    def test_outside_semester(self):
        suspension = ClassroomSuspension(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 5),
            reason='test'
        )
        self.assertEqual(affected_weekdays(suspension), set())


class SuspensionPlanTest(SuspensionTestCase):
    def test_register_lists_affected_and_matches_replacement(self):
        resp = self._create_suspension()
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(data['status'], 'draft')
        self.assertEqual(len(data['items']), 1)
        item = data['items'][0]
        self.assertEqual(item['entry'], self.entry.id)
        self.assertEqual(item['action'], 'relocate')
        self.assertEqual(item['new_classroom'], self.room_b.id)
        self.assertEqual(item['original_classroom'], self.room_a.id)

    def test_replacement_requires_same_type_and_capacity(self):
        # 同类型但容量不足、容量够但类型不同，都不能作为替代
        Classroom.objects.create(name='A102', capacity=40, room_type='normal')
        Classroom.objects.create(name='LAB1', capacity=80, room_type='lab')
        suspension = ClassroomSuspension.objects.create(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 7),
            reason='检修'
        )
        plan, blocking = compute_suspension_plan(suspension)
        self.assertEqual(blocking, [])
        self.assertEqual(plan[0]['action'], 'relocate')
        self.assertEqual(plan[0]['new_classroom'].id, self.room_b.id)

    def test_occupied_replacement_not_chosen(self):
        # B201 同时段已被占用 -> 无替代 -> 停课
        ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.klass2, course=self.course2,
            teacher=self.teacher2, classroom=self.room_b,
            day_of_week=1, period=1
        )
        suspension = ClassroomSuspension.objects.create(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 7),
            reason='检修'
        )
        plan, blocking = compute_suspension_plan(suspension)
        self.assertEqual(blocking, [])
        self.assertEqual(plan[0]['action'], 'suspend')

    def test_locked_entry_blocks_plan(self):
        self.entry.is_locked = True
        self.entry.save()
        resp = self._create_suspension()
        data = resp.json()
        self.assertEqual(data['status'], 'blocked')
        self.assertIn('锁定', data['blocking_reason'])
        self.assertEqual(data['items'][0]['action'], 'blocked')

    def test_teacher_conflict_blocks_plan(self):
        # 教师同时段在别的教室有课 -> 无法安置
        ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.klass2, course=self.course2,
            teacher=self.teacher, classroom=self.room_b,
            day_of_week=1, period=1
        )
        suspension = ClassroomSuspension.objects.create(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 7),
            reason='检修'
        )
        plan, blocking = compute_suspension_plan(suspension)
        self.assertTrue(blocking)
        self.assertEqual(plan[0]['action'], 'blocked')

    def test_invalid_date_range_rejected(self):
        resp = self._create_suspension(start_date='2025-09-10', end_date='2025-09-01')
        self.assertEqual(resp.status_code, 400)
        resp = self._create_suspension(start_date='2026-03-01', end_date='2026-03-05')
        self.assertEqual(resp.status_code, 400)


class SuspensionConfirmTest(SuspensionTestCase):
    def test_confirm_applies_relocation(self):
        resp = self._create_suspension()
        sid = resp.json()['id']
        resp = self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()['already_applied'])
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.classroom_id, self.room_b.id)
        self.assertIn('电路检修', self.entry.schedule_note)
        self.assertIn('A101', self.entry.schedule_note)
        suspension = ClassroomSuspension.objects.get(id=sid)
        self.assertEqual(suspension.status, 'applied')
        self.assertIsNotNone(suspension.applied_at)

    def test_confirm_idempotent(self):
        resp = self._create_suspension()
        sid = resp.json()['id']
        self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        first = ClassroomSuspension.objects.get(id=sid)
        # 重复确认只生效一次
        resp = self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['already_applied'])
        second = ClassroomSuspension.objects.get(id=sid)
        self.assertEqual(first.applied_at, second.applied_at)
        self.assertEqual(
            ClassroomSuspensionItem.objects.filter(suspension_id=sid).count(), 1
        )
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.classroom_id, self.room_b.id)

    def test_confirm_blocked_keeps_original_schedule(self):
        self.entry.is_locked = True
        self.entry.save()
        resp = self._create_suspension()
        sid = resp.json()['id']
        resp = self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('无法安置', resp.json()['message'])
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.classroom_id, self.room_a.id)
        self.assertFalse(self.entry.is_suspended)
        suspension = ClassroomSuspension.objects.get(id=sid)
        self.assertEqual(suspension.status, 'blocked')
        self.assertIn('锁定', suspension.blocking_reason)

    def test_confirm_suspend_when_no_replacement(self):
        self.room_b.delete()
        resp = self._create_suspension()
        sid = resp.json()['id']
        resp = self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        self.assertEqual(resp.status_code, 200)
        self.entry.refresh_from_db()
        self.assertTrue(self.entry.is_suspended)
        self.assertIn('停课', self.entry.schedule_note)
        item = ClassroomSuspensionItem.objects.get(suspension_id=sid)
        self.assertEqual(item.action, 'suspend')

    def test_restore_makes_classroom_reusable(self):
        resp = self._create_suspension()
        sid = resp.json()['id']
        self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        resp = self.client.post(f'/api/classroom-suspensions/{sid}/restore/')
        self.assertEqual(resp.status_code, 200)
        suspension = ClassroomSuspension.objects.get(id=sid)
        self.assertEqual(suspension.status, 'restored')
        self.assertIsNotNone(suspension.restored_at)
        # 恢复后确认被拒绝
        resp = self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        self.assertEqual(resp.status_code, 400)

    def test_history_readable_after_confirm(self):
        resp = self._create_suspension()
        sid = resp.json()['id']
        self.client.post(f'/api/classroom-suspensions/{sid}/confirm/')
        resp = self.client.get(f'/api/classroom-suspensions/{sid}/')
        data = resp.json()
        self.assertEqual(data['reason'], '电路检修')
        self.assertEqual(data['items'][0]['action'], 'relocate')
        self.assertEqual(data['items'][0]['original_classroom_name'], 'A101')
        self.assertEqual(data['items'][0]['new_classroom_name'], 'B201')


class SuspensionSchedulingTest(SuspensionTestCase):
    def test_auto_schedule_excludes_suspended_classroom(self):
        ClassroomSuspension.objects.create(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 7),
            reason='检修', status='applied'
        )
        resp = self.client.post('/api/schedules/auto_schedule/', {
            'semester_id': self.semester.id, 'respect_locked': False
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        used_rooms = {e['classroom'] for e in resp.json()['schedule']}
        self.assertNotIn(self.room_a.id, used_rooms)
        self.assertIn(self.room_b.id, used_rooms)

    def test_auto_schedule_reuses_classroom_after_restore(self):
        ClassroomSuspension.objects.create(
            classroom=self.room_a, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 7),
            reason='检修', status='restored'
        )
        self.room_b.delete()  # 只剩 A101 可用
        resp = self.client.post('/api/schedules/auto_schedule/', {
            'semester_id': self.semester.id, 'respect_locked': False
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        used_rooms = {e['classroom'] for e in resp.json()['schedule']}
        self.assertIn(self.room_a.id, used_rooms)

    def test_manual_entry_cannot_use_suspended_classroom(self):
        ClassroomSuspension.objects.create(
            classroom=self.room_b, semester=self.semester,
            start_date=date(2025, 9, 1), end_date=date(2025, 9, 7),
            reason='检修', status='applied'
        )
        resp = self.client.patch(
            f'/api/schedules/{self.entry.id}/',
            {'classroom': self.room_b.id}, format='json'
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn('停用', resp.json()['error'])
        # 未停用教室正常调整
        resp = self.client.patch(
            f'/api/schedules/{self.entry.id}/',
            {'is_locked': True}, format='json'
        )
        self.assertEqual(resp.status_code, 200)

    def test_suspended_entries_excluded_from_conflict_check(self):
        self.entry.is_suspended = True
        self.entry.save()
        # 同一教室同时段另一节课，与已停课条目不构成冲突
        ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.klass2, course=self.course2,
            teacher=self.teacher2, classroom=self.room_a,
            day_of_week=1, period=1
        )
        resp = self.client.post('/api/schedules/check_conflicts/', {
            'semester_id': self.semester.id
        }, format='json')
        self.assertEqual(resp.status_code, 200)
        classroom_conflicts = [
            c for c in resp.json()['conflicts']
            if c['conflict_type'] == 'classroom'
        ]
        self.assertEqual(classroom_conflicts, [])
