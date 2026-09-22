from django.db import models
from core.models import Classroom, Teacher, Class, Course, Semester


class ClassCourse(models.Model):
    class_id = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='course_assignments')
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['class_id', 'course', 'teacher', 'semester']
        ordering = ['semester', 'class_id']

    def __str__(self):
        return f"{self.class_id} - {self.course} ({self.teacher})"


class ScheduleEntry(models.Model):
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    class_id = models.ForeignKey(Class, on_delete=models.CASCADE, related_name='schedules')
    course = models.ForeignKey(Course, on_delete=models.CASCADE)
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE)
    classroom = models.ForeignKey(Classroom, on_delete=models.CASCADE)
    day_of_week = models.IntegerField(help_text='1-5 代表周一到周五')
    period = models.IntegerField(help_text='第几节课')
    is_locked = models.BooleanField(default=False, help_text='锁定后不参与自动重排')
    is_suspended = models.BooleanField(default=False, help_text='因教室临时停用等原因停课')
    schedule_note = models.CharField(
        max_length=255, blank=True,
        help_text='课表处置说明（如教室停用调整、停课原因）'
    )
    is_conflict = models.BooleanField(default=False)
    conflict_type = models.CharField(max_length=50, blank=True)
    original_teacher = models.ForeignKey(
        Teacher, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='substitute_for',
        help_text='如果是代课，记录原教师'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['semester', 'day_of_week', 'period']

    def __str__(self):
        return (f"{self.class_id} - {self.course} @ "
                f"周{self.day_of_week}第{self.period}节")


class Conflict(models.Model):
    CONFLICT_TYPES = [
        ('teacher', '教师冲突'),
        ('classroom', '教室冲突'),
        ('class', '班级冲突'),
    ]

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    conflict_type = models.CharField(max_length=20, choices=CONFLICT_TYPES)
    day_of_week = models.IntegerField()
    period = models.IntegerField()
    involved_entries = models.JSONField(default=list)
    message = models.TextField()
    resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_conflict_type_display()} @ 周{self.day_of_week}第{self.period}节"


class SwapRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', '待审批'),
        ('approved', '已批准'),
        ('rejected', '已拒绝'),
    ]

    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    requesting_teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name='swap_requests_made'
    )
    target_teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name='swap_requests_received'
    )
    entry1 = models.ForeignKey(
        ScheduleEntry, on_delete=models.CASCADE, related_name='swap_source'
    )
    entry2 = models.ForeignKey(
        ScheduleEntry, on_delete=models.CASCADE, related_name='swap_target'
    )
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"调课申请: {self.requesting_teacher} <-> {self.target_teacher}"


class ClassroomSuspension(models.Model):
    """教室临时停用登记单"""

    STATUS_CHOICES = [
        ('draft', '待确认'),
        ('applied', '已生效'),
        ('blocked', '无法安置'),
        ('restored', '已恢复'),
    ]

    classroom = models.ForeignKey(
        Classroom, on_delete=models.CASCADE, related_name='suspensions'
    )
    semester = models.ForeignKey(
        Semester, on_delete=models.CASCADE, related_name='classroom_suspensions'
    )
    start_date = models.DateField(help_text='停用开始日期')
    end_date = models.DateField(help_text='停用结束日期')
    reason = models.TextField(help_text='停用原因')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    blocking_reason = models.TextField(blank=True, help_text='无法安置时的阻断原因')
    applied_at = models.DateTimeField(null=True, blank=True, help_text='确认生效时间')
    restored_at = models.DateTimeField(null=True, blank=True, help_text='恢复时间')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.classroom.name} 停用 {self.start_date}~{self.end_date}"


class ClassroomSuspensionItem(models.Model):
    """教室停用处置明细（每个受影响课表条目一条）"""

    ACTION_CHOICES = [
        ('relocate', '调整教室'),
        ('suspend', '停课'),
        ('blocked', '无法安置'),
    ]

    suspension = models.ForeignKey(
        ClassroomSuspension, on_delete=models.CASCADE, related_name='items'
    )
    entry = models.ForeignKey(
        ScheduleEntry, on_delete=models.CASCADE, related_name='suspension_items'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    original_classroom = models.ForeignKey(
        Classroom, on_delete=models.CASCADE, related_name='+'
    )
    new_classroom = models.ForeignKey(
        Classroom, on_delete=models.SET_NULL, null=True, blank=True, related_name='+'
    )
    note = models.CharField(max_length=255, blank=True, help_text='处置说明或阻断原因')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['entry__day_of_week', 'entry__period', 'id']

    def __str__(self):
        return f"{self.suspension} - {self.entry} ({self.get_action_display()})"


class Substitute(models.Model):
    semester = models.ForeignKey(Semester, on_delete=models.CASCADE)
    original_teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name='absences'
    )
    substitute_teacher = models.ForeignKey(
        Teacher, on_delete=models.CASCADE, related_name='substitutions'
    )
    affected_entry = models.ForeignKey(
        ScheduleEntry, on_delete=models.CASCADE, related_name='substitute_record'
    )
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.substitute_teacher} 代 {self.original_teacher}"
