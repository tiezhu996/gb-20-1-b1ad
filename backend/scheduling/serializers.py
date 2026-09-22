from rest_framework import serializers
from .models import (
    ClassCourse, ScheduleEntry, Conflict, SwapRequest, Substitute,
    ClassroomSuspension, ClassroomSuspensionItem
)


class ClassCourseSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)
    class_name = serializers.CharField(source='class_id.name', read_only=True)
    weekly_hours = serializers.IntegerField(source='course.weekly_hours', read_only=True)

    class Meta:
        model = ClassCourse
        fields = '__all__'


class ScheduleEntrySerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)
    classroom_name = serializers.CharField(source='classroom.name', read_only=True)
    class_name = serializers.CharField(source='class_id.name', read_only=True)

    class Meta:
        model = ScheduleEntry
        fields = '__all__'


class ScheduleEntryDetailSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)
    classroom_name = serializers.CharField(source='classroom.name', read_only=True)
    class_name = serializers.CharField(source='class_id.name', read_only=True)
    original_teacher_name = serializers.CharField(
        source='original_teacher.name', read_only=True, allow_null=True
    )

    class Meta:
        model = ScheduleEntry
        fields = '__all__'


class ConflictSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conflict
        fields = '__all__'


class SwapRequestSerializer(serializers.ModelSerializer):
    requesting_teacher_name = serializers.CharField(
        source='requesting_teacher.name', read_only=True
    )
    target_teacher_name = serializers.CharField(
        source='target_teacher.name', read_only=True
    )

    class Meta:
        model = SwapRequest
        fields = '__all__'


class SubstituteSerializer(serializers.ModelSerializer):
    original_teacher_name = serializers.CharField(
        source='original_teacher.name', read_only=True
    )
    substitute_teacher_name = serializers.CharField(
        source='substitute_teacher.name', read_only=True
    )

    class Meta:
        model = Substitute
        fields = '__all__'


class ClassroomSuspensionItemSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='entry.course.name', read_only=True)
    class_name = serializers.CharField(source='entry.class_id.name', read_only=True)
    teacher_name = serializers.CharField(source='entry.teacher.name', read_only=True)
    day_of_week = serializers.IntegerField(source='entry.day_of_week', read_only=True)
    period = serializers.IntegerField(source='entry.period', read_only=True)
    is_locked = serializers.BooleanField(source='entry.is_locked', read_only=True)
    original_classroom_name = serializers.CharField(
        source='original_classroom.name', read_only=True
    )
    new_classroom_name = serializers.CharField(
        source='new_classroom.name', read_only=True, allow_null=True
    )
    action_display = serializers.CharField(source='get_action_display', read_only=True)

    class Meta:
        model = ClassroomSuspensionItem
        fields = '__all__'


class ClassroomSuspensionSerializer(serializers.ModelSerializer):
    classroom_name = serializers.CharField(source='classroom.name', read_only=True)
    semester_name = serializers.CharField(source='semester.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    items = ClassroomSuspensionItemSerializer(many=True, read_only=True)

    class Meta:
        model = ClassroomSuspension
        fields = '__all__'


class ClassroomSuspensionCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassroomSuspension
        fields = ['classroom', 'semester', 'start_date', 'end_date', 'reason']

    def validate(self, attrs):
        start = attrs['start_date']
        end = attrs['end_date']
        if start > end:
            raise serializers.ValidationError(
                {'end_date': '停用结束日期不能早于开始日期'}
            )
        semester = attrs['semester']
        if start > semester.end_date or end < semester.start_date:
            raise serializers.ValidationError(
                {'start_date': '停用区间与学期日期范围无交集'}
            )
        return attrs


class AutoScheduleRequestSerializer(serializers.Serializer):
    semester_id = serializers.IntegerField()
    respect_locked = serializers.BooleanField(default=True)


class ConflictCheckSerializer(serializers.Serializer):
    semester_id = serializers.IntegerField()


class SwapScheduleRequestSerializer(serializers.Serializer):
    entry1_id = serializers.IntegerField()
    entry2_id = serializers.IntegerField()
    reason = serializers.CharField(required=False)


class SubstituteRequestSerializer(serializers.Serializer):
    entry_id = serializers.IntegerField()
    substitute_teacher_id = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField()
