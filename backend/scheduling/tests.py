from datetime import date

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from core.models import Classroom, Teacher, Class, Course, Semester
from scheduling.models import (
    ScheduleEntry, ClassroomSuspension, ClassroomSuspensionDisposition
)
from scheduling.suspension import suspension_weekdays


class SuspensionTestBase(APITestCase):
    def setUp(self):
        self.semester = Semester.objects.create(
            name='2026 秋季',
            start_date=date(2026, 9, 1),
            end_date=date(2027, 1, 31),
            is_active=True,
            daily_periods=[{'name': f'第{i}节', 'order': i} for i in range(1, 8)],
            weekly_days=5,
        )
        self.teacher1 = Teacher.objects.create(name='张老师', subject='数学')
        self.teacher2 = Teacher.objects.create(name='李老师', subject='物理')
        self.class1 = Class.objects.create(grade=10, name='高一1班', student_count=40)
        self.class2 = Class.objects.create(grade=10, name='高一2班', student_count=45)
        self.course = Course.objects.create(
            name='数学', weekly_hours=1, preferred_room_type='normal', priority='high'
        )

        self.room_a = Classroom.objects.create(
            name='A101', capacity=50, room_type='normal'
        )
        self.room_b = Classroom.objects.create(
            name='B202', capacity=60, room_type='normal'
        )
        self.room_small = Classroom.objects.create(
            name='C303', capacity=30, room_type='normal'
        )
        self.room_lab = Classroom.objects.create(
            name='Lab1', capacity=60, room_type='lab'
        )

        # A101 周一第1节：高一1班数学课（受影响）
        self.entry_mon = ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.class1, course=self.course,
            teacher=self.teacher1, classroom=self.room_a,
            day_of_week=1, period=1,
        )
        # A101 周三第2节：高一1班数学课（受影响）
        self.entry_wed = ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.class1, course=self.course,
            teacher=self.teacher1, classroom=self.room_a,
            day_of_week=3, period=2,
        )
        # A101 周五第3节：高一1班数学课（区间仅覆盖周一~周四，不受影响）
        self.entry_fri = ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.class1, course=self.course,
            teacher=self.teacher1, classroom=self.room_a,
            day_of_week=5, period=3,
        )

        self.list_url = reverse('classroomsuspension-list')

    def create_suspension(self, start=date(2026, 11, 2), end=date(2026, 11, 5),
                          classroom=None, reason='教学楼检修'):
        """2026-11-02(周一) ~ 2026-11-05(周四)"""
        resp = self.client.post(self.list_url, {
            'classroom_id': (classroom or self.room_a).id,
            'semester_id': self.semester.id,
            'start_date': start.isoformat(),
            'end_date': end.isoformat(),
            'reason': reason,
        }, format='json')
        return resp


class SuspensionWeekdayTests(SuspensionTestBase):
    def test_weekday_coverage(self):
        # 周一到周四
        self.assertEqual(
            suspension_weekdays(date(2026, 11, 2), date(2026, 11, 5)),
            {1, 2, 3, 4},
        )
        # 仅一天
        self.assertEqual(
            suspension_weekdays(date(2026, 11, 2), date(2026, 11, 2)),
            {1},
        )
        # 整周
        self.assertEqual(
            suspension_weekdays(date(2026, 11, 2), date(2026, 11, 8)),
            {1, 2, 3, 4, 5, 6, 7},
        )


class SuspensionPreviewTests(SuspensionTestBase):
    def test_create_lists_affected_entries_and_candidates(self):
        resp = self.create_suspension()
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        body = resp.json()
        self.assertEqual(body['affected_count'], 2)
        self.assertFalse(body['blocked'])
        affected_entry_ids = {item['entry']['id'] for item in body['items']}
        self.assertIn(self.entry_mon.id, affected_entry_ids)
        self.assertIn(self.entry_wed.id, affected_entry_ids)
        self.assertNotIn(self.entry_fri.id, affected_entry_ids)

        # 同类型 + 容量足够(>=40)：B202 可替代；C303 容量不足；Lab1 类型不符
        candidates = {
            item['entry']['id']: {c['id'] for c in item['candidates']}
            for item in body['items']
        }
        self.assertIn(self.room_b.id, candidates[self.entry_mon.id])
        self.assertNotIn(self.room_small.id, candidates[self.entry_mon.id])
        self.assertNotIn(self.room_lab.id, candidates[self.entry_mon.id])
        # 默认自动匹配 B202（容量最小且足够）
        for item in body['items']:
            self.assertEqual(item['chosen_classroom_id'], self.room_b.id)

    def test_create_validates_date_range_and_semester_overlap(self):
        resp = self.client.post(self.list_url, {
            'classroom_id': self.room_a.id,
            'semester_id': self.semester.id,
            'start_date': '2026-12-10',
            'end_date': '2026-12-01',
            'reason': '错误区间',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        resp = self.client.post(self.list_url, {
            'classroom_id': self.room_a.id,
            'semester_id': self.semester.id,
            'start_date': '2030-01-01',
            'end_date': '2030-01-03',
            'reason': '学期外',
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_occupied_room_not_offered_as_candidate(self):
        # B202 周一第1节已被另一个班级占用，A101 的课不能调过去
        ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.class2, course=self.course,
            teacher=self.teacher2, classroom=self.room_b,
            day_of_week=1, period=1,
        )
        resp = self.create_suspension()
        body = resp.json()
        mon_item = next(
            item for item in body['items']
            if item['entry']['id'] == self.entry_mon.id
        )
        self.assertNotIn(self.room_b.id, {c['id'] for c in mon_item['candidates']})

    def test_other_applied_suspension_blocks_candidate(self):
        # B202 同一周也停用（周一），不能作为替代教室
        ClassroomSuspension.objects.create(
            classroom=self.room_b, semester=self.semester,
            start_date=date(2026, 11, 2), end_date=date(2026, 11, 3),
            reason='B楼停电', status='applied', disposal='relocate',
        )
        resp = self.create_suspension()
        body = resp.json()
        self.assertTrue(body['blocked'])
        reasons = ' '.join(body['blocking_reasons'])
        self.assertIn('没有同类型且容量足够的空闲替代教室', reasons)


class SuspensionConfirmRelocateTests(SuspensionTestBase):
    def test_confirm_relocates_all_entries_in_one_batch(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        resp = self.client.post(url, {'disposal': 'relocate'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        body = resp.json()
        self.assertEqual(body['status'], 'applied')
        self.assertFalse(body['already_applied'])
        self.assertEqual(len(body['results']), 2)

        self.entry_mon.refresh_from_db()
        self.entry_wed.refresh_from_db()
        self.entry_fri.refresh_from_db()
        self.assertEqual(self.entry_mon.classroom_id, self.room_b.id)
        self.assertEqual(self.entry_wed.classroom_id, self.room_b.id)
        # 不受影响的条目保持不变
        self.assertEqual(self.entry_fri.classroom_id, self.room_a.id)

        suspension = ClassroomSuspension.objects.get(id=suspension_id)
        self.assertEqual(suspension.status, 'applied')
        self.assertEqual(suspension.disposal, 'relocate')
        self.assertIsNotNone(suspension.confirmed_at)
        self.assertEqual(suspension.dispositions.count(), 2)
        for d in suspension.dispositions.all():
            self.assertEqual(d.action, 'relocated')
            self.assertEqual(d.original_classroom_id, self.room_a.id)
            self.assertEqual(d.new_classroom_id, self.room_b.id)

    def test_locked_entry_blocks_entire_batch(self):
        self.entry_mon.is_locked = True
        self.entry_mon.save()
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        resp = self.client.post(url, {'disposal': 'relocate'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        body = resp.json()
        self.assertEqual(body['status'], 'blocked')
        self.assertTrue(any('锁定' in r for r in body['blocking_reasons']))

        # 整批保持原课表：另一条未锁定的课也不得挪动
        self.entry_mon.refresh_from_db()
        self.entry_wed.refresh_from_db()
        self.assertEqual(self.entry_mon.classroom_id, self.room_a.id)
        self.assertEqual(self.entry_wed.classroom_id, self.room_a.id)
        # 未写入任何处置记录
        self.assertEqual(ClassroomSuspensionDisposition.objects.count(), 0)
        self.assertEqual(
            ClassroomSuspension.objects.get(id=suspension_id).status, 'pending'
        )

    def test_no_available_room_blocks_entire_batch(self):
        # B202 两个时段都被占用 → 两条课都无处可去
        ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.class2, course=self.course,
            teacher=self.teacher2, classroom=self.room_b,
            day_of_week=1, period=1,
        )
        ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.class2, course=self.course,
            teacher=self.teacher2, classroom=self.room_b,
            day_of_week=3, period=2,
        )
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        resp = self.client.post(url, {'disposal': 'relocate'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertIn('无法安置', ' '.join(resp.json()['blocking_reasons']))
        self.assertEqual(ScheduleEntry.objects.filter(classroom=self.room_a).count(), 3)

    def test_duplicate_confirm_applies_only_once(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        first = self.client.post(url, {'disposal': 'relocate'}, format='json')
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertFalse(first.json()['already_applied'])

        # 重复确认只回读已有结果，不再写入
        second = self.client.post(url, {'disposal': 'relocate'}, format='json')
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.json()['already_applied'])
        self.assertEqual(
            ClassroomSuspensionDisposition.objects.filter(suspension_id=suspension_id).count(),
            2,
        )
        self.entry_mon.refresh_from_db()
        self.assertEqual(self.entry_mon.classroom_id, self.room_b.id)

    def test_confirm_with_explicit_assignment(self):
        # 再加一间同类型大教室，教务员显式指定使用它
        room_c = Classroom.objects.create(name='D404', capacity=80, room_type='normal')
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        resp = self.client.post(url, {
            'disposal': 'relocate',
            'assignments': {str(self.entry_mon.id): room_c.id},
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.entry_mon.refresh_from_db()
        self.entry_wed.refresh_from_db()
        self.assertEqual(self.entry_mon.classroom_id, room_c.id)
        # 未指定的条目仍自动匹配
        self.assertEqual(self.entry_wed.classroom_id, self.room_b.id)

    def test_invalid_explicit_assignment_rejected(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        # 实验室类型不符
        resp = self.client.post(url, {
            'disposal': 'relocate',
            'assignments': {str(self.entry_mon.id): self.room_lab.id},
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.entry_mon.refresh_from_db()
        self.assertEqual(self.entry_mon.classroom_id, self.room_a.id)


class SuspensionConfirmCancelTests(SuspensionTestBase):
    def test_confirm_cancel_writes_cancellation_records(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        resp = self.client.post(url, {'disposal': 'cancel'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()['status'], 'applied')

        # 条目本身不删除、不换教室，停课记录写入
        self.assertEqual(ScheduleEntry.objects.filter(classroom=self.room_a).count(), 3)
        dispositions = ClassroomSuspensionDisposition.objects.filter(
            suspension_id=suspension_id
        )
        self.assertEqual(dispositions.count(), 2)
        for d in dispositions:
            self.assertEqual(d.action, 'cancelled')
            self.assertIsNone(d.new_classroom_id)

    def test_locked_entry_blocks_cancel_batch(self):
        self.entry_mon.is_locked = True
        self.entry_mon.save()
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])

        resp = self.client.post(url, {'disposal': 'cancel'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(any('锁定' in r for r in resp.json()['blocking_reasons']))
        self.assertEqual(ClassroomSuspensionDisposition.objects.count(), 0)


class SuspensionBlockingTests(SuspensionTestBase):
    def _apply_suspension(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])
        self.client.post(url, {'disposal': 'relocate'}, format='json')
        return suspension_id

    def test_manual_scheduling_cannot_use_suspended_room(self):
        self._apply_suspension()
        url = reverse('scheduleentry-list')

        # 周一（停用区间覆盖）第4节排课到 A101 → 拒绝
        resp = self.client.post(url, {
            'semester': self.semester.id,
            'class_id': self.class2.id,
            'course': self.course.id,
            'teacher': self.teacher2.id,
            'classroom': self.room_a.id,
            'day_of_week': 1,
            'period': 4,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('临时停用', str(resp.json()))

        # 周五（停用区间未覆盖）仍可排到 A101
        resp = self.client.post(url, {
            'semester': self.semester.id,
            'class_id': self.class2.id,
            'course': self.course.id,
            'teacher': self.teacher2.id,
            'classroom': self.room_a.id,
            'day_of_week': 5,
            'period': 4,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_lock_toggle_on_leftover_entry_allowed(self):
        # 阻断场景下锁定条目仍留在停用教室，此时切换锁定不应被拒
        self.entry_mon.is_locked = True
        self.entry_mon.save()
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])
        resp = self.client.post(url, {'disposal': 'relocate'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)

        resp = self.client.patch(
            reverse('scheduleentry-detail', args=[self.entry_mon.id]),
            {'is_locked': False}, format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_auto_schedule_avoids_suspended_room_on_affected_weekdays(self):
        self._apply_suspension()
        url = reverse('scheduleentry-auto-schedule')
        # 清理预置条目，让自动排课从零开始（尊重锁定，无锁定条目）
        ScheduleEntry.objects.all().delete()

        # 给两个班都配数学课
        from scheduling.models import ClassCourse
        ClassCourse.objects.create(
            semester=self.semester, class_id=self.class1,
            course=self.course, teacher=self.teacher1,
        )
        course2 = Course.objects.create(
            name='物理', weekly_hours=5, preferred_room_type='normal',
            priority='medium',
        )
        ClassCourse.objects.create(
            semester=self.semester, class_id=self.class2,
            course=course2, teacher=self.teacher2,
        )

        resp = self.client.post(url, {
            'semester_id': self.semester.id,
            'respect_locked': True,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        # A101 在周一~周四不应出现排课；周五可用
        used_days = set(
            ScheduleEntry.objects.filter(classroom=self.room_a)
            .values_list('day_of_week', flat=True)
        )
        self.assertTrue(used_days.issubset({5}) if used_days else True)

    def test_recover_releases_room(self):
        suspension_id = self._apply_suspension()

        # 恢复前排不进去
        resp = self.client.post(reverse('scheduleentry-list'), {
            'semester': self.semester.id,
            'class_id': self.class2.id,
            'course': self.course.id,
            'teacher': self.teacher2.id,
            'classroom': self.room_a.id,
            'day_of_week': 2,
            'period': 4,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

        recover_url = reverse('classroomsuspension-recover', args=[suspension_id])
        resp = self.client.post(recover_url, {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.json()['status'], 'recovered')
        self.assertEqual(
            ClassroomSuspension.objects.get(id=suspension_id).status, 'recovered'
        )
        # 重复恢复幂等
        again = self.client.post(recover_url, {}, format='json')
        self.assertTrue(again.json().get('already_recovered'))

        # 恢复后可重新使用
        resp = self.client.post(reverse('scheduleentry-list'), {
            'semester': self.semester.id,
            'class_id': self.class2.id,
            'course': self.course.id,
            'teacher': self.teacher2.id,
            'classroom': self.room_a.id,
            'day_of_week': 2,
            'period': 4,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_pending_suspension_does_not_block_scheduling(self):
        self.create_suspension()
        resp = self.client.post(reverse('scheduleentry-list'), {
            'semester': self.semester.id,
            'class_id': self.class2.id,
            'course': self.course.id,
            'teacher': self.teacher2.id,
            'classroom': self.room_a.id,
            'day_of_week': 1,
            'period': 4,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_swap_into_suspended_weekday_rejected(self):
        # entry_fri 在 A101 周五；另一条 B202 周一的课与其调课
        other = ScheduleEntry.objects.create(
            semester=self.semester, class_id=self.class2, course=self.course,
            teacher=self.teacher2, classroom=self.room_b,
            day_of_week=1, period=3,
        )
        self._apply_suspension()  # 周一~周四 A101 停用

        url = reverse('scheduleentry-swap')
        resp = self.client.post(url, {
            'entry1_id': self.entry_fri.id,
            'entry2_id': other.id,
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.entry_fri.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.entry_fri.day_of_week, 5)
        self.assertEqual(other.day_of_week, 1)


class SuspensionReadbackTests(SuspensionTestBase):
    def test_schedule_api_returns_suspension_history_with_reason(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])
        self.client.post(url, {'disposal': 'relocate'}, format='json')

        resp = self.client.get(
            reverse('scheduleentry-by-class'),
            {'semester_id': self.semester.id, 'class_id': self.class1.id},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        moved = next(e for e in resp.json() if e['id'] == self.entry_mon.id)
        records = moved['suspension_records']
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['action'], 'relocated')
        self.assertEqual(records[0]['reason'], '教学楼检修')
        self.assertEqual(records[0]['original_classroom_name'], 'A101')
        self.assertEqual(records[0]['new_classroom_name'], 'B202')
        self.assertEqual(records[0]['start_date'], '2026-11-02')
        self.assertEqual(records[0]['end_date'], '2026-11-05')

    def test_disposition_snapshot_survives_entry_deletion(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        url = reverse('classroomsuspension-confirm', args=[suspension_id])
        self.client.post(url, {'disposal': 'cancel'}, format='json')
        self.entry_mon.delete()

        suspension = ClassroomSuspension.objects.get(id=suspension_id)
        disposition = suspension.dispositions.filter(course_name='数学').first()
        self.assertIsNotNone(disposition)
        self.assertIsNone(disposition.entry_id)
        self.assertEqual(disposition.class_name, str(self.class1))
        self.assertEqual(disposition.day_of_week, 1)
        self.assertEqual(disposition.period, 1)

    def test_list_filtering_and_delete_restriction(self):
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']

        # 按学期筛选
        resp = self.client.get(self.list_url, {'semester': self.semester.id})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()), 1)

        # 待确认的登记可以删除
        resp = self.client.delete(
            reverse('classroomsuspension-detail', args=[suspension_id])
        )
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(ClassroomSuspension.objects.count(), 0)

        # 已生效的记录不能删除，只能恢复（保留历史）
        resp = self.create_suspension()
        suspension_id = resp.json()['suspension']['id']
        self.client.post(
            reverse('classroomsuspension-confirm', args=[suspension_id]),
            {'disposal': 'relocate'}, format='json',
        )
        resp = self.client.delete(
            reverse('classroomsuspension-detail', args=[suspension_id])
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(ClassroomSuspension.objects.filter(id=suspension_id).exists())
