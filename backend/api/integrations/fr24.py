# api/integrations/fr24.py
"""
Flightradar24-klient (LIVE + optional summary + airport arrivals)

Konfig i settings.py (eller via env):
    FR24_API_BASE = "https://fr24api.flightradar24.com/api"   # default
    FR24_API_TOKEN = "<din token>"                            # PÅKREVD
    FR24_AUTH_SCHEME = "bearer"  # eller "x-api-key"
    FR24_ACCEPT_VERSION = "v1"   # mange FR24-endepunkt bruker versjon i header
    # (valgfritt om du har summary)
    FR24_FLIGHT_SUMMARY_PATH = "/flight-summary"              # eller "/flight-summary/full"
    FR24_FLIGHT_SUMMARY_VARIANT = "full"                      # "full" | "light"

Hva som er tilgjengelig avhenger av plan. Behandle 403/404 som 'ikke tilgjengelig'.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings

# === Konfig ===
FR24_API_BASE: str = getattr(settings, "FR24_API_BASE",
                             "https://fr24api.flightradar24.com/api")
FR24_API_TOKEN: Optional[str] = getattr(settings, "FR24_API_TOKEN", None)
FR24_AUTH_SCHEME: str = getattr(settings, "FR24_AUTH_SCHEME",
                                "bearer")  # "bearer" | "x-api-key"
FR24_ACCEPT_VERSION: Optional[str] = getattr(settings, "FR24_ACCEPT_VERSION",
                                             "v1")

# Summary (valgfritt – kan gi 404 hvis ikke i din plan)
FR24_FLIGHT_SUMMARY_PATH: str = getattr(settings, "FR24_FLIGHT_SUMMARY_PATH",
                                        "/flight-summary")
FR24_FLIGHT_SUMMARY_VARIANT: str = getattr(settings,
                                           "FR24_FLIGHT_SUMMARY_VARIANT",
                                           "full")  # "full" | "light"

# IATA -> tillatte prefikser for flight (IATA) og callsign (ICAO).
# Vi tillater også at enkelte feeder bruker IATA i callsign-feltet ved å legge iata i "icao"-listen senere.
AIRLINE_ALIASES: Dict[str, List[str]] = {
    # Nordics / vanlige hos deg
    "DY": ["NOZ", "NSZ", "NAX"],  # Norwegian
    "SK": ["SAS", "SZS"],  # SAS (+ "SZS" observert i noen feeder)
    "LH": ["DLH"],  # Lufthansa
    "WF": ["WIF"],  # Widerøe
    "KL": ["KLM"],  # KLM
    "BA": ["BAW"],  # British Airways
    "AF": ["AFR"],  # Air France
    "TK": ["THY"],  # Turkish
    "AY": ["FIN"],  # Finnair
    "LX": ["SWR"],  # Swiss
    "OS": ["AUA"],  # Austrian
    "SN": ["BEL"],  # Brussels
    "LO": ["LOT"],  # LOT
    "IB": ["IBE"],  # Iberia
    "EI": ["EIN"],  # Aer Lingus
    "AZ": ["ITY", "AZA"],  # ITA / Alitalia
    "UA": ["UAL"],  # United
    "AA": ["AAL"],  # American
    "DL": ["DAL"],  # Delta
    "QR": ["QTR"],  # Qatar
    "EK": ["UAE"],  # Emirates
    "SU": ["AFL"],  # Aeroflot
    "RJ": ["RJA"],  # Royal Jordanian
    "HV": ["TRA"],  # Transavia
    "TO": ["TVF"],  # Transavia France
    "FR": ["RYR", "MAY"],  # Ryanair (MAY sett i enkelte feeds)
    "XQ": ["SXS"],  # SunExpress
    "T7": ["T7M"],  # Private (observasjoner)
    # legg til flere etter behov
}

# Regex for callsign (ICAO + nummer/suffiks), f.eks. SAS4612, DLH5UJ, NOZ2889A
_CALLSIGN_RE = re.compile(r"^[A-Z]{3}[0-9A-Z]{1,5}$")


def looks_like_callsign(s: str) -> bool:
    return bool(_CALLSIGN_RE.match((s or "").strip().upper().replace(" ", "")))


def _norm(s: str) -> str:
    return (s or "").strip().upper().replace(" ", "")


def _digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def filter_by_callsign(items: List[dict], query: str) -> List[dict]:
    """Eksakt callsign-match (robust normalisering)."""
    q = _norm(query)
    out = []
    for it in items:
        cs = _norm(it.get("callsign"))
        if cs == q:
            out.append(it)
    return out


def live_positions_multi(bounds_list: List[str],
                         maxage: int = 180,
                         limit: int = 500) -> List[Dict[str, Any]]:
    """
    Hent live-positions for flere bounds og slå sammen unike rader (på fr24_id eller fallback-nøkkel).
    """
    seen = set()
    out: List[Dict[str, Any]] = []
    for b in bounds_list:
        try:
            items = live_positions(bounds=b, maxage=maxage, limit=limit)
        except Exception:
            items = []
        for it in items:
            key = it.get("fr24_id") or (it.get("flight"), it.get("callsign"),
                                        it.get("timestamp"))
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
    return out


# === Intern hjelp ===
def _headers() -> Dict[str, str]:
    if not FR24_API_TOKEN:
        raise RuntimeError("FR24_API_TOKEN is not set")
    h = {
        "Accept": "application/json",
        "User-Agent": "transport-backend/1.0",
    }
    if FR24_ACCEPT_VERSION:
        h["Accept-Version"] = FR24_ACCEPT_VERSION
    if FR24_AUTH_SCHEME.lower() == "x-api-key":
        h["x-api-key"] = FR24_API_TOKEN
    else:
        h["Authorization"] = f"Bearer {FR24_API_TOKEN}"
    return h


def _iso_utc_day_range(d: dt.date) -> (str, str):
    start = dt.datetime.combine(d, dt.time.min).isoformat() + "Z"
    end = dt.datetime.combine(d, dt.time.max).isoformat() + "Z"
    return start, end


def _dig(dct: Any, *paths: str) -> Any:
    """
    Hent første eksisterende verdi gitt dot-paths ('a', 'a.b', ...).
    Returnerer None hvis ingenting finnes.
    """
    for p in paths:
        cur = dct
        ok = True
        for part in p.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _norm_flight_no(s: str) -> str:
    """Normaliser flightnr til [A-Z0-9] (fjerner mellomrom, bindestrek osv.)."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


# === LIVE: Flight positions ===
def live_positions(
    bounds: str,
    variant: str = "full",
    maxage: int = 120,
    limit: int = 500,
    return_raw: bool = False,
) -> List[Dict[str, Any]]:
    """
    Kaller FR24 Live Flight Positions:
      GET {FR24_API_BASE}/live/flight-positions/{variant}?bounds=N,S,W,E&maxage=120&limit=500

    bounds: "N,S,W,E"
    variant: "full" eller "light"
    """
    path = f"/live/flight-positions/{variant}"
    params = {
        "bounds": bounds,
        "maxage": maxage,
        "limit": limit,
        "format": "json",
    }
    r = requests.get(FR24_API_BASE + path,
                     headers=_headers(),
                     params=params,
                     timeout=15)
    r.raise_for_status()
    j = r.json()

    if return_raw:
        # Returner rå JSON fra FR24 for debugging
        return j  # type: ignore[return-value]

    # Finn liste som inneholder flyene – tillat ulike nøkkelnavn
    items: List[Dict[str, Any]] = []
    if isinstance(j, list):
        items = j  # sjelden, men støttes
    elif isinstance(j, dict):
        for key in ("data", "result", "results", "flights", "items", "records",
                    "rows", "features"):
            v = j.get(key)
            if isinstance(v, list):
                items = v  # type: ignore[assignment]
                break
        if not items and isinstance(j.get("aircraft"), list):
            items = j["aircraft"]  # type: ignore[assignment]

    out: List[Dict[str, Any]] = []
    for it in items:
        flight = _dig(it, "flight", "callsign", "ident", "label")
        callsign = _dig(it, "callsign", "label")
        reg = _dig(it, "reg", "registration", "aircraft.registration")
        lat = _dig(it, "lat", "latitude", "position.lat", "trail.0.lat")
        lon = _dig(it, "lon", "longitude", "position.lon", "trail.0.lon")
        alt = _dig(it, "alt", "baro_altitude", "altitude")
        spd = _dig(it, "speed", "ground_speed", "gs", "gspeed")
        track = _dig(it, "track", "heading")
        squawk = _dig(it, "squawk")
        ts = _dig(it, "timestamp")
        eta = _dig(it, "eta")
        orig = _dig(it, "orig_iata", "origin.iata", "airport.origin.code.iata")
        dest = _dig(it, "dest_iata", "destination.iata",
                    "airport.destination.code.iata")
        frid = _dig(it, "fr24_id", "id", "flight_id")
        painted = _dig(it, "painted_as")
        oper = _dig(it, "operating_as")

        out.append({
            "fr24_id": frid,
            "flight": flight,
            "callsign": callsign,
            "reg": reg,
            "orig_iata": orig,
            "dest_iata": dest,
            "lat": lat,
            "lon": lon,
            "alt_ft": alt,
            "speed_kts": spd,
            "track": track,
            "squawk": squawk,
            "timestamp": ts,
            "eta": eta,
            "painted_as": painted,
            "operating_as": oper,
            "dep_time": None,
            "arr_time": None,
        })

    return out


# === Flightnr/callsign-matching (robust) ===
def _split_iata_num(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Del 'DY540' -> ('DY', '540').
    Tillater evt. bokstav-suffiks på slutten (DY540A) – tall-delen blir '540'.
    Returnerer (None, None) hvis ikke IATA2 + digits(+optional letter).
    """
    q = _norm(query)
    m = re.match(r"^([A-Z]{2})([0-9]{1,4}[A-Z]?)$", q)
    if not m:
        return None, None
    iata = m.group(1)
    num = _digits(m.group(2))
    return iata, num


def _match_flight_field(field: str, allowed_airline_codes: List[str],
                        num: str) -> bool:
    """
    Sjekk item.flight (IATA) – typisk 'DY540' / 'DY 540' / 'dy540a'.
    """
    f = _norm(field)
    if not f:
        return False
    fnum = _digits(f)
    if not fnum or fnum != num:
        return False
    m = re.match(r"^([A-Z]+)", f)
    airline = m.group(1) if m else ""
    return airline in allowed_airline_codes


def _match_callsign_field(field: str, allowed_icao_codes: List[str],
                          num: str) -> bool:
    """
    Sjekk item.callsign (ICAO) – typisk 'NOZ540', 'NSZ540A', 'DLH5UJ'.
    Vi krever at tall-delen == num eller starter med num (tåler suffiks).
    """
    cs = _norm(field)
    if not cs or len(cs) < 3:
        return False
    icao = cs[:3]
    if icao not in allowed_icao_codes:
        return False
    digits = _digits(cs[3:])
    if not digits:
        return False
    return digits == num or digits.startswith(num)


def filter_by_flight_number(items: List[Dict[str, Any]],
                            query: str) -> List[Dict[str, Any]]:
    """
    Robust matcher for flightnr:
      - Hvis query ser ut som callsign (SAS4612/NOZ540/DLH5UJ): eksakt callsign-match.
      - Ellers: split IATA+nummer (DY540) og match mot både item.flight (IATA)
        og item.callsign (ICAO) via alias (NOZ/NSZ/NAX).
      - Tåler bokstav-suffiks i callsign (NOZ540A), og mellomrom/dash i flight.
    """
    q = _norm(query)

    # 1) Eksakt callsign, hvis det ser slik ut
    if looks_like_callsign(q):
        out = []
        for it in items:
            if _norm(it.get("callsign")) == q:
                out.append(it)
        return out

    # 2) IATA flight?
    iata, num = _split_iata_num(q)
    if not iata or not num:
        # fallback: ren strengsammenlikning på item.flight
        out = []
        for it in items:
            if _norm(it.get("flight")) == q:
                out.append(it)
        return out

    allowed_iata = [iata]
    allowed_icao = AIRLINE_ALIASES.get(iata, []).copy()
    # noen feeder bruker IATA i callsign-feltet – legg til som fallback
    if iata not in allowed_icao:
        allowed_icao.append(iata)

    out: List[Dict[str, Any]] = []
    for it in items:
        f = it.get("flight")
        cs = it.get("callsign")

        ok = False
        if f and _match_flight_field(f, allowed_iata, num):
            ok = True
        if not ok and cs and _match_callsign_field(cs, allowed_icao, num):
            ok = True

        if ok:
            out.append(it)

    if out:
        return out

    # 3) Som siste utvei – tall-likhet
    ndigs = _digits(q)
    num_match = [
        it for it in items if _digits(it.get("flight")) == ndigs
        or _digits(it.get("callsign")) == ndigs
    ]
    if num_match:
        return num_match

    # 4) Svak fallback: startswith / substring
    starts = [
        it for it in items if _norm(it.get("flight")).startswith(q)
        or _norm(it.get("callsign")).startswith(q)
    ]
    if starts:
        return starts
    subs = [
        it for it in items
        if q in _norm(it.get("flight")) or q in _norm(it.get("callsign"))
    ]
    return subs


# === SUMMARY (valgfritt, krever korrekt plan/endepunkt) ===
def search_flights_by_number(
    number: str,
    day: dt.date,
    limit: int = 20,
    return_raw: bool = False,
) -> List[Dict[str, Any]]:
    """
    Bruk hvis din plan støtter "flight summary".
    NB: Mange kontoer har ikke dette – gir 404. I så fall bruk live_positions().
    """
    start, end = _iso_utc_day_range(day)

    path = FR24_FLIGHT_SUMMARY_PATH  # f.eks. "/flight-summary" eller "/flight-summary/full"
    params = {
        "flight": number.strip().upper(),
        "flight_datetime_from": start,
        "flight_datetime_to": end,
        "size": limit,
        "format": "json",
    }
    # Hvis path ikke allerede inkluderer varianten, send som parameter
    if "full" not in path and "light" not in path and FR24_FLIGHT_SUMMARY_VARIANT:
        params["variant"] = FR24_FLIGHT_SUMMARY_VARIANT

    r = requests.get(FR24_API_BASE + path,
                     headers=_headers(),
                     params=params,
                     timeout=15)
    r.raise_for_status()
    j = r.json()

    if return_raw:
        return j  # type: ignore[return-value]

    # Finn listefeltet uansett nøkkelnavn
    items: List[Dict[str, Any]] = []
    if isinstance(j, list):
        items = j
    elif isinstance(j, dict):
        for key in ("data", "result", "results", "flights", "items", "records",
                    "rows"):
            v = j.get(key)
            if isinstance(v, list):
                items = v  # type: ignore[assignment]
                break

    out: List[Dict[str, Any]] = []
    for it in items:
        out.append({
            "fr24_id":
            _dig(it, "fr24_id", "id", "flight_id"),
            "flight":
            _dig(it, "flight", "flight_iata", "callsign", "ident"),
            "airline":
            _dig(it, "operated_as", "airline", "airline.name"),
            "type":
            _dig(it, "type", "aircraft.type"),
            "reg":
            _dig(it, "reg", "aircraft.registration"),
            "orig_iata":
            _dig(it, "orig_iata", "origin.iata", "airport.origin.code.iata"),
            "dest_iata":
            _dig(it, "dest_iata", "destination.iata",
                 "airport.destination.code.iata"),
            "dep_time":
            _dig(it, "datetime_takeoff", "scheduled_departure",
                 "departure.scheduled", "departure.time"),
            "arr_time":
            _dig(it, "datetime_landed", "scheduled_arrival",
                 "arrival.scheduled", "arrival.time"),
        })
    return out


# === AIRPORT ARRIVALS (board) – avhenger av plan/endepunkt) ===
def airport_arrivals(
    airport_code: str,  # "SVG" eller "ENZV"
    hours_from: int = 0,
    hours_to: int = 6,
    limit: int = 100,
    return_raw: bool = False,
) -> List[Dict[str, Any]]:
    """
    Forsøk å hente 'arrival board' for en flyplass via FR24.
    Prøver flere path-varianter siden ulike planer har ulik URL-struktur.
    return_raw=True -> returner raw JSON + meta for første 200-svar (liste med ett element som inneholder _raw_*).
    return_raw=False -> returner normalisert liste:
        {
          "flight": "...",        # DY540 etc
          "callsign": "...",      # NOZ540...
          "orig_iata": "...",
          "dest_iata": "...",
          "eta": "...",           # ISO-string hvis tilstede
          "schedule": "...",      # ISO-string hvis tilstede
          "status": "..."         # evt. statuskode/tekst
        }
    """
    if not FR24_API_TOKEN:
        raise RuntimeError("FR24_API_TOKEN is not set")

    code = (airport_code or "").strip().upper()
    iata = code if len(code) == 3 else None
    icao = code if len(code) == 4 else None
    if not iata and not icao:
        # brukeren gav kanskje "SVG", det er OK; men hvis helt off – kast klar feil
        raise ValueError("airport_code må være IATA (3) eller ICAO (4).")

    # Kandidat-paths (rekkefølge viktig). Param-funksjonen lager params for hver kandidat.
    def p1() -> Tuple[str, Dict[str, str]]:
        # Variant 1: /airports/board?code=ENZV&type=arrivals
        return (
            "/airports/board",
            {
                "code": icao or iata,
                "type": "arrivals",
                # tidsvinduer varierer mellom kontoer; send flere nøkler for sikkerhets skyld
                "time_from": str(hours_from),
                "time_to": str(hours_to),
                "timeFrom": str(hours_from),
                "timeTo": str(hours_to),
                "limit": str(limit),
                "format": "json",
            })

    def p2() -> Tuple[str, Dict[str, str]]:
        # Variant 2: /airports/{code}/arrivals
        c = icao or iata
        return (f"/airports/{c}/arrivals", {
            "limit": str(limit),
            "format": "json",
            "time_from": str(hours_from),
            "time_to": str(hours_to),
        })

    def p3() -> Tuple[str, Dict[str, str]]:
        # Variant 3: /airport-board?code=ENZV&arrivals=1
        return ("/airport-board", {
            "code": icao or iata,
            "arrivals": "1",
            "time_from": str(hours_from),
            "time_to": str(hours_to),
            "format": "json",
            "limit": str(limit),
        })

    candidates: List[Tuple[str, Dict[str, str]]] = [p1(), p2(), p3()]

    last_exc: Optional[Exception] = None
    for path, params in candidates:
        try:
            r = requests.get(
                FR24_API_BASE + path,
                headers=_headers(),
                params=params,
                timeout=15,
            )
            # Noen planer gir 404/403 for ikke-støttede path
            if r.status_code >= 400:
                last_exc = requests.HTTPError(f"{r.status_code} for {path}",
                                              response=r)
                continue

            j = r.json()
            if return_raw:
                # Gi rådata + litt meta i en liste (enhetlig returtype)
                return [{
                    "_raw_path": path,
                    "_raw_params": params,
                    "_raw_status": r.status_code,
                    "_raw": j,
                }]

            # Normaliser: finn en liste i j
            payload = None
            if isinstance(j, list):
                payload = j
            elif isinstance(j, dict):
                for key in ("arrivals", "data", "result", "results", "flights",
                            "items", "records", "rows"):
                    v = j.get(key)
                    if isinstance(v, list):
                        payload = v
                        break
                if payload is None and isinstance(j.get("aircraft"), list):
                    payload = j["aircraft"]

            if not isinstance(payload, list):
                # vi fikk 200, men ukjent struktur – returner tom liste
                return []

            out: List[Dict[str, Any]] = []
            for it in payload:
                out.append({
                    "flight":
                    _dig(it, "flight", "flight_iata", "callsign", "ident",
                         "label"),
                    "callsign":
                    _dig(it, "callsign", "ident", "label"),
                    "orig_iata":
                    _dig(it, "origin.iata", "orig_iata",
                         "airport.origin.code.iata", "origin.code"),
                    "dest_iata":
                    _dig(it, "destination.iata", "dest_iata",
                         "airport.destination.code.iata", "destination.code"),
                    "eta":
                    _dig(it, "arrival.estimated", "times.estimated.arrival",
                         "eta", "est_arrival_time"),
                    "schedule":
                    _dig(it, "arrival.scheduled", "times.scheduled.arrival",
                         "schedule_time"),
                    "status":
                    _dig(it, "status", "operation.status", "status.code"),
                })
            return out

        except Exception as e:
            last_exc = e
            continue

    # ingenting ga brukbart svar
    if last_exc:
        raise last_exc
    return []
