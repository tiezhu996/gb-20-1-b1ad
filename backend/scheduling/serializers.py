from rest_framework import serializers
from .models import (
    ClassCourse, ScheduleEntry, Conflict, SwapRequest, Substitute,
    ClassroomSuspension, ClassroomSuspensionDisposition
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


class ClassroomSuspensionDispositionSerializer(serializers.ModelSerializer):
    action_display = serializers.CharField(source='get_action_display', read_only=True)
    original_classroom_name = serializers.CharField(
        source='original_classroom.name', read_only=True
    )
    new_classroom_name = serializers.CharField(
        source='new_classroom.name', read_only=True, allow_null=True
    )
    reason = serializers.CharField(source='suspension.reason', read_only=True)
    start_date = serializers.DateField(source='suspension.start_date', read_only=True)
    end_date = serializers.DateField(source='suspension.end_date', read_only=True)
    suspension_status = serializers.CharField(source='suspension.status', read_only=True)

    class Meta:
        model = ClassroomSuspensionDisposition
        fields = '__all__'


class ScheduleEntryDetailSerializer(serializers.ModelSerializer):
    course_name = serializers.CharField(source='course.name', read_only=True)
    teacher_name = serializers.CharField(source='teacher.name', read_only=True)
    classroom_name = serializers.CharField(source='classroom.name', read_only=True)
    class_name = serializers.CharField(source='class_id.name', read_only=True)
    original_teacher_name = serializers.CharField(
        source='original_teacher.name', read_only=True, allow_null=True
    )
    suspension_records = serializers.SerializerMethodField()

    class Meta:
        model = ScheduleEntry
        fields = '__all__'

    def get_suspension_records(self, obj):
        records = getattr(obj, 'suspension_dispositions', None)
        if records is None:
            return []
        return ClassroomSuspensionDispositionSerializer(
            records.all(), many=True
        ).data


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


class ClassroomSuspensionSerializer(serializers.ModelSerializer):
    classroom_name = serializers.CharField(source='classroom.name', read_only=True)
    semester_name = serializers.CharField(source='semester.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    disposal_display = serializers.CharField(source='get_disposal_display', read_only=True)
    dispositions = ClassroomSuspensionDispositionSerializer(many=True, read_only=True)

    class Meta:
        model = ClassroomSuspension
        fields = '__all__'


class ClassroomSuspensionCreateRequestSerializer(serializers.Serializer):
    classroom_id = serializers.IntegerField()
    semester_id = serializers.IntegerField()
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(allow_blank=False)

    def validate(self, attrs):
        if attrs['start_date'] > attrs['end_date']:
            raise serializers.ValidationError('停用开始日期不能晚于结束日期')
        return attrs


class ClassroomSuspensionConfirmRequestSerializer(serializers.Serializer):
    disposal = serializers.ChoiceField(choices=['relocate', 'cancel'])
    # entry_id -> new_classroom_id，教务员可在预览基础上调整替代教室
    assignments = serializers.DictField(
        child=serializers.IntegerField(), required=False
    )


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
