from django.contrib import admin
from django import forms
from django.utils import timezone
from .models import (
    Customer,
    PricePlan,
    CustomerPricePlan,
    Holiday,
    Location,
    Vehicle,
    Driver,
    Shift,
    Trip,
    Assignment,
)
from .services import pricing_for_trip

# --- Enkle registreringer ---
admin.site.register(Customer)
admin.site.register(CustomerPricePlan)
admin.site.register(Holiday)
admin.site.register(Location)
admin.site.register(Shift)
admin.site.register(Assignment)


@admin.register(PricePlan)
class PricePlanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "base_price",
        "base_pax_included",
        "extra_pax_price",
        "night_start",
        "night_end",
        "night_surcharge",
        "holiday_surcharge",
        "stop1_surcharge",
        "stop2_surcharge",
        "active",
    )
    list_filter = ("active", )


@admin.register(Driver)
class DriverAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "active", "user")
    list_filter = ("active", )
    search_fields = ("name", "phone", "user__username")


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "vehicle_type", "reg_no", "seats", "active")
    list_filter = ("vehicle_type", "active")
    search_fields = ("name", "reg_no")


# --- Trip admin form (din eksisterende logikk) ---
class TripAdminForm(forms.ModelForm):

    class Meta:
        model = Trip
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        price = cleaned.get("price")
        # tillat manglende price kun hvis kunden har prisplan
        if price in (None, ""):
            if not customer or not CustomerPricePlan.objects.filter(
                    customer=customer).exists():
                self.add_error(
                    "price", "Pris er påkrevd når kunden ikke har prisplan.")
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        # beregn pris automatisk hvis den mangler, men prisplan finnes
        if obj.price in (
                None,
                "") and obj.customer and CustomerPricePlan.objects.filter(
                    customer=obj.customer).exists():
            data = {
                "date": obj.date,
                "start_time": obj.start_time,
                "pax": obj.pax,
                "customer": obj.customer,
                "origin_location": obj.origin_location,
                "destination_location": obj.destination_location,
                "stop1_location": obj.stop1_location,
                "stop2_location": obj.stop2_location,
            }
            obj.price = pricing_for_trip(data)
        if commit:
            obj.save()
            self.save_m2m()
        return obj


# --- Trip admin med fakturafelt ---
@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    form = TripAdminForm

    list_display = (
        "id",
        "date",
        "start_time",
        "customer",
        "status",
        "invoiced",
        "invoiced_at",
        "invoiced_by",
        "flight_number",
        "po_number",
    )
    list_filter = (
        "status",
        "invoiced",  # ⬅️ nytt
        "date",
        "customer",
    )
    search_fields = ("id", "customer__name", "flight_number", "po_number")

    # Vi lar admin redigere 'invoiced', men holder 'invoiced_at/by' readonly.
    readonly_fields = ("invoiced_at", "invoiced_by")

    def save_model(self, request, obj, form, change):
        """
        Hvis 'invoiced' endres i admin:
          - setter vi invoiced_at/invoiced_by automatisk når True
          - rydder feltene når False
        """
        if change and "invoiced" in form.changed_data:
            if obj.invoiced:
                obj.invoiced_at = timezone.now()
                obj.invoiced_by = request.user
            else:
                obj.invoiced_at = None
                obj.invoiced_by = None
        super().save_model(request, obj, form, change)
