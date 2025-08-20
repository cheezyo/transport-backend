from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Customer(models.Model):
    name = models.CharField(max_length=200, unique=True)
    orgnr = models.CharField(max_length=50, blank=True, null=True)
    invoice_email = models.EmailField(blank=True, null=True)

    def __str__(self):
        return self.name


class PricePlan(models.Model):
    name = models.CharField(max_length=120, unique=True)
    base_price = models.IntegerField(default=900)
    base_pax_included = models.IntegerField(default=7)
    extra_pax_price = models.IntegerField(default=0)
    night_start = models.TimeField(null=True, blank=True)
    night_end = models.TimeField(null=True, blank=True)
    night_surcharge = models.IntegerField(default=0)
    holiday_surcharge = models.IntegerField(default=0)

    # NYTT:
    stop1_surcharge = models.IntegerField(
        default=0)  # tillegg når turen har 1 stopp
    stop2_surcharge = models.IntegerField(
        default=0)  # tillegg når turen har 2 stopp

    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class CustomerPricePlan(models.Model):
    customer = models.OneToOneField(Customer,
                                    on_delete=models.CASCADE,
                                    related_name='price_plan_link')
    price_plan = models.ForeignKey(PricePlan,
                                   on_delete=models.PROTECT,
                                   related_name='customers')


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=120)
    country_code = models.CharField(max_length=5, default='NO')

    def __str__(self):
        return f"{self.date} {self.name}"


class Location(models.Model):
    name = models.CharField(max_length=255, unique=True)
    address = models.CharField(max_length=255, blank=True, null=True)
    lat = models.FloatField(blank=True, null=True)
    lon = models.FloatField(blank=True, null=True)
    tags = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return self.name


class Vehicle(models.Model):
    VEHICLE_TYPES = [
        ("car", "Car"),
        ("bus", "Bus"),
    ]

    # NYTT: navn på kjøretøyet
    name = models.CharField(max_length=120)

    vehicle_type = models.CharField(max_length=10, choices=VEHICLE_TYPES)
    reg_no = models.CharField(max_length=20, unique=True)
    seats = models.IntegerField(default=8)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.reg_no})"


class Driver(models.Model):
    user = models.OneToOneField(User,
                                on_delete=models.SET_NULL,
                                null=True,
                                blank=True,
                                related_name='+')
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=50, blank=True, null=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Shift(models.Model):
    driver = models.ForeignKey(Driver,
                               on_delete=models.CASCADE,
                               related_name='shifts')
    start = models.DateTimeField()
    end = models.DateTimeField()
    status = models.CharField(max_length=20, default='planned')

    def __str__(self):
        return f"{self.driver.name} {self.start:%Y-%m-%d %H:%M}–{self.end:%H:%M}"


class Trip(models.Model):
    STATUS_CHOICES = [
        ("unassigned", "unassigned"),
        ("assigned", "assigned"),
        ("exception", "exception"),
    ]

    date = models.DateField()
    start_time = models.TimeField()
    duration_min = models.IntegerField(default=30)

    origin_location = models.ForeignKey("Location",
                                        on_delete=models.PROTECT,
                                        related_name="trips_from")
    destination_location = models.ForeignKey("Location",
                                             on_delete=models.PROTECT,
                                             related_name="trips_to")

    # NYTT: valgfrie stopp
    stop1_location = models.ForeignKey("Location",
                                       on_delete=models.PROTECT,
                                       null=True,
                                       blank=True,
                                       related_name="trips_stop1")
    stop2_location = models.ForeignKey("Location",
                                       on_delete=models.PROTECT,
                                       null=True,
                                       blank=True,
                                       related_name="trips_stop2")

    customer = models.ForeignKey("Customer",
                                 on_delete=models.SET_NULL,
                                 null=True,
                                 blank=True,
                                 related_name="trips")
    pax = models.IntegerField()
    price = models.IntegerField(null=True, blank=True)
    status = models.CharField(max_length=20,
                              choices=STATUS_CHOICES,
                              default="unassigned")
    comment = models.TextField(blank=True, null=True)
    exception_note = models.TextField(blank=True, null=True)

    vehicle = models.ForeignKey("Vehicle",
                                on_delete=models.SET_NULL,
                                null=True,
                                blank=True,
                                related_name="trips")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.date} {self.start_time} {self.origin_location}→{self.destination_location}"


class Assignment(models.Model):
    trip = models.OneToOneField(Trip,
                                on_delete=models.CASCADE,
                                related_name='assignment')
    driver = models.ForeignKey(Driver,
                               on_delete=models.CASCADE,
                               related_name='assignments')
    assigned_by = models.ForeignKey(User,
                                    on_delete=models.SET_NULL,
                                    null=True,
                                    blank=True,
                                    related_name='made_assignments')
    assigned_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.trip} → {self.driver.name}"
