from rest_framework import serializers
from django.contrib.auth.models import User
from django.db import transaction
from .models import Customer, PricePlan, CustomerPricePlan, Holiday, Location, Vehicle, Driver, Shift, Trip, Assignment
from .services import pricing_for_trip


class UserSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']


class CustomerSerializer(serializers.ModelSerializer):

    class Meta:
        model = Customer
        fields = '__all__'


class PricePlanSerializer(serializers.ModelSerializer):

    class Meta:
        model = PricePlan
        fields = '__all__'


class CustomerPricePlanSerializer(serializers.ModelSerializer):

    class Meta:
        model = CustomerPricePlan
        fields = '__all__'


class HolidaySerializer(serializers.ModelSerializer):

    class Meta:
        model = Holiday
        fields = '__all__'


class LocationSerializer(serializers.ModelSerializer):

    class Meta:
        model = Location
        fields = '__all__'


class VehicleSerializer(serializers.ModelSerializer):

    class Meta:
        model = Vehicle
        fields = ["id", "name", "vehicle_type", "reg_no", "seats", "active"]


class DriverSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    user_id = serializers.PrimaryKeyRelatedField(source="user",
                                                 queryset=User.objects.all(),
                                                 write_only=True,
                                                 required=False,
                                                 allow_null=True)

    class Meta:
        model = Driver
        fields = ["id", "name", "phone", "active", "user", "user_id"]


class ShiftSerializer(serializers.ModelSerializer):

    class Meta:
        model = Shift
        fields = '__all__'


class TripSerializer(serializers.ModelSerializer):
    # eksisterende write-only felter for smarte lokasjoner
    origin_name = serializers.CharField(write_only=True, required=False)
    destination_name = serializers.CharField(write_only=True, required=False)
    stop1_name = serializers.CharField(write_only=True, required=False)
    stop2_name = serializers.CharField(write_only=True, required=False)

    # valgfri tildeling via Trip-CRUD (enkelt i UI)
    driver_id = serializers.IntegerField(write_only=True,
                                         required=False,
                                         allow_null=True)

    # NYTT: lesbart felt for hvem som er tildelt
    current_driver = serializers.SerializerMethodField(read_only=True)
    price = serializers.IntegerField(required=False, allow_null=True)

    class Meta:
        model = Trip
        fields = [
            "id",
            "date",
            "start_time",
            "duration_min",
            "origin_location",
            "destination_location",
            "origin_name",
            "destination_name",
            "stop1_location",
            "stop2_location",
            "stop1_name",
            "stop2_name",
            "customer",
            "pax",
            "price",
            "status",  # read-only (styres automatisk)
            "comment",
            "exception_note",
            "vehicle",
            "created_at",
            "driver_id",  # write-only
            "current_driver",  # read-only
        ]
        read_only_fields = ["status", "created_at", "current_driver"]

    def validate(self, attrs):
        """
        price er valgfritt KUN dersom kunden har prisplan.
        Hvis ingen kunde, eller kunde uten prisplan → price må sendes.
        """
        customer = attrs.get("customer",
                             getattr(self.instance, "customer", None))
        price = attrs.get("price", None)
        if price == "":
            price = None

        # pris ikke satt: må ha prisplan
        if not customer:
            raise serializers.ValidationError(
                {"price": "Price is required when no customer is selected."})

        has_plan = CustomerPricePlan.objects.filter(customer=customer).exists()
        if not has_plan:
            raise serializers.ValidationError({
                "price":
                "Price is required because this customer has no price plan."
            })

        return attrs

    def _ensure_location(self, name: str):
        loc, _ = Location.objects.get_or_create(name=name.strip())
        return loc

    def get_current_driver(self, obj):
        a = getattr(obj, "assignment", None)
        if not a:
            return None
        d = a.driver
        return {
            "id":
            d.id,
            "name":
            getattr(d, "name", None) or getattr(d.user, "username", str(d.id))
        }

    @transaction.atomic
    def create(self, validated):
        # Navn → FK
        origin_name = validated.pop("origin_name", None)
        destination_name = validated.pop("destination_name", None)
        stop1_name = validated.pop("stop1_name", None)
        stop2_name = validated.pop("stop2_name", None)
        if origin_name and not validated.get("origin_location"):
            validated["origin_location"] = self._ensure_location(origin_name)
        if destination_name and not validated.get("destination_location"):
            validated["destination_location"] = self._ensure_location(
                destination_name)
        if stop1_name and not validated.get("stop1_location"):
            validated["stop1_location"] = self._ensure_location(stop1_name)
        if stop2_name and not validated.get("stop2_location"):
            validated["stop2_location"] = self._ensure_location(stop2_name)

        # Pris hvis utelatt
        if ("price" not in validated) or (validated.get("price")
                                          in (None, "")):
            validated["price"] = pricing_for_trip(validated)

        # Håndter driver_id for status
        driver_id = validated.pop("driver_id", None)
        validated["status"] = "assigned" if driver_id else "unassigned"

        trip = super().create(validated)

        # Opprett Assignment hvis driver fulgte med
        if driver_id:
            try:
                driver = Driver.objects.get(pk=driver_id, active=True)
            except Driver.DoesNotExist:
                raise serializers.ValidationError(
                    {"driver_id": "Driver not found or inactive"})
            Assignment.objects.update_or_create(trip=trip,
                                                defaults={"driver": driver})
        return trip

    @transaction.atomic
    def update(self, instance, validated):
        # Navn → FK ved oppdatering
        origin_name = validated.pop("origin_name", None)
        destination_name = validated.pop("destination_name", None)
        stop1_name = validated.pop("stop1_name", None)
        stop2_name = validated.pop("stop2_name", None)
        if origin_name:
            validated["origin_location"] = self._ensure_location(origin_name)
        if destination_name:
            validated["destination_location"] = self._ensure_location(
                destination_name)
        if stop1_name:
            validated["stop1_location"] = self._ensure_location(stop1_name)
        if stop2_name:
            validated["stop2_location"] = self._ensure_location(stop2_name)

        driver_id = validated.pop("driver_id", None)

        trip = super().update(instance, validated)

        # Endre tildeling om driver_id ble sendt
        if driver_id is not None:
            if driver_id in ("", None):
                # fjern tildeling
                Assignment.objects.filter(trip=trip).delete()
                if trip.status != "unassigned":
                    trip.status = "unassigned"
                    trip.save(update_fields=["status"])
            else:
                try:
                    driver = Driver.objects.get(pk=driver_id, active=True)
                except Driver.DoesNotExist:
                    raise serializers.ValidationError(
                        {"driver_id": "Driver not found or inactive"})
                Assignment.objects.update_or_create(
                    trip=trip, defaults={"driver": driver})
                if trip.status == "unassigned":
                    trip.status = "assigned"
                    trip.save(update_fields=["status"])
        return trip


class AssignmentSerializer(serializers.ModelSerializer):

    class Meta:
        model = Assignment
        fields = '__all__'
        read_only_fields = ['assigned_at']
