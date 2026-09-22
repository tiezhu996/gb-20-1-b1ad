from django.contrib import admin
from .models import (
    ClassCourse, ScheduleEntry, Conflict, SwapRequest, Substitute,
    ClassroomSuspension, ClassroomSuspensionDisposition
)

admin.site.register(ClassCourse)
admin.site.register(ScheduleEntry)
admin.site.register(Conflict)
admin.site.register(SwapRequest)
admin.site.register(Substitute)
admin.site.register(ClassroomSuspension)
admin.site.register(ClassroomSuspensionDisposition)
