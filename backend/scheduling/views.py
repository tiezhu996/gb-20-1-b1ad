from django.http import HttpResponse
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.serializers import ValidationError
from django.db import transaction
from django.utils import timezone
from core.models import Semester, Classroom, Teacher, Class
from .models import (
    ClassCourse, ScheduleEntry, Conflict, SwapRequest, Substitute,
    ClassroomSuspension, ClassroomSuspensionDisposition
)
from .serializers import (
    ClassCourseSerializer, ScheduleEntrySerializer,
    ScheduleEntryDetailSerializer, ConflictSerializer,
    SwapRequestSerializer, SubstituteSerializer,
    ClassroomSuspensionSerializer, ClassroomSuspensionDispositionSerializer,
    ClassroomSuspensionCreateRequestSerializer,
    ClassroomSuspensionConfirmRequestSerializer,
    AutoScheduleRequestSerializer, ConflictCheckSerializer,
    SwapScheduleRequestSerializer, SubstituteRequestSerializer
)
from .csp_solver import CSPScheduler, ConflictDetector, SchedulingTask, TimeSlot
from .suspension import (
    blocked_slots_for_semester, classroom_is_suspended_on,
    compute_plan, get_affected_entries
)
from .pdf_export import (
    generate_class_timetable_pdf,
    generate_teacher_timetable_pdf,
    generate_classroom_timetable_pdf
)


class ClassCourseViewSet(viewsets.ModelViewSet):
    queryset = ClassCourse.objects.all()
    serializer_class = ClassCourseSerializer
    permission_classes = [AllowAny]


class ScheduleEntryViewSet(viewsets.ModelViewSet):
    queryset = ScheduleEntry.objects.all().select_related(
        'course', 'teacher', 'classroom', 'class_id', 'semester'
    ).prefetch_related(
        'suspension_dispositions__suspension',
        'suspension_dispositions__original_classroom',
        'suspension_dispositions__new_classroom'
    )
    serializer_class = ScheduleEntryDetailSerializer
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return ScheduleEntryDetailSerializer
        return ScheduleEntrySerializer

    def _guard_classroom_suspension(self, validated_data, instance=None):
        """停用期内普通排课不得占用该教室。

        只在教室/星期/学期实际发生变化时校验，以便已留在停用教室内的
        锁定条目仍可切换锁定状态。
        """
        classroom = validated_data.get(
            'classroom_id',
            validated_data.get('classroom', None),
        )
        semester = validated_data.get(
            'semester_id',
            validated_data.get('semester', None),
        )
        day_of_week = validated_data.get('day_of_week')
        classroom_id = getattr(classroom, 'id', classroom)
        semester_id = getattr(semester, 'id', semester)
        if classroom_id is None:
            classroom_id = instance.classroom_id if instance else None
        if semester_id is None:
            semester_id = instance.semester_id if instance else None
        if day_of_week is None:
            day_of_week = instance.day_of_week if instance else None

        if instance is not None and not validated_data:
            return
        if instance is not None:
            unchanged = (
                classroom_id == instance.classroom_id
                and semester_id == instance.semester_id
                and day_of_week == instance.day_of_week
            )
            if unchanged:
                return

        suspension = classroom_is_suspended_on(classroom_id, semester_id, day_of_week)
        if suspension is not None:
            raise ValidationError({
                'classroom': (
                    f"教室 {suspension.classroom.name} 在 "
                    f"{suspension.start_date}~{suspension.end_date} 临时停用"
                    f"（原因：{suspension.reason}），该时段不得排课"
                )
            })

    def perform_create(self, serializer):
        self._guard_classroom_suspension(serializer.validated_data)
        serializer.save()

    def perform_update(self, serializer):
        self._guard_classroom_suspension(serializer.validated_data, serializer.instance)
        serializer.save()

    @action(detail=False, methods=['get'])
    def by_semester(self, request):
        semester_id = request.query_params.get('semester_id')
        if not semester_id:
            return Response(
                {'error': 'semester_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        entries = self.queryset.filter(semester_id=semester_id)
        serializer = ScheduleEntryDetailSerializer(entries, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def by_class(self, request):
        semester_id = request.query_params.get('semester_id')
        class_id = request.query_params.get('class_id')
        entries = self.queryset.filter(semester_id=semester_id, class_id=class_id)
        serializer = ScheduleEntryDetailSerializer(entries, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def by_teacher(self, request):
        semester_id = request.query_params.get('semester_id')
        teacher_id = request.query_params.get('teacher_id')
        entries = self.queryset.filter(semester_id=semester_id, teacher_id=teacher_id)
        serializer = ScheduleEntryDetailSerializer(entries, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def by_classroom(self, request):
        semester_id = request.query_params.get('semester_id')
        classroom_id = request.query_params.get('classroom_id')
        entries = self.queryset.filter(semester_id=semester_id, classroom_id=classroom_id)
        serializer = ScheduleEntryDetailSerializer(entries, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def auto_schedule(self, request):
        req_serializer = AutoScheduleRequestSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        semester_id = req_serializer.validated_data['semester_id']
        respect_locked = req_serializer.validated_data['respect_locked']

        try:
            semester = Semester.objects.get(id=semester_id)
        except Semester.DoesNotExist:
            return Response(
                {'error': 'Semester not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        class_courses = ClassCourse.objects.filter(
            semester=semester
        ).select_related('class_id', 'course', 'teacher')

        if not class_courses.exists():
            return Response(
                {'error': 'No class courses configured for this semester'},
                status=status.HTTP_400_BAD_REQUEST
            )

        tasks = []
        for cc in class_courses:
            tasks.append(SchedulingTask(
                class_id=cc.class_id.id,
                course_id=cc.course.id,
                teacher_id=cc.teacher.id,
                weekly_hours=cc.course.weekly_hours,
                preferred_room_type=cc.course.preferred_room_type,
                priority=cc.course.priority,
                available_time_slots=[],
                classroom_capacity=cc.class_id.student_count or 40
            ))

        classrooms_data = {
            c.id: {
                'room_type': c.room_type,
                'capacity': c.capacity,
                'name': c.name
            } for c in Classroom.objects.filter(is_active=True)
        }

        teachers_data = {
            t.id: {
                'name': t.name,
                'available_time_slots': t.available_time_slots if t.available_time_slots else []
            } for t in Teacher.objects.filter(is_active=True)
        }

        locked_entries = []
        if respect_locked:
            locked = ScheduleEntry.objects.filter(
                semester=semester, is_locked=True
            ).values(
                'id', 'class_id', 'teacher_id', 'classroom_id',
                'day_of_week', 'period', 'is_locked'
            )
            locked_entries = list(locked)

        # 生效中的教室停用：停用区间覆盖的时段不得被自动排课占用
        blocked_classroom_slots = blocked_slots_for_semester(semester)

        scheduler = CSPScheduler(semester)
        assignments, scheduling_conflicts = scheduler.schedule(
            tasks, classrooms_data, teachers_data, locked_entries,
            blocked_classroom_slots=blocked_classroom_slots
        )

        with transaction.atomic():
            if respect_locked:
                ScheduleEntry.objects.filter(
                    semester=semester, is_locked=False
                ).delete()
            else:
                ScheduleEntry.objects.filter(semester=semester).delete()

            bulk_entries = []
            for a in assignments:
                if a.get('is_locked'):
                    continue
                bulk_entries.append(ScheduleEntry(
                    semester_id=a['semester_id'],
                    class_id_id=a['class_id'],
                    course_id=a['course_id'],
                    teacher_id=a['teacher_id'],
                    classroom_id=a['classroom_id'],
                    day_of_week=a['day_of_week'],
                    period=a['period'],
                    is_locked=False
                ))
            ScheduleEntry.objects.bulk_create(bulk_entries)

            all_entries = ScheduleEntry.objects.filter(
                semester=semester
            ).values('id', 'teacher_id', 'classroom_id', 'class_id', 'day_of_week', 'period')

            detector = ConflictDetector()
            conflicts = detector.detect_conflicts(list(all_entries))

            Conflict.objects.filter(semester=semester).delete()
            bulk_conflicts = []
            for c in conflicts:
                bulk_conflicts.append(Conflict(
                    semester=semester,
                    conflict_type=c['conflict_type'],
                    day_of_week=c['day_of_week'],
                    period=c['period'],
                    involved_entries=c['involved_entries'],
                    message=c['message']
                ))
            Conflict.objects.bulk_create(bulk_conflicts)

            for c in conflicts:
                for eid in c['involved_entries']:
                    try:
                        entry = ScheduleEntry.objects.get(id=eid)
                        entry.is_conflict = True
                        entry.conflict_type = c['conflict_type']
                        entry.save()
                    except ScheduleEntry.DoesNotExist:
                        pass

        final_entries = ScheduleEntry.objects.filter(semester=semester)
        serializer = ScheduleEntryDetailSerializer(final_entries, many=True)

        return Response({
            'schedule': serializer.data,
            'conflicts': conflicts,
            'scheduling_messages': scheduling_conflicts,
            'total_entries': len(serializer.data)
        })

    @action(detail=False, methods=['post'])
    def check_conflicts(self, request):
        req_serializer = ConflictCheckSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        semester_id = req_serializer.validated_data['semester_id']
        entries = ScheduleEntry.objects.filter(
            semester_id=semester_id
        ).values('id', 'teacher_id', 'classroom_id', 'class_id', 'day_of_week', 'period')

        detector = ConflictDetector()
        conflicts = detector.detect_conflicts(list(entries))

        return Response({'conflicts': conflicts})

    @action(detail=False, methods=['post'])
    def swap(self, request):
        req_serializer = SwapScheduleRequestSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        entry1_id = req_serializer.validated_data['entry1_id']
        entry2_id = req_serializer.validated_data['entry2_id']
        reason = req_serializer.validated_data.get('reason', '')

        try:
            entry1 = ScheduleEntry.objects.get(id=entry1_id)
            entry2 = ScheduleEntry.objects.get(id=entry2_id)
        except ScheduleEntry.DoesNotExist:
            return Response(
                {'error': 'One or both entries not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        with transaction.atomic():
            day1, period1 = entry1.day_of_week, entry1.period
            day2, period2 = entry2.day_of_week, entry2.period

            # 调课不得把课程换到该教室停用的星期
            blocked = classroom_is_suspended_on(
                entry1.classroom_id, entry1.semester_id, day2
            )
            if blocked is None:
                blocked = classroom_is_suspended_on(
                    entry2.classroom_id, entry2.semester_id, day1
                )
            if blocked is not None:
                return Response({
                    'error': (
                        f"教室 {blocked.classroom.name} 在 "
                        f"{blocked.start_date}~{blocked.end_date} 临时停用"
                        f"（原因：{blocked.reason}），该时段不得调课"
                    )
                }, status=status.HTTP_400_BAD_REQUEST)

            entry1.day_of_week, entry1.period = day2, period2
            entry2.day_of_week, entry2.period = day1, period1

            entry1.save()
            entry2.save()

            if reason:
                SwapRequest.objects.create(
                    semester=entry1.semester,
                    requesting_teacher=entry1.teacher,
                    target_teacher=entry2.teacher,
                    entry1=entry1,
                    entry2=entry2,
                    reason=reason,
                    status='approved'
                )

        return Response({'status': 'success', 'message': 'Swap completed'})

    @action(detail=False, methods=['post'])
    def substitute(self, request):
        req_serializer = SubstituteRequestSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(req_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        entry_id = req_serializer.validated_data['entry_id']
        substitute_teacher_id = req_serializer.validated_data['substitute_teacher_id']
        start_date = req_serializer.validated_data['start_date']
        end_date = req_serializer.validated_data['end_date']
        reason = req_serializer.validated_data['reason']

        try:
            entry = ScheduleEntry.objects.get(id=entry_id)
            substitute_teacher = Teacher.objects.get(id=substitute_teacher_id)
        except (ScheduleEntry.DoesNotExist, Teacher.DoesNotExist):
            return Response(
                {'error': 'Entry or teacher not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        original_teacher = entry.teacher

        with transaction.atomic():
            Substitute.objects.create(
                semester=entry.semester,
                original_teacher=original_teacher,
                substitute_teacher=substitute_teacher,
                affected_entry=entry,
                start_date=start_date,
                end_date=end_date,
                reason=reason
            )

            entry.original_teacher = original_teacher
            entry.teacher = substitute_teacher
            entry.save()

        serializer = ScheduleEntryDetailSerializer(entry)
        return Response({'status': 'success', 'entry': serializer.data})

    @action(detail=False, methods=['get'])
    def export_pdf(self, request):
        semester_id = request.query_params.get('semester_id')
        entity_type = request.query_params.get('type')
        entity_id = request.query_params.get('id')

        try:
            semester = Semester.objects.get(id=semester_id)
        except Semester.DoesNotExist:
            return Response(
                {'error': 'Semester not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        pdf_buffer = None
        filename = 'timetable.pdf'

        try:
            if entity_type == 'class':
                class_obj = Class.objects.get(id=entity_id)
                pdf_buffer = generate_class_timetable_pdf(class_obj, semester)
                filename = f'{class_obj.name}_课表.pdf'
            elif entity_type == 'teacher':
                teacher = Teacher.objects.get(id=entity_id)
                pdf_buffer = generate_teacher_timetable_pdf(teacher, semester)
                filename = f'{teacher.name}_课表.pdf'
            elif entity_type == 'classroom':
                classroom = Classroom.objects.get(id=entity_id)
                pdf_buffer = generate_classroom_timetable_pdf(classroom, semester)
                filename = f'{classroom.name}_课表.pdf'
            else:
                return Response(
                    {'error': 'Invalid type. Must be class, teacher, or classroom'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except (Class.DoesNotExist, Teacher.DoesNotExist, Classroom.DoesNotExist):
            return Response(
                {'error': 'Entity not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        response = HttpResponse(pdf_buffer, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response


class ConflictViewSet(viewsets.ModelViewSet):
    queryset = Conflict.objects.all().select_related('semester')
    serializer_class = ConflictSerializer
    permission_classes = [AllowAny]


class SwapRequestViewSet(viewsets.ModelViewSet):
    queryset = SwapRequest.objects.all().select_related(
        'semester', 'requesting_teacher', 'target_teacher'
    )
    serializer_class = SwapRequestSerializer
    permission_classes = [AllowAny]


class SubstituteViewSet(viewsets.ModelViewSet):
    queryset = Substitute.objects.all().select_related(
        'semester', 'original_teacher', 'substitute_teacher'
    )
    serializer_class = SubstituteSerializer
    permission_classes = [AllowAny]


class ClassroomSuspensionViewSet(viewsets.ModelViewSet):
    """教室临时停用登记与处置。"""
    queryset = ClassroomSuspension.objects.all().select_related(
        'classroom', 'semester'
    ).prefetch_related(
        'dispositions__original_classroom',
        'dispositions__new_classroom',
    )
    serializer_class = ClassroomSuspensionSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = self.queryset
        classroom_id = self.request.query_params.get('classroom')
        semester_id = self.request.query_params.get('semester')
        if classroom_id:
            queryset = queryset.filter(classroom_id=classroom_id)
        if semester_id:
            queryset = queryset.filter(semester_id=semester_id)
        return queryset

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'pending':
            return Response({
                'error': '已生效的停用记录不能删除；如教室已可使用，请执行恢复操作（历史记录会保留）'
            }, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        req_serializer = ClassroomSuspensionCreateRequestSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(
                req_serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )
        data = req_serializer.validated_data
        try:
            classroom = Classroom.objects.get(id=data['classroom_id'])
            semester = Semester.objects.get(id=data['semester_id'])
        except (Classroom.DoesNotExist, Semester.DoesNotExist):
            return Response(
                {'error': 'Classroom or semester not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        if data['end_date'] < semester.start_date or data['start_date'] > semester.end_date:
            return Response(
                {'error': '停用日期区间与所选学期没有交集'},
                status=status.HTTP_400_BAD_REQUEST
            )

        suspension = ClassroomSuspension.objects.create(
            classroom=classroom,
            semester=semester,
            start_date=data['start_date'],
            end_date=data['end_date'],
            reason=data['reason'],
            status='pending',
        )
        return Response(
            self._build_preview(suspension), status=status.HTTP_201_CREATED
        )

    def _build_preview(self, suspension):
        """列出受影响课程及每个课程可用的替代教室。"""
        entries = get_affected_entries(suspension)
        plan_items, blocking_reasons = compute_plan(suspension, entries)

        items = []
        for item in plan_items:
            items.append({
                'entry': ScheduleEntryDetailSerializer(item['entry']).data,
                'candidates': [
                    {
                        'id': room.id,
                        'name': room.name,
                        'capacity': room.capacity,
                        'room_type': room.room_type,
                    }
                    for room in item['candidates']
                ],
                'chosen_classroom_id': item['chosen_classroom_id'],
                'block_reason': item['block_reason'],
            })

        return {
            'suspension': ClassroomSuspensionSerializer(suspension).data,
            'items': items,
            'blocked': bool(blocking_reasons),
            'blocking_reasons': blocking_reasons,
            'affected_count': len(items),
        }

    @action(detail=True, methods=['get'])
    def preview(self, request, pk=None):
        suspension = self.get_object()
        return Response(self._build_preview(suspension))

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        req_serializer = ClassroomSuspensionConfirmRequestSerializer(data=request.data)
        if not req_serializer.is_valid():
            return Response(
                req_serializer.errors, status=status.HTTP_400_BAD_REQUEST
            )
        disposal = req_serializer.validated_data['disposal']
        raw_assignments = req_serializer.validated_data.get('assignments') or {}
        try:
            assignments = {int(k): int(v) for k, v in raw_assignments.items()}
        except (TypeError, ValueError):
            return Response(
                {'error': 'assignments 格式应为 课程条目ID -> 替代教室ID'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # select_for_update 串行化并发确认；行锁内复查状态，
        # 保证重复或并发确认只生效一次
        with transaction.atomic():
            suspension = ClassroomSuspension.objects.select_for_update().get(pk=pk)

            if suspension.status != 'pending':
                suspension.refresh_from_db()
                return Response({
                    'status': suspension.status,
                    'already_applied': True,
                    'message': '该停用登记已确认，请勿重复提交',
                    'suspension': ClassroomSuspensionSerializer(suspension).data,
                    'results': ClassroomSuspensionDispositionSerializer(
                        suspension.dispositions.all(), many=True
                    ).data,
                })

            entries = get_affected_entries(suspension)

            if disposal == 'cancel':
                # 停课不要求替代教室，但锁定课程不能改动
                locked_entries = [entry for entry in entries if entry.is_locked]
                if locked_entries:
                    blocking_reasons = [
                        (
                            f"{entry.course.name}（{entry.class_id.name}，周{entry.day_of_week}"
                            f"第{entry.period}节）为锁定课程，不能改动，如需停课请先解锁"
                        )
                        for entry in locked_entries
                    ]
                    return Response({
                        'status': 'blocked',
                        'message': '存在不能改动的锁定课程，整批保持原课表',
                        'blocking_reasons': blocking_reasons,
                    }, status=status.HTTP_409_CONFLICT)

                dispositions = [
                    self._make_disposition(suspension, entry, None, 'cancelled')
                    for entry in entries
                ]
                ClassroomSuspensionDisposition.objects.bulk_create(dispositions)
            else:
                plan_items, blocking_reasons = compute_plan(
                    suspension, entries, assignments=assignments
                )
                if blocking_reasons:
                    # 任一课程无法安置：整批回滚，原课表不动
                    return Response({
                        'status': 'blocked',
                        'message': '存在无法安置的课程，整批保持原课表',
                        'blocking_reasons': blocking_reasons,
                        'items': [
                            {
                                'entry_id': item['entry'].id,
                                'chosen_classroom_id': item['chosen_classroom_id'],
                                'block_reason': item['block_reason'],
                            }
                            for item in plan_items
                        ],
                    }, status=status.HTTP_409_CONFLICT)

                chosen = {
                    item['entry'].id: item['chosen_classroom_id']
                    for item in plan_items
                }
                dispositions = []
                for entry in entries:
                    new_room_id = chosen[entry.id]
                    dispositions.append(
                        self._make_disposition(suspension, entry, new_room_id, 'relocated')
                    )
                    entry.classroom_id = new_room_id
                    entry.save(update_fields=['classroom_id', 'updated_at'])
                ClassroomSuspensionDisposition.objects.bulk_create(dispositions)

            suspension.status = 'applied'
            suspension.disposal = disposal
            suspension.confirmed_at = timezone.now()
            suspension.save(update_fields=[
                'status', 'disposal', 'confirmed_at', 'updated_at'
            ])

        suspension.refresh_from_db()
        return Response({
            'status': 'applied',
            'already_applied': False,
            'message': '停用处置已生效' if disposal == 'relocate' else '停课记录已写入',
            'suspension': ClassroomSuspensionSerializer(suspension).data,
            'results': ClassroomSuspensionDispositionSerializer(
                suspension.dispositions.all(), many=True
            ).data,
        })

    @staticmethod
    def _make_disposition(suspension, entry, new_classroom_id, action):
        return ClassroomSuspensionDisposition(
            suspension=suspension,
            entry=entry,
            action=action,
            original_classroom=entry.classroom,
            new_classroom_id=new_classroom_id,
            class_name=str(entry.class_id),
            course_name=entry.course.name,
            day_of_week=entry.day_of_week,
            period=entry.period,
        )

    @action(detail=True, methods=['post'])
    def recover(self, request, pk=None):
        with transaction.atomic():
            suspension = ClassroomSuspension.objects.select_for_update().get(pk=pk)

            if suspension.status == 'recovered':
                return Response({
                    'status': 'recovered',
                    'already_recovered': True,
                    'message': '该教室已恢复使用',
                    'suspension': ClassroomSuspensionSerializer(suspension).data,
                })
            if suspension.status == 'pending':
                return Response({
                    'error': '停用尚未生效，无需恢复；待确认的登记可直接删除'
                }, status=status.HTTP_400_BAD_REQUEST)

            suspension.status = 'recovered'
            suspension.recovered_at = timezone.now()
            suspension.save(update_fields=['status', 'recovered_at', 'updated_at'])

        suspension.refresh_from_db()
        return Response({
            'status': 'recovered',
            'message': '教室已恢复，可重新用于排课',
            'suspension': ClassroomSuspensionSerializer(suspension).data,
        })
