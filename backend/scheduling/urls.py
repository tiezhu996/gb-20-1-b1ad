from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ClassCourseViewSet, ScheduleEntryViewSet,
    ConflictViewSet, SwapRequestViewSet, SubstituteViewSet,
    ClassroomSuspensionViewSet
)

router = DefaultRouter()
router.register(r'class-courses', ClassCourseViewSet)
router.register(r'schedules', ScheduleEntryViewSet)
router.register(r'conflicts', ConflictViewSet)
router.register(r'swap-requests', SwapRequestViewSet)
router.register(r'substitutes', SubstituteViewSet)
router.register(r'classroom-suspensions', ClassroomSuspensionViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
