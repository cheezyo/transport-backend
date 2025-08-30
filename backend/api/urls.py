from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import CustomerViewSet, PricePlanViewSet, CustomerPricePlanViewSet, HolidayViewSet, LocationViewSet, VehicleViewSet, DriverViewSet, ShiftViewSet, TripViewSet, AssignmentViewSet, MeView
from django.urls import path, include

from .views import AirArrivalsFR24

router = DefaultRouter()
router.register(r'customers', CustomerViewSet)
router.register(r'price-plans', PricePlanViewSet)
router.register(r'customer-price-plans', CustomerPricePlanViewSet)
router.register(r'holidays', HolidayViewSet)
router.register(r'locations', LocationViewSet)
router.register(r'vehicles', VehicleViewSet)
router.register(r'drivers', DriverViewSet)
router.register(r'shifts', ShiftViewSet)
router.register(r'trips', TripViewSet)
router.register(r'assignments', AssignmentViewSet)

urlpatterns = [
    path('auth/token/',
         TokenObtainPairView.as_view(),
         name='token_obtain_pair'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('auth/me/', MeView.as_view(), name='auth_me'),
    path('', include(router.urls)),
    path("air/arrivals-fr24", AirArrivalsFR24.as_view()),
]
