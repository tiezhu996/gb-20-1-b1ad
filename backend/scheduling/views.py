from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.db import transaction
from core.models import Semester, Classroom, Teacher, Class
from .models import (
    ClassCourse, ScheduleEntry, Conflict, SwapRequest, Substitute,
    ClassroomSuspension, ClassroomSuspensionItem
)
from .serializers import (
    ClassCourseSerializer, ScheduleEntrySerializer,
    ScheduleEntryDetailSerializer, ConflictSerializer,
    SwapRequestSerializer, SubstituteSerializer,
    AutoScheduleRequestSerializer, ConflictCheckSerializer,
    SwapScheduleRequestSerializer, SubstituteRequestSerializer,
    ClassroomSuspensionSerializer, ClassroomSuspensionCreateSerializer
)
from .csp_solver import CSPScheduler, ConflictDetector, SchedulingTask, TimeSlot
from .suspension_service import compute_suspension_plan, record_plan, apply_suspension
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
    )
    serializer_class = ScheduleEntryDetailSerializer
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action in ['list', 'retrieve']:
            return ScheduleEntryDetailSerializer
        return ScheduleEntrySerializer

    @staticmethod
    def _suspension_guard(request, instance=None):
        """停用期内的教室不允许通过普通排课占用"""
        classroom_id = request.data.get('classroom')
        if not classroom_id:
            return None
        semester_id = request.data.get('semester')
        if not semester_id and instance is not None:
            semester_id = instance.semester_id
        if not semester_id:
            return None
        suspended = (
            ClassroomSuspension.objects
            .filter(classroom_id=classroom_id, semester_id=semester_id)
            .exclude(status='restored')
            .first()
        )
        if suspended:
            return Response(
                {'error': (f"教室 {suspended.classroom.name} 在 "
                           f"{suspended.start_date} 至 {suspended.end_date} 期间停用"
                           f"（{suspended.reason}），不能安排课程")},
                status=status.HTTP_400_BAD_REQUEST
            )
        return None

    def create(self, request, *args, **kwargs):
        guard = self._suspension_guard(request)
        if guard is not None:
            return guard
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        guard = self._suspension_guard(request, instance=self.get_object())
        if guard is not None:
            return guard
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        guard = self._suspension_guard(request, instance=self.get_object())
        if guard is not None:
            return guard
        return super().partial_update(request, *args, **kwargs)

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

        # 停用期内（未恢复）的教室不参与普通排课
        suspended_rooms = {
            row['classroom_id']: row['reason']
            for row in ClassroomSuspension.objects
            .filter(semester=semester)
            .exclude(status='restored')
            .values('classroom_id', 'reason')
        }
        classrooms_data = {
            c.id: {
                'room_type': c.room_type,
                'capacity': c.capacity,
                'name': c.name
            } for c in Classroom.objects.filter(is_active=True)
            if c.id not in suspended_rooms
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

        scheduler = CSPScheduler(semester)
        assignments, scheduling_conflicts = scheduler.schedule(
            tasks, classrooms_data, teachers_data, locked_entries
        )

        if suspended_rooms:
            names = '、'.join(
                Classroom.objects.filter(id__in=suspended_rooms).values_list('name', flat=True)
            )
            scheduling_conflicts.append({
                'type': 'classroom_suspension',
                'message': f"教室 {names} 处于停用期，本次排课未使用"
            })

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
                semester=semester, is_suspended=False
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
            semester_id=semester_id, is_suspended=False
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
    """教室临时停用：登记、受影响课程预览、确认生效、恢复"""

    queryset = ClassroomSuspension.objects.select_related(
        'classroom', 'semester'
    ).prefetch_related(
        'items__entry__course', 'items__entry__class_id', 'items__entry__teacher',
        'items__original_classroom', 'items__new_classroom'
    )
    serializer_class = ClassroomSuspensionSerializer
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        if self.action == 'create':
            return ClassroomSuspensionCreateSerializer
        return ClassroomSuspensionSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        classroom_id = self.request.query_params.get('classroom_id')
        semester_id = self.request.query_params.get('semester_id')
        status_param = self.request.query_params.get('status')
        if classroom_id:
            qs = qs.filter(classroom_id=classroom_id)
        if semester_id:
            qs = qs.filter(semester_id=semester_id)
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    def create(self, request, *args, **kwargs):
        serializer = ClassroomSuspensionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            suspension = serializer.save()
            # 登记后立即列出受影响课程并匹配替代教室
            plan_items, blocking = compute_suspension_plan(suspension)
            if blocking:
                suspension.status = 'blocked'
                suspension.blocking_reason = '；'.join(blocking)
                suspension.save(update_fields=['status', 'blocking_reason', 'updated_at'])
            record_plan(suspension, plan_items)
        return Response(
            ClassroomSuspensionSerializer(suspension).data,
            status=status.HTTP_201_CREATED
        )

    def destroy(self, request, *args, **kwargs):
        suspension = self.get_object()
        if suspension.status in ('applied', 'restored'):
            return Response(
                {'error': '已生效或已恢复的停用单不能删除（已生效的请先执行恢复）'},
                status=status.HTTP_400_BAD_REQUEST
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """确认生效：一次性写入替代安排或停课记录，重复/并发确认只生效一次"""
        with transaction.atomic():
            # 锁定学期行，串行化同学期的并发确认，避免替代教室被重复占用
            suspension = get_object_or_404(
                ClassroomSuspension.objects.select_for_update(), pk=pk
            )
            Semester.objects.select_for_update().get(pk=suspension.semester_id)

            if suspension.status == 'applied':
                return Response({
                    'already_applied': True,
                    'message': '该停用单已确认生效，重复确认不会重复执行',
                    'suspension': ClassroomSuspensionSerializer(suspension).data
                })
            if suspension.status == 'restored':
                return Response(
                    {'error': '该停用单已恢复，不能再确认'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            _, blocking = apply_suspension(suspension)
            data = ClassroomSuspensionSerializer(suspension).data
            if blocking:
                return Response({
                    'already_applied': False,
                    'message': '存在无法安置的课程，整批保持原课表',
                    'suspension': data
                })
            return Response({
                'already_applied': False,
                'message': '停用处置已生效，替代安排/停课记录已写入',
                'suspension': data
            })

    @action(detail=True, methods=['post'])
    def restore(self, request, pk=None):
        """恢复教室：结束后停用期，教室可重新参与排课"""
        with transaction.atomic():
            suspension = get_object_or_404(
                ClassroomSuspension.objects.select_for_update(), pk=pk
            )
            if suspension.status == 'restored':
                return Response({
                    'already_restored': True,
                    'message': '该停用单已处于恢复状态',
                    'suspension': ClassroomSuspensionSerializer(suspension).data
                })
            suspension.status = 'restored'
            suspension.restored_at = timezone.now()
            suspension.save(update_fields=['status', 'restored_at', 'updated_at'])
        return Response({
            'already_restored': False,
            'message': '教室已恢复，可重新参与排课',
            'suspension': ClassroomSuspensionSerializer(suspension).data
        })
