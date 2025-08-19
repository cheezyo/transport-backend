from django.contrib import admin
from django import forms
from .models import Customer, PricePlan, CustomerPricePlan, Holiday, Location, Vehicle, Driver, Shift, Trip, Assignment
from .services import pricing_for_trip

admin.site.register(Customer)
admin.site.register(CustomerPricePlan)
admin.site.register(Holiday)
admin.site.register(Location)
admin.site.register(Shift)
admin.site.register(Trip)
admin.site.register(Assignment)


@admin.register(PricePlan)
class PricePlanAdmin(admin.ModelAdmin):
  list_display = ("id", "name", "base_price", "base_pax_included",
                  "extra_pax_price", "night_start", "night_end",
                  "night_surcharge", "holiday_surcharge", "stop1_surcharge",
                  "stop2_surcharge", "active")
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
        self.add_error("price",
                       "Pris er påkrevd når kunden ikke har prisplan.")
    return cleaned

  def save(self, commit=True):
    obj = super().save(commit=False)
    # beregn pris automatisk hvis den mangler, men prisplan finnes
    if obj.price in (None,
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
