from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.db.models import Q
from .models import Customer, PricePlan, CustomerPricePlan, Holiday, Location, Vehicle, Driver, Shift, Trip, Assignment
from .serializers import CustomerSerializer, PricePlanSerializer, CustomerPricePlanSerializer, HolidaySerializer, LocationSerializer, VehicleSerializer, DriverSerializer, ShiftSerializer, TripSerializer, AssignmentSerializer
from .services import pricing_for_trip
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from .serializers import UserSerializer


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all().order_by('name')
    serializer_class = CustomerSerializer


class PricePlanViewSet(viewsets.ModelViewSet):
    queryset = PricePlan.objects.all().order_by('name')
    serializer_class = PricePlanSerializer


class CustomerPricePlanViewSet(viewsets.ModelViewSet):
    queryset = CustomerPricePlan.objects.select_related(
        'customer', 'price_plan').all()
    serializer_class = CustomerPricePlanSerializer


class HolidayViewSet(viewsets.ModelViewSet):
    queryset = Holiday.objects.all().order_by('date')
    serializer_class = HolidaySerializer


class LocationViewSet(viewsets.ModelViewSet):
    queryset = Location.objects.all().order_by('name')
    serializer_class = LocationSerializer

    @action(detail=False, methods=['get'])
    def search(self, request):
        q = request.query_params.get('q', '').strip()
        qs = self.get_queryset()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(address__icontains=q))
        data = [{'id': x.id, 'name': x.name} for x in qs[:20]]
        return Response(data)


class VehicleViewSet(viewsets.ModelViewSet):
    queryset = Vehicle.objects.all().order_by('reg_no')
    serializer_class = VehicleSerializer


class DriverViewSet(viewsets.ModelViewSet):
    queryset = Driver.objects.all().order_by('name')
    serializer_class = DriverSerializer


class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.all().order_by('-start')
    serializer_class = ShiftSerializer


class TripViewSet(viewsets.ModelViewSet):
    queryset = Trip.objects.select_related("origin_location",
                                           "destination_location", "vehicle",
                                           "customer").all().order_by(
                                               "date", "start_time")
    serializer_class = TripSerializer

    # --- Enkle filtre ---
    def get_queryset(self):
        qs = super().get_queryset()
        status_ = self.request.query_params.get("status")
        date_ = self.request.query_params.get("date")
        driver_id = self.request.query_params.get("driver")

        if status_:
            qs = qs.filter(status=status_)
        if date_:
            qs = qs.filter(date=date_)
        if driver_id:
            qs = qs.filter(assignment__driver_id=driver_id)

        return qs

    # --- Actions for tildeling ---
    @action(detail=True, methods=["post"])
    @transaction.atomic
    def assign_driver(self, request, pk=None):
        trip = self.get_object()
        driver_id = request.data.get("driver_id")
        if not driver_id:
            return Response({"detail": "driver_id is required"}, status=400)

        driver = get_object_or_404(Driver, pk=driver_id, active=True)
        Assignment.objects.update_or_create(
            trip=trip,
            defaults={
                "driver":
                driver,
                "assigned_by":
                request.user if request.user.is_authenticated else None
            })
        if trip.status == "unassigned":
            trip.status = "assigned"
            trip.save(update_fields=["status"])
        return Response({"status": "ok", "trip": trip.id, "driver": driver.id})

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def unassign_driver(self, request, pk=None):
        trip = self.get_object()
        Assignment.objects.filter(trip=trip).delete()
        if trip.status != "unassigned":
            trip.status = "unassigned"
            trip.save(update_fields=["status"])
        return Response({"status": "ok", "trip": trip.id})


class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.select_related(
        'trip', 'driver').all().order_by('-assigned_at')
    serializer_class = AssignmentSerializer


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
