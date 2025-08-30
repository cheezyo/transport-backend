from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db.models import Q
from .models import (Customer, PricePlan, CustomerPricePlan, Holiday, Location,
                     Vehicle, Driver, Shift, Trip, Assignment)
from .serializers import (CustomerSerializer, PricePlanSerializer,
                          CustomerPricePlanSerializer, HolidaySerializer,
                          LocationSerializer, VehicleSerializer,
                          DriverSerializer, ShiftSerializer, TripSerializer,
                          AssignmentSerializer, UserSerializer)
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from datetime import date as _date
from django.utils import timezone
from rest_framework import status as http_status
import datetime as dt
import requests
import xml.etree.ElementTree as ET
import re
from django.utils.timezone import now as dj_now

# ---------------- Avinor ETA ----------------
AVINOR_XML = "https://asrv.avinor.no/XmlFeed/v1.0"
SVG_IATA = "SVG"  # Stavanger

_BAD_XML_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF]")
_UNESCAPED_AMP = re.compile(
    r"&(?!(?:amp|lt|gt|quot|apos|#[0-9]+|#[xX][0-9a-fA-F]+);)")


def _norm_fno(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _clean_xml_text(text: str) -> str:
    text = text.lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _BAD_XML_CHARS.sub("", text)
    text = _UNESCAPED_AMP.sub("&amp;", text)
    return text.strip()


# api/views.py (kort test-API)
class AirArrivalsFR24(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        from .integrations.fr24 import airport_arrivals
        airport = (request.query_params.get("airport")
                   or "SVG").strip().upper()
        hours_from = int(request.query_params.get("hours_from", "0"))
        hours_to = int(request.query_params.get("hours_to", "6"))
        limit = int(request.query_params.get("limit", "100"))
        raw = request.query_params.get("raw")
        try:
            if raw:
                data = airport_arrivals(airport,
                                        hours_from,
                                        hours_to,
                                        limit,
                                        return_raw=True)
                return Response(data)
            data = airport_arrivals(airport,
                                    hours_from,
                                    hours_to,
                                    limit,
                                    return_raw=False)
            return Response({
                "airport": airport,
                "count": len(data),
                "results": data
            })
        except requests.HTTPError as e:
            return Response({"detail": f"FR24 HTTP: {e}"}, status=502)
        except Exception as e:
            return Response({"detail": str(e)}, status=502)


class AirEtaAvinor(APIView):
    """
    GET /api/air/eta-avinor?number=DY540
      Query (valgfri):
        - hours_from: default 0
        - hours_to:   default 12
        - raw:        returner renset XML (tekst)
        - debug:      returner metadata + alle matcher
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        number = (request.query_params.get("number") or "").strip()
        if not number:
            return Response({"detail": "number er påkrevd"}, status=400)

        try:
            hours_from = int(request.query_params.get("hours_from", "0"))
            hours_to = int(request.query_params.get("hours_to", "12"))
        except ValueError:
            return Response({"detail": "hours_from/hours_to må være heltall"},
                            status=400)

        want_raw = request.query_params.get("raw")
        want_debug = request.query_params.get("debug")

        # Riktige parameternavn og headers, og IKKE følg redirects
        params = {
            "airport": SVG_IATA,
            "direction": "A",  # A=Arrivals, D=Departures
            "timeFrom": str(hours_from),
            "timeTo": str(hours_to),
        }
        headers = {
            "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.8",
            "User-Agent": "transport-backend/1.0",
        }
        try:
            r = requests.get(
                AVINOR_XML,
                params=params,
                headers=headers,
                timeout=15,
                allow_redirects=False,
            )

            if 300 <= r.status_code < 400:
                return Response(
                    {
                        "detail": "Avinor svarte med redirect (ikke XML).",
                        "status": r.status_code,
                        "location": r.headers.get("Location"),
                    },
                    status=502,
                )

            r.raise_for_status()
            text = r.text

            ct = (r.headers.get("Content-Type") or "").lower()
            if "xml" not in ct and not text.lstrip().startswith("<"):
                return Response(
                    {
                        "detail":
                        "Avinor returnerte ikke XML (mulig HTML/cookie-side).",
                        "content_type": ct,
                        "snippet": text[:400],
                    },
                    status=502,
                )
        except requests.HTTPError as e:
            return Response({"detail": f"Avinor HTTP: {e}"}, status=502)
        except Exception as e:
            return Response({"detail": str(e)}, status=502)

        cleaned = _clean_xml_text(text)
        if want_raw:
            return Response(cleaned)

        # Parse XML (med fallback)
        try:
            root = ET.fromstring(cleaned.encode("utf-8"))
        except Exception:
            hard = cleaned.replace("&", "&amp;")
            hard = _clean_xml_text(hard)
            try:
                root = ET.fromstring(hard.encode("utf-8"))
            except Exception as e2:
                return Response({"detail": f"Kunne ikke parse XML: {e2}"},
                                status=502)

        # Match på flight_id (eksakt, normalisert)
        needle = _norm_fno(number)
        flights = root.findall(".//flight")
        hits = []
        for fl in flights:
            fid = (fl.findtext("flight_id") or "").strip()
            if _norm_fno(fid) != needle:
                continue

            # Les status-elementets attributter (kode + tid)
            status_el = fl.find("status")
            status_code = status_time_s = None
            if status_el is not None:
                status_code = status_el.attrib.get("code") or None
                status_time_s = status_el.attrib.get(
                    "time") or None  # ISO, ofte med 'Z'

            est_arrival = (fl.findtext("est_arrival_time") or "").strip()
            sched = (fl.findtext("schedule_time") or "").strip()
            airline = (fl.findtext("airline") or "").strip()
            route = (fl.findtext("route") or "").strip()
            status_txt = (fl.findtext("status") or "").strip()  # kan være tom

            # Beste ETA-kilde: status@time (E/A) > est_arrival_time > schedule_time
            eta_dt = None
            if status_time_s and (status_code in ("E", "A")):
                eta_dt = _parse_dt(status_time_s.replace("Z", "+00:00"))
            if eta_dt is None:
                eta_dt = _parse_dt(est_arrival) or _parse_dt(sched)

            hits.append({
                "flight": fid,
                "airline": airline or None,
                "route": route or None,
                "status": status_txt or status_code or None,
                "status_code": status_code,
                "status_time": status_time_s,
                "eta_iso": (eta_dt.isoformat() if eta_dt else None),
                "est_arrival_time": est_arrival or None,
                "schedule_time": sched or None,
            })

        if want_debug:
            sample = [{
                "flight_id":
                fl.findtext("flight_id"),
                "schedule_time":
                fl.findtext("schedule_time"),
                "est_arrival_time":
                fl.findtext("est_arrival_time"),
                "status_code": (fl.find("status").attrib.get("code")
                                if fl.find("status") is not None else None),
                "status_time": (fl.find("status").attrib.get("time")
                                if fl.find("status") is not None else None),
            } for fl in flights[:3]]
            return Response({
                "params_used": params,
                "total_in_feed": len(flights),
                "matches": len(hits),
                "sample": sample,
                "results": hits,
            })

        if not hits:
            return Response({"detail": "Ingen treff i Avinor-feed."},
                            status=404)

        # Velg beste: nærmest nå i fremtiden, ellers siste i fortiden (UTC-aware)
        def score(item):
            eta = _parse_dt(item["eta_iso"]) if item["eta_iso"] else None
            if not eta:
                return (1, dt.datetime.max.replace(tzinfo=dt.timezone.utc))
            if eta.tzinfo is None:
                eta = eta.replace(tzinfo=dt.timezone.utc)
            now = dt.datetime.now(dt.timezone.utc)
            return (0, eta) if eta >= now else (1, eta)

        picked = sorted(hits, key=score)[0]

        # Konverter tider til lokal tid (Europe/Oslo fra Django settings)
        def _to_local(x):
            if not x:
                return None
            if x.tzinfo is None:
                x = x.replace(tzinfo=dt.timezone.utc)
            return x.astimezone(timezone.get_current_timezone())

        eta_utc = _parse_dt(
            picked["eta_iso"]) if picked.get("eta_iso") else None
        sched_utc = _parse_dt(
            picked["schedule_time"]) if picked.get("schedule_time") else None
        est_utc = _parse_dt(picked["est_arrival_time"]) if picked.get(
            "est_arrival_time") else None

        eta_local = _to_local(eta_utc) if eta_utc else None
        sched_local = _to_local(sched_utc) if sched_utc else None
        est_local = _to_local(est_utc) if est_utc else None

        return Response({
            "flight":
            picked["flight"],
            "dest_iata":
            SVG_IATA,
            "eta": (eta_local.isoformat() if eta_local else None),  # lokal tid
            "eta_utc": (eta_utc.isoformat() if eta_utc else None),  # rå UTC
            "status":
            picked["status"],
            "status_code":
            picked["status_code"],
            "status_time":
            picked["status_time"],  # rå status-tid fra Avinor
            "source":
            "avinor-xml",
            "airline":
            picked["airline"],
            "route":
            picked["route"],
            "est_arrival_time": (est_local.isoformat() if est_local else None),
            "est_arrival_time_utc": (est_utc.isoformat() if est_utc else None),
            "schedule_time":
            (sched_local.isoformat() if sched_local else None),
            "schedule_time_utc":
            (sched_utc.isoformat() if sched_utc else None),
        })


# ---------------- Lokale ViewSets ----------------
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

    def get_queryset(self):
        qs = super().get_queryset()
        status_ = self.request.query_params.get("status")
        date_ = self.request.query_params.get("date")
        driver_id = self.request.query_params.get("driver")
        month_ = self.request.query_params.get("month")
        week_ = self.request.query_params.get("week")
        customer_id = self.request.query_params.get("customer")
        invoiced_ = self.request.query_params.get("invoiced")

        if status_:
            qs = qs.filter(status=status_)
        if date_:
            qs = qs.filter(date=date_)
        if driver_id:
            qs = qs.filter(assignment__driver_id=driver_id)
        if month_:
            try:
                year_s, mon_s = month_.split("-", 1)
                qs = qs.filter(date__year=int(year_s), date__month=int(mon_s))
            except Exception:
                pass
        if week_:
            try:
                y_s, w_s = week_.split("-W", 1)
                y, w = int(y_s), int(w_s)
                start = _date.fromisocalendar(y, w, 1)
                end = _date.fromisocalendar(y, w, 7)
                qs = qs.filter(date__range=(start, end))
            except Exception:
                pass
        if customer_id:
            qs = qs.filter(customer_id=customer_id)
        if invoiced_ is not None and invoiced_ != "":
            inv = str(invoiced_).lower() in ("true", "1", "yes", "y", "on")
            qs = qs.filter(invoiced=inv)

        return qs

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
                request.user if request.user.is_authenticated else None,
            },
        )
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

    @action(detail=True, methods=["post"], url_path="set_invoiced")
    def set_invoiced(self, request, pk=None):
        trip = self.get_object()
        invoiced = request.data.get("invoiced", None)
        if invoiced is None:
            return Response(
                {"detail": "Missing 'invoiced' field (true/false)."},
                status=http_status.HTTP_400_BAD_REQUEST)
        inv = invoiced
        if isinstance(inv, str):
            inv = inv.lower() in ("true", "1", "yes", "y", "on")
        if inv:
            trip.invoiced = True
            trip.invoiced_at = timezone.now()
            trip.invoiced_by = request.user
        else:
            trip.invoiced = False
            trip.invoiced_at = None
            trip.invoiced_by = None
        trip.save(update_fields=["invoiced", "invoiced_at", "invoiced_by"])
        return Response(self.get_serializer(trip).data)

    @action(detail=False, methods=["post"], url_path="bulk_invoice")
    def bulk_invoice(self, request):
        customer_id = request.data.get("customer")
        month_ = request.data.get("month")
        invoiced = request.data.get("invoiced", True)
        if not customer_id or not month_:
            return Response(
                {"detail": "Both 'customer' and 'month' are required."},
                status=http_status.HTTP_400_BAD_REQUEST)
        inv = invoiced
        if isinstance(inv, str):
            inv = inv.lower() in ("true", "1", "yes", "y", "on")
        qs = self.get_queryset().filter(customer_id=customer_id)
        try:
            y_s, m_s = str(month_).split("-", 1)
            qs = qs.filter(date__year=int(y_s), date__month=int(m_s))
        except Exception:
            return Response(
                {"detail": "Invalid 'month' format. Expected 'YYYY-MM'."},
                status=http_status.HTTP_400_BAD_REQUEST)
        if inv:
            updated = qs.filter(invoiced=False).update(
                invoiced=True,
                invoiced_at=timezone.now(),
                invoiced_by_id=request.user.id)
        else:
            updated = qs.filter(invoiced=True).update(invoiced=False,
                                                      invoiced_at=None,
                                                      invoiced_by_id=None)
        return Response({"status": "ok", "updated": updated})


class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.select_related(
        'trip', 'driver').all().order_by('-assigned_at')
    serializer_class = AssignmentSerializer


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserSerializer(request.user).data)
