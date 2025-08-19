from datetime import datetime
from typing import Dict, Any
from .models import CustomerPricePlan, Holiday, Location, PricePlan
from datetime import datetime
from datetime import time as time_cls


def is_holiday(d) -> bool:
    return Holiday.objects.filter(date=d).exists()


def in_night_window(t, start, end) -> bool:
    if not start or not end:
        return False
    # if window doesn't span midnight
    if start <= end:
        return start <= t <= end
    # spans midnight
    return t >= start or t <= end


def pricing_for_trip(data):
    base_price = 900
    base_pax_included = 7
    extra_pax_price = 0
    night_surcharge = 0
    night_start = None
    night_end = None
    holiday_surcharge = 0
    stop1_surcharge = 0  # NYTT
    stop2_surcharge = 0  # NYTT

    # Hent prisplan
    customer = data.get("customer")
    if customer:
        link = CustomerPricePlan.objects.filter(
            customer=customer).select_related("price_plan").first()
        if link and link.price_plan and link.price_plan.active:
            pp: PricePlan = link.price_plan
            base_price = pp.base_price
            base_pax_included = pp.base_pax_included
            extra_pax_price = pp.extra_pax_price
            night_surcharge = pp.night_surcharge
            night_start = pp.night_start
            night_end = pp.night_end
            holiday_surcharge = pp.holiday_surcharge
            stop1_surcharge = pp.stop1_surcharge  # NYTT
            stop2_surcharge = pp.stop2_surcharge  # NYTT

    # PAX
    pax = int(data.get("pax") or 1)
    price = base_price
    if pax > base_pax_included:
        price += (pax - base_pax_included) * int(extra_pax_price)

    # Natt
    st = data.get("start_time")
    if isinstance(st, str):
        st = datetime.strptime(st, "%H:%M").time()

    def in_night_window(t, start, end):
        if not start or not end:
            return False
        if start <= end:
            return start <= t <= end
        return t >= start or t <= end

    if in_night_window(st, night_start, night_end):
        price += int(night_surcharge)

    # Helligdag
    d = data.get("date")
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    if Holiday.objects.filter(date=d).exists():
        price += int(holiday_surcharge)

    # --- Stopp-tillegg (NYTT) ---
    # Vi regner et stopp som tilstedeværelse av stop1/stop2 enten med ID i validated data
    # eller hvis serializer fikk navn-feltene (origin/dest håndteres som vanlig).
    stops = 0
    if data.get("stop1_location") is not None or data.get("stop1_name"):
        stops += 1
    if data.get("stop2_location") is not None or data.get("stop2_name"):
        stops += 1

    if stops == 1:
        price += int(stop1_surcharge)
    elif stops >= 2:
        price += int(stop2_surcharge)

    return int(price)
