# backend/api/management/commands/import_holidays.py
import sys
import json
import calendar
import datetime as dt
from typing import Iterable

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError

import requests  # pip install requests om du ikke har det

from api.models import Holiday


def iter_sundays(year: int) -> Iterable[dt.date]:
    """Yield alle søndager i gitt år."""
    d = dt.date(year, 1, 1)
    # finn første søndag
    days_until_sunday = (6 - d.weekday()) % 7  # Monday=0 ... Sunday=6
    d = d + dt.timedelta(days=days_until_sunday)
    while d.year == year:
        yield d
        d = d + dt.timedelta(days=7)


class Command(BaseCommand):
    help = "Importer røde dager for et år fra Nager.Date og marker alle søndager som røde."

    def add_arguments(self, parser):
        parser.add_argument("--year",
                            type=int,
                            required=True,
                            help="Kalenderår, f.eks. 2025")
        parser.add_argument("--country",
                            type=str,
                            default="NO",
                            help="Landskode (default NO)")
        parser.add_argument(
            "--include-sundays",
            action="store_true",
            default=True,
            help="Marker alle søndager som røde dager (default: True)")
        parser.add_argument("--skip-api",
                            action="store_true",
                            help="Hopp over API-kall (bruk kun søndager).")

    def handle(self, *args, **opts):
        year = opts["year"]
        country = (opts["country"] or "NO").upper()
        include_sundays = opts["include_sundays"]
        skip_api = opts.get("skip_api", False)

        created = 0
        updated = 0
        skipped = 0

        # 1) Hent offisielle helligdager fra Nager.Date
        holidays_from_api = []
        if not skip_api:
            url = f"https://date.nager.at/api/v3/PublicHolidays/{year}/{country}"
            self.stdout.write(
                self.style.NOTICE(f"Henter helligdager fra {url} ..."))
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                holidays_from_api = resp.json()
                # forventer liste av { date: 'YYYY-MM-DD', localName: '...', name: '...' ... }
            except requests.RequestException as e:
                self.stderr.write(
                    self.style.WARNING(
                        f"Kunne ikke hente fra Nager.Date: {e}. Fortsetter uten API-data."
                    ))
                holidays_from_api = []

        # 2) Lagre/oppdater røde dager fra API
        with transaction.atomic():
            for h in holidays_from_api:
                try:
                    date_str = h.get("date")  # 'YYYY-MM-DD'
                    name = h.get("localName") or h.get("name") or "Helligdag"
                    d = dt.date.fromisoformat(date_str)
                except Exception:
                    continue

                # Merk: Hvis din Holiday-modell har unique=True på date,
                # så er det én oppføring per dato (uansett land)
                obj, was_created = Holiday.objects.update_or_create(
                    date=d,
                    defaults={
                        "name": name,
                        "country_code": country,
                    },
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        # 3) Marker alle søndager som røde dager
        if include_sundays:
            with transaction.atomic():
                for d in iter_sundays(year):
                    try:
                        obj, was_created = Holiday.objects.get_or_create(
                            date=d,
                            defaults={
                                "name": "Søndag",
                                "country_code": country,
                            },
                        )
                        if was_created:
                            created += 1
                        else:
                            # Hvis den finnes fra før (f.eks. allerede en helligdag på søndag),
                            # lar vi den stå (ikke overskriv navnet).
                            skipped += 1
                    except IntegrityError:
                        skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Ferdig: created={created}, updated={updated}, skipped={skipped} for {year} ({country})"
            ))
