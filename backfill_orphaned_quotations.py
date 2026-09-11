#!/usr/bin/env python3
"""
Eenmalig script: haalt drie "verweesde" offertes op -- wél aangemaakt in
Scope, maar nooit opgeslagen in Turso omdat portal_quotations toen nog niet
het juiste schema had (zie README.md, "Foutafhandeling bij het opslaan") --
via GET /v1/quotations/{id} en zet ze alsnog in portal_quotations, met
create_method = 'Mail'.

Draai dit EENMALIG, via de losse GitHub Actions-workflow
".github/workflows/backfill-orphaned-quotations.yml" (Actions -> Backfill
verweesde offertes -> Run workflow), of lokaal met dezelfde
env-variabelen als check_offertes.py (zie README.md).

Na een geslaagde run kun je dit bestand en de bijbehorende workflow met een
gerust hart weer verwijderen -- het is geen onderdeel van de doorlopende
automatisering.
"""
import json
import os

import requests

from check_offertes import (
    SCOPE_AUTH,
    SCOPE_BASE,
    _insert_row,
    _place_label,
    ensure_schema,
    get_turso_client,
    now_iso,
)

# De drie offertes die zijn aangemaakt tijdens het uitwerken van de
# portal_quotations-koppeling (2026-09-11) en nooit in Turso zijn beland.
ORPHANED_IDENTIFIERS = [
    "a6d33cfe-93b9-4a7f-9472-9d1973774bc9",
    "c75f8584-cbd4-4f7d-9d0e-5bede1cc7bd1",
    "fd5ae391-25ab-4e1b-a67e-8b66b4d26177",
]

# Alle drie zijn tijdens het testen aangemaakt vanuit dezelfde afzender
# (nardo.bezemer@riege.com), waarvoor email_partner_map de partnercode
# LIFE geeft -- de Scope-quotation-response zelf bevat geen partnercode
# (alleen een partner-identifier), dus die code kunnen we niet uit de
# response zelf terugvinden. Override met de env-var BACKFILL_PARTNER_CODE
# als dit een keer niet klopt.
KNOWN_PARTNER_CODE = os.environ.get("BACKFILL_PARTNER_CODE", "LIFE")


def fetch_quotation(identifier):
    r = requests.get(
        f"{SCOPE_BASE}/v1/quotations/{identifier}",
        auth=SCOPE_AUTH,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def main():
    turso = get_turso_client()
    ensure_schema(turso)

    for identifier in ORPHANED_IDENTIFIERS:
        print(f"--- Ophalen {identifier}")
        try:
            data = fetch_quotation(identifier)
        except requests.RequestException as e:
            print(f"  kon offerte niet ophalen bij Scope: {e}")
            continue

        general = data.get("general", {}) or {}
        contact = general.get("prospect", {}).get("contact", {}) or {}
        prospect_partner = general.get("prospect", {}).get("partner", {}) or {}

        try:
            _insert_row(
                turso,
                "portal_quotations",
                {
                    "identifier": data.get("identifier", identifier),
                    "number": data.get("number"),
                    "external_identifier": data.get("externalIdentifier") or f"MAIL-BACKFILL-{identifier[:8]}",
                    "shipment_type": data.get("shipmentType"),
                    "status": data.get("status"),
                    "partner_code": KNOWN_PARTNER_CODE,
                    "partner_name": prospect_partner.get("name"),
                    "contact_name": contact.get("name"),
                    "contact_email": contact.get("emailAddress"),
                    "departure": _place_label(general.get("departure")),
                    "destination": _place_label(general.get("destination")),
                    "salesperson_name": None,
                    "currency": (data.get("calculation") or {}).get("currency"),
                    "quotation_json": json.dumps(data, ensure_ascii=False),
                    "created_at": now_iso(),
                    "create_method": "Mail",
                },
                on_conflict="ON CONFLICT(identifier) DO NOTHING",
            )
            print("  opgeslagen in portal_quotations (of stond er al -- ON CONFLICT DO NOTHING).")
        except Exception as e:  # noqa: BLE001
            print(f"  opslaan in portal_quotations mislukte: {e}")

    turso.close()


if __name__ == "__main__":
    main()
