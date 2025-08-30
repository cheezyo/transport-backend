# apps/transport/integrations/avinor.py
from __future__ import annotations
import datetime as dt
from typing import Any, Dict, List, Optional
import re
import requests
from django.utils import timezone

AVINOR_BASE = "https://api.avinor.no/FlightTimetable"


def _norm_flight(s: str) -> str:
    # "DY 540" -> "DY540", "dy-540" -> "DY540"
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _today_oslo_str() -> str:
    # Bruk lokal (settings.TIME_ZONE) — i ditt oppsett: Europe/Oslo
    d = timezone.localdate()
    return d.isoformat()  # YYYY-MM-DD


def fetch_arrivals_svg(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Henter alle ankomster til SVG for gitt dato (default: i dag, lokal tid).
    Avinor-endepunkt:
    GET https://api.avinor.no/FlightTimetable?airport=SVG&direction=Arrival&date=YYYY-MM-DD
    """
    date_s = (date_str or _today_oslo_str())
    params = {"airport": "SVG", "direction": "Arrival", "date": date_s}
    r = requests.get(AVINOR_BASE, params=params, timeout=15)
    r.raise_for_status()
    j = r.json()
    # Avinor svarer normalt med en liste av flights
    return j if isinstance(
        j, list) else j.get("flights", []) or j.get("result", []) or []


def find_eta_svg_by_flight(
        number: str,
        date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Returner en 'best match' flight for gitt flightnummer (IATA), inkludert forventet/planlagt ankomst.
    """
    flights = fetch_arrivals_svg(date_str)
    needle = _norm_flight(number)

    # 1) eksakt treff på flightId
    exact = [
        f for f in flights if _norm_flight(str(f.get("flightId"))) == needle
    ]

    # 2) se i ev. codeshares dersom Avinor eksponerer det (varierer litt)
    if not exact:
        cand = []
        for f in flights:
            # noen payloads har "codeShares" som liste over iata-strings
            shares = f.get("codeShares") or f.get("codeshares") or []
            try:
                for s in shares:
                    if _norm_flight(str(s)) == needle:
                        cand.append(f)
                        break
            except Exception:
                pass
        exact = cand

    # 3) hvis flere – velg den nærmest nå (på estimert/planlagt ankomst)
    if not exact:
        return None

    def _parse(s: Optional[str]) -> Optional[dt.datetime]:
        if not s:
            return None
        try:
            # Avinor gir ofte lokal tid uten Z — tolk som naive lokal og konverter til aware
            # men vi trenger bare sortering; naive funker også
            return dt.datetime.fromisoformat(s)
        except Exception:
            return None

    now = timezone.localtime()

    def _eta_key(f: Dict[str, Any]):
        eta = _parse(f.get("estimatedTime")) or _parse(f.get("scheduleTime"))
        if eta is None:
            return dt.datetime.max
        # anta at dette er lokal Oslo-tid; sammenlign som naive:
        return eta

    exact.sort(key=_eta_key)
    best = exact[0]

    # pakk ut nyttige felter (felt-navn kan variere – vi er defensive)
    return {
        "flight":
        best.get("flightId"),
        "airline":
        best.get("airline") or best.get("carrier"),
        "origin_iata":
        best.get("airportFrom") or best.get("origin") or best.get("fromIata"),
        "dest_iata":
        "SVG",
        "scheduled":
        best.get("scheduleTime"),
        "estimated":
        best.get("estimatedTime") or best.get("statusTime"),
        "status":
        best.get("status") or best.get("remarks"),
        "gate":
        best.get("gate"),
        "baggage":
        best.get("baggage"),
        "raw":
        best,  # nyttig ved feil; kan fjernes om du vil
    }
