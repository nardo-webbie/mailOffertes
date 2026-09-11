#!/usr/bin/env python3
"""
MailOfferte -- monitort het Gmail-label "Offerte", probeert van elke nieuwe
mail een offerte te maken in Scope (Riege), en slaat gelukte offertes op in
Turso.

Flow per draai (bedoeld om elke ~15-20 min via GitHub Actions te draaien):
  1. Gmail: zoek berichten met label "Offerte" die nog geen "Offerte/Verwerkt"
     of "Offerte/Fout" label hebben.
  2. Voor elk bericht: haal de tekst op, laat Claude (Anthropic API) er een
     Scope-offerte-JSON van maken (air/seafcl/sealcl/road-vorm, wat past).
  3. Vul waar mogelijk de bestaande klant (partner) en de verkoper
     (salesperson) aan via de Scope Partner-/Salesperson-API's.
  4. POST naar Scope's quotation-API.
       - Bij succes (2xx): sla de offerte op in Turso (tabel `quotations`)
         en label de mail "Offerte/Verwerkt".
       - Bij falen: log de fout in Turso (tabel `quotation_errors`) en label
         de mail "Offerte/Fout" (zodat 'ie niet opnieuw wordt geprobeerd;
         voor handmatige controle/herstel).

Vereiste env-variabelen (zie README.md / GitHub Actions secrets):
  SCOPE_SERVER, SCOPE_USER, SCOPE_PASSWORD,
  SCOPE_ORGANIZATION_CODE, SCOPE_LEGAL_ENTITY_CODE,
  TURSO_DATABASE_URL, TURSO_AUTH_TOKEN,
  ANTHROPIC_API_KEY,
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
"""
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from requests.auth import HTTPBasicAuth

# --------------------------------------------------------------------------
# Config / env
# --------------------------------------------------------------------------

SCOPE_SERVER = os.environ["SCOPE_SERVER"]
SCOPE_USER = os.environ["SCOPE_USER"]
SCOPE_PASSWORD = os.environ["SCOPE_PASSWORD"]
SCOPE_ORGANIZATION_CODE = os.environ.get("SCOPE_ORGANIZATION_CODE")
SCOPE_LEGAL_ENTITY_CODE = os.environ.get("SCOPE_LEGAL_ENTITY_CODE")
# Partnercode die als "owner" (eigenaar/vestiging) van elke aangemaakte
# offerte gebruikt wordt -- Scope eist dit veld ("Quotation's owner must be
# supplied"). Standaard SCORTM; override met de env-var/secret
# SCOPE_OWNER_PARTNER_CODE als dat ooit een andere code moet zijn.
SCOPE_OWNER_PARTNER_CODE = os.environ.get("SCOPE_OWNER_PARTNER_CODE", "SCORTM")

TURSO_DATABASE_URL = os.environ["TURSO_DATABASE_URL"]
TURSO_AUTH_TOKEN = os.environ["TURSO_AUTH_TOKEN"]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

GMAIL_CLIENT_ID = os.environ["GMAIL_CLIENT_ID"]
GMAIL_CLIENT_SECRET = os.environ["GMAIL_CLIENT_SECRET"]
GMAIL_REFRESH_TOKEN = os.environ["GMAIL_REFRESH_TOKEN"]

LABEL_SOURCE = "Offerte"
LABEL_DONE = "Offerte/Verwerkt"
LABEL_ERROR = "Offerte/Fout"

SCOPE_BASE = f"https://{SCOPE_SERVER}/scope/rest"
SCOPE_AUTH = HTTPBasicAuth(SCOPE_USER, SCOPE_PASSWORD)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Gmail (REST, met refresh-token -- geen google-api-python-client nodig)
# --------------------------------------------------------------------------

class Gmail:
    def __init__(self):
        self._access_token = None

    def _refresh(self):
        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": GMAIL_CLIENT_ID,
                "client_secret": GMAIL_CLIENT_SECRET,
                "refresh_token": GMAIL_REFRESH_TOKEN,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
        if resp.status_code >= 400:
            # Print Google's actual error (invalid_client / invalid_grant / ...)
            # zodat de Actions-log meteen zegt welk secret fout staat, in
            # plaats van alleen "400 Bad Request".
            print("Gmail token-refresh mislukt:", resp.status_code, resp.text, file=sys.stderr)
        resp.raise_for_status()
        self._access_token = resp.json()["access_token"]

    def _headers(self):
        if not self._access_token:
            self._refresh()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _get(self, path, params=None):
        r = requests.get(
            f"https://www.googleapis.com/gmail/v1/users/me/{path}",
            headers=self._headers(),
            params=params,
            timeout=30,
        )
        if r.status_code == 401:
            self._refresh()
            r = requests.get(
                f"https://www.googleapis.com/gmail/v1/users/me/{path}",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
        r.raise_for_status()
        return r.json()

    def _post(self, path, payload):
        r = requests.post(
            f"https://www.googleapis.com/gmail/v1/users/me/{path}",
            headers=self._headers(),
            json=payload,
            timeout=30,
        )
        if r.status_code == 401:
            self._refresh()
            r = requests.post(
                f"https://www.googleapis.com/gmail/v1/users/me/{path}",
                headers=self._headers(),
                json=payload,
                timeout=30,
            )
        r.raise_for_status()
        return r.json()

    def label_map(self):
        data = self._get("labels")
        return {lbl["name"]: lbl["id"] for lbl in data.get("labels", [])}

    def ensure_label(self, name):
        labels = self.label_map()
        if name in labels:
            return labels[name]
        created = requests.post(
            "https://www.googleapis.com/gmail/v1/users/me/labels",
            headers=self._headers(),
            json={"name": name},
            timeout=30,
        )
        created.raise_for_status()
        return created.json()["id"]

    def list_unprocessed(self, label_source, label_done, label_error):
        q = f'label:"{label_source}" -label:"{label_done}" -label:"{label_error}"'
        data = self._get("messages", params={"q": q, "maxResults": 25})
        return [m["id"] for m in data.get("messages", [])]

    def get_message(self, msg_id):
        return self._get(f"messages/{msg_id}", params={"format": "full"})

    def modify_labels(self, msg_id, add=None, remove=None):
        payload = {}
        if add:
            payload["addLabelIds"] = add
        if remove:
            payload["removeLabelIds"] = remove
        self._post(f"messages/{msg_id}/modify", payload)


def _b64url_decode(data):
    data = data.replace("-", "+").replace("_", "/")
    padding = "=" * (-len(data) % 4)
    return base64.b64decode(data + padding)


def extract_plain_text(payload):
    """Loopt de Gmail message payload door en pakt de eerste text/plain
    (of anders text/html, ruw) body. Genoeg voor het doorgeven aan het
    extractiemodel -- geen mooie HTML-naar-tekst-conversie nodig."""

    def walk(part):
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        if mime == "text/plain" and body.get("data"):
            return _b64url_decode(body["data"]).decode("utf-8", errors="replace")
        for sub in part.get("parts", []) or []:
            found = walk(sub)
            if found:
                return found
        if mime == "text/html" and body.get("data"):
            html = _b64url_decode(body["data"]).decode("utf-8", errors="replace")
            return re.sub("<[^<]+?>", " ", html)
        return None

    text = walk(payload.get("payload", {}))
    return text or ""


def get_header(payload, name):
    for h in payload.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def extract_email_address(header_value):
    """Haalt het kale e-mailadres uit een From-header als 'Naam <adres@x.nl>'
    of gewoon 'adres@x.nl'. Lowercased, want de mapping-tabel matcht exact."""
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", header_value or "")
    return m.group(0).lower() if m else ""


# --------------------------------------------------------------------------
# Scope (Riege) -- partner-/salesperson-lookup + quotation-aanmaak
# --------------------------------------------------------------------------

def scope_get(path, params=None):
    r = requests.get(
        f"{SCOPE_BASE}/{path}",
        params=params,
        auth=SCOPE_AUTH,
        headers={"Accept": "application/json"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


_owner_identifier_cache = {}


def find_partner_by_code(code):
    """Exacte lookup via de Partner-API's 'code'-parameter (i.t.t.
    find_partner_by_text hieronder, dat alleen client-side op naam matcht).
    Gebruikt voor SCOPE_OWNER_PARTNER_CODE ('owner.identifier' -- Scope eist
    dit veld op elke offerte)."""
    if code in _owner_identifier_cache:
        return _owner_identifier_cache[code]
    try:
        data = scope_get(
            "v4/partners",
            params={
                "organizationCode": SCOPE_ORGANIZATION_CODE,
                "legalEntityCode": SCOPE_LEGAL_ENTITY_CODE,
                "code": code,
            },
        )
    except requests.RequestException as e:
        print(f"  partner-lookup (code={code}) faalde:", e)
        return None

    items = data if isinstance(data, list) else data.get("partner") or data.get("partners") or []
    if not items:
        print(f"  partner met code {code!r} niet gevonden via Partner-API")
        return None
    identifier = items[0].get("identifier")
    _owner_identifier_cache[code] = identifier
    return identifier


def resolve_partner_for_email(turso, email_address):
    """Betrouwbare manier om te bepalen wie de 'orderer' is: kijkt in de
    handmatig onderhouden tabel email_partner_map (email_address ->
    scope_partner_code). Als daar een rij voor bestaat maar de identifier
    nog niet gecached is (of de code is gewijzigd), wordt die opgezocht via
    de Partner-API en teruggeschreven, zodat volgende runs geen extra
    API-call meer nodig hebben.

    Retourneert (partner_identifier, partner_code) of (None, None) als er
    geen mapping voor dit adres bestaat."""
    if not email_address:
        return None, None

    # email_address komt hier al lowercased binnen (extract_email_address),
    # maar SQLite vergelijkt TEXT standaard hoofdlettergevoelig -- vergelijk
    # daarom met lower() aan beide kanten, voor het geval de rij met een
    # andere schrijfwijze is ingevoerd.
    rs = turso.execute(
        "SELECT scope_partner_code, scope_partner_identifier FROM email_partner_map "
        "WHERE lower(email_address) = ?",
        [email_address],
    )
    if not rs.rows:
        return None, None

    code, identifier = rs.rows[0][0], rs.rows[0][1]
    if identifier:
        return identifier, code

    identifier = find_partner_by_code(code)
    if identifier:
        turso.execute(
            "UPDATE email_partner_map SET scope_partner_identifier = ?, updated_at = ? "
            "WHERE lower(email_address) = ?",
            [identifier, now_iso(), email_address],
        )
    else:
        print(f"  mapping voor {email_address} verwijst naar partnercode {code!r}, "
              f"maar die is niet gevonden via de Partner-API -- controleer de code.")
    return identifier, code


def find_partner_by_text(search_text):
    """Best-effort: haalt partners op (org/legal-entity-scoped) en matcht
    client-side op naam/e-maildomein, want de Partner-API biedt geen
    vrije-tekst-zoekparameter. Retourneert het partner-'identifier'-veld
    of None als er niets overtuigends gevonden wordt."""
    try:
        data = scope_get(
            "v4/partners",
            params={
                "organizationCode": SCOPE_ORGANIZATION_CODE,
                "legalEntityCode": SCOPE_LEGAL_ENTITY_CODE,
                "size": 2000,
            },
        )
    except requests.RequestException:
        return None

    items = data if isinstance(data, list) else data.get("partner") or data.get("partners") or []
    needle = (search_text or "").lower()
    if not needle:
        return None
    for p in items:
        name = (p.get("name") or "").lower()
        if name and name in needle:
            return p.get("identifier") or p.get("code")
    return None


def find_salesperson_identifier(hint=SCOPE_USER):
    try:
        data = scope_get(
            "v1/salespersons",
            params={"organizationCode": SCOPE_ORGANIZATION_CODE, "size": 1000},
        )
    except requests.RequestException:
        return None
    items = data if isinstance(data, list) else data.get("salesperson") or data.get("salespersons") or []
    hint = (hint or "").lower()
    for sp in items:
        name = f"{sp.get('firstName', '')} {sp.get('lastName', '')} {sp.get('code', '')}".lower()
        if hint and hint in name:
            return sp.get("identifier")
    # Geen match: laat het veld leeg -- Scope vult evt. een default,
    # of de aanmaak faalt met een duidelijke foutmelding die we loggen.
    return None


def create_quotation(payload):
    r = requests.post(
        f"{SCOPE_BASE}/v1/quotations/",
        json=payload,
        auth=SCOPE_AUTH,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    return r


# --------------------------------------------------------------------------
# Extractie met Claude (Anthropic API)
# --------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
Je zet een binnengekomen Nederlandstalige of Engelstalige offerteaanvraag-mail \
om naar een JSON-object voor de Riege Scope quotation-API (POST /v1/quotations/).

Kies de vorm die het beste past bij de vervoerswijze in de mail. Drie voorbeelden \
van de vorm die Scope verwacht (velden mogen weggelaten worden als ze niet van \
toepassing of niet bekend zijn -- verzin NOOIT waarden die niet uit de mail of de \
context blijken):

LUCHTVRACHT voorbeeld:
{
  "externalIdentifier": "9876543210",
  "shipmentType": "airExport",
  "general": {
    "prospect": {"contact": {"name": "Herr Wenzel", "emailAddress": "kwenzel@spl-sport-riege.com"}},
    "consignee": {"address": {"name": "Toy Company Inc.", "street": "3000 East Mariposa Avenue", "city": "New York", "zip": "10007", "state": "NY", "country": "US"}},
    "departure": {"iataCode": "AMS"},
    "destination": {"iataCode": "JFK"},
    "placeOfDelivery": {"unlocode": "USNYC", "name": "New York", "zip": "10007", "state": "NY"},
    "incoTerms": "DDU",
    "movementScope": "door2Port",
    "packageType": "BX",
    "totalPieces": "1",
    "totalGrossWeight": {"unit": "kg", "value": "100"},
    "totalVolume": {"unit": "m3", "value": "1"},
    "totalChargeableWeight": {"unit": "kg", "value": "166.667"},
    "latestShippingDate": "2026-08-31",
    "sentQuoteBefore": "2026-08-26"
  },
  "calculation": {"currency": "EUR", "shipmentScope": {"orderType": ["pickup", "exportHandling", "mainCarriageAir"]}},
  "validFrom": "2026-08-26",
  "validTo": "2026-08-31",
  "internalNotes": {"note": [{"priority": "medium", "visibility": "branch", "content": "Handle with care!"}]}
}

ZEEVRACHT FCL voorbeeld:
{
  "externalIdentifier": "9876543210",
  "shipmentType": "seaImportFCL",
  "general": {
    "prospect": {"partner": {"customerIdentificationNumber": "405778932"}},
    "consignee": {"address": {"name": "SPL Sports GmbH", "street": "Franz-Ferdinand-Strasse 515", "city": "Berlin", "zip": "10178", "country": "DE"}},
    "placeOfReceipt": {"name": "Shanghai"},
    "departure": {"unlocode": "CNSHA"},
    "destination": {"unlocode": "DEHAM"},
    "placeOfDelivery": {"unlocode": "DEBER", "name": "Berlin", "zip": "10178"},
    "incoTerms": "EXW",
    "seaContainers": {"container": [{"containerCount": "1", "containerType": {"isoCode": "20GP"}}]},
    "totalGrossWeight": {"unit": "kg", "value": "10000"},
    "latestShippingDate": "2026-08-20"
  },
  "calculation": {"currency": "USD"},
  "validFrom": "2026-08-13",
  "validTo": "2026-08-20"
}

WEGVERVOER (binnenland/EU) voorbeeld:
{
  "externalIdentifier": "987654322",
  "shipmentType": "roadImport",
  "general": {
    "prospect": {"partner": {"name": "Sport Logic IQ GmbH"}, "contact": {"name": "Sandra Schneider"}},
    "consignee": {"address": {"name": "Spandix Glam GmbH", "city": "Hamburg", "country": "DE"}},
    "departure": {"name": "Frankfurt"},
    "destination": {"unlocode": "DEHAM", "name": "Hamburg"},
    "incoTerms": "CFR",
    "totalGrossWeight": {"unit": "kg", "value": "120"},
    "totalVolume": {"unit": "m3", "value": "0.285"}
  },
  "calculation": {"currency": "EUR"},
  "validFrom": "2026-08-13",
  "validTo": "2026-08-20"
}

Regels:
- Antwoord ALLEEN met geldige JSON (geen uitleg, geen markdown-codeblok).
- Vandaag is {today}. Gebruik dit voor validFrom (vandaag) en validTo
  (standaard: vandaag + 5 dagen) als de mail geen expliciete geldigheidsdatum noemt.
  Gebruik voor latestShippingDate een redelijke datum uit de mail, anders validTo.
- Vul "general.prospect.contact.name" en "emailAddress" met de afzender van de mail
  als er geen duidelijke aparte contactpersoon genoemd wordt.
- Zet er bovendien een top-level veld "_customer_name_guess" bij: je beste gok van
  de bedrijfsnaam van de klant/afzender (voor onze eigen administratie, niet voor Scope).
  Zet ook "_shipment_summary" (max 15 woorden, NL) met een korte samenvatting.
- Als je te weinig informatie hebt om er een zinnige offerte-aanvraag van te maken
  (bijv. de mail is geen offerteaanvraag), antwoord dan met exact:
  {"_unusable": true, "_reason": "<korte reden in het NL>"}
"""


def extract_quotation_json(email_text, email_from, email_subject):
    from anthropic import Anthropic

    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    user_msg = (
        f"Van: {email_from}\nOnderwerp: {email_subject}\n\nInhoud:\n{email_text[:8000]}"
    )
    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2000,
        system=EXTRACTION_SYSTEM_PROMPT.replace("{today}", today),
        messages=[{"role": "user", "content": user_msg}],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    return json.loads(raw)


# --------------------------------------------------------------------------
# Turso
# --------------------------------------------------------------------------

def get_turso_client():
    import libsql_client

    return libsql_client.create_client_sync(url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)


def ensure_schema(client):
    here = os.path.dirname(os.path.abspath(__file__))
    sql = open(os.path.join(here, "schema.sql")).read()
    # Verwijder eerst alle losse commentaarregels (-- ...), pas dan op ';'
    # splitsen -- anders wordt een hele CREATE TABLE overgeslagen zodra er
    # een commentaarregel vóór staat (de vorige aanpak checkte alleen of de
    # HELE statement met "--" begon, in plaats van commentaar weg te snijden).
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    cleaned = "\n".join(lines)
    for stmt in [s.strip() for s in cleaned.split(";") if s.strip()]:
        client.execute(stmt)


def save_success(client, msg, quotation_id, external_id, request_payload, response_json):
    client.execute(
        "INSERT INTO quotations (gmail_message_id, gmail_thread_id, email_subject, email_from, "
        "email_date, scope_quotation_identifier, scope_external_identifier, customer_name, "
        "departure, destination, shipment_type, request_json, response_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(gmail_message_id) DO NOTHING",
        [
            msg["id"],
            msg.get("threadId"),
            get_header(msg, "Subject"),
            get_header(msg, "From"),
            get_header(msg, "Date"),
            quotation_id,
            external_id,
            request_payload.get("_customer_name_guess"),
            json.dumps(request_payload.get("general", {}).get("departure", {}), ensure_ascii=False),
            json.dumps(request_payload.get("general", {}).get("destination", {}), ensure_ascii=False),
            request_payload.get("shipmentType"),
            json.dumps(request_payload, ensure_ascii=False),
            json.dumps(response_json, ensure_ascii=False),
            now_iso(),
        ],
    )


def save_error(client, msg, stage, error_message, request_payload=None, response_body=None):
    client.execute(
        "INSERT INTO quotation_errors (gmail_message_id, email_subject, email_from, error_stage, "
        "error_message, request_json, response_body, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            msg["id"] if msg else None,
            get_header(msg, "Subject") if msg else None,
            get_header(msg, "From") if msg else None,
            stage,
            str(error_message)[:2000],
            json.dumps(request_payload, ensure_ascii=False) if request_payload else None,
            str(response_body)[:4000] if response_body else None,
            now_iso(),
        ],
    )


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    gmail = Gmail()
    label_ids = {
        LABEL_SOURCE: gmail.ensure_label(LABEL_SOURCE),
        LABEL_DONE: gmail.ensure_label(LABEL_DONE),
        LABEL_ERROR: gmail.ensure_label(LABEL_ERROR),
    }

    ids = gmail.list_unprocessed(LABEL_SOURCE, LABEL_DONE, LABEL_ERROR)
    print(f"{len(ids)} nog te verwerken mail(s) in '{LABEL_SOURCE}'.")
    if not ids:
        return

    turso = get_turso_client()
    ensure_schema(turso)

    for msg_id in ids:
        msg = gmail.get_message(msg_id)
        subject = get_header(msg, "Subject")
        sender = get_header(msg, "From")
        print(f"--- Verwerk: {subject!r} van {sender}")

        try:
            body_text = extract_plain_text(msg)
            quotation_json = extract_quotation_json(body_text, sender, subject)
        except Exception as e:  # noqa: BLE001
            print("  extractie mislukt:", e)
            save_error(turso, msg, "extractie", e)
            gmail.modify_labels(msg_id, add=[label_ids[LABEL_ERROR]])
            continue

        if quotation_json.get("_unusable"):
            reason = quotation_json.get("_reason", "onbekend")
            print("  geen bruikbare offerteaanvraag:", reason)
            save_error(turso, msg, "onbruikbaar", reason)
            gmail.modify_labels(msg_id, add=[label_ids[LABEL_ERROR]])
            continue

        customer_guess = quotation_json.pop("_customer_name_guess", None)
        summary = quotation_json.pop("_shipment_summary", None)
        quotation_json["_customer_name_guess"] = customer_guess  # blijft in ons record, niet naar Scope
        payload_for_scope = {k: v for k, v in quotation_json.items() if not k.startswith("_")}

        # Owner is verplicht ("Quotation's owner must be supplied") -- Scope
        # koppelt dit aan de partnercode SCOPE_OWNER_PARTNER_CODE (SCORTM).
        try:
            owner_id = find_partner_by_code(SCOPE_OWNER_PARTNER_CODE)
            if owner_id:
                payload_for_scope["owner"] = {"identifier": owner_id}
            else:
                print(f"  WAARSCHUWING: geen identifier gevonden voor owner-partnercode {SCOPE_OWNER_PARTNER_CODE!r}; Scope zal deze offerte waarschijnlijk afwijzen.")
        except Exception as e:  # noqa: BLE001
            print("  owner-lookup faalde (niet fataal, maar Scope zal waarschijnlijk afwijzen):", e)

        # Best-effort aanvullen van bekende klant/verkoper.
        try:
            sp_id = find_salesperson_identifier()
            if sp_id:
                payload_for_scope.setdefault("salesperson", {})["identifier"] = sp_id
        except Exception as e:  # noqa: BLE001
            print("  salesperson-lookup faalde (niet fataal):", e)

        # Wie is de "orderer"? Eerst de betrouwbare weg: mailadres opzoeken
        # in email_partner_map. Alleen als daar niets voor staat, terugvallen
        # op het onzekere naam-matchen.
        try:
            prospect = payload_for_scope.get("general", {}).get("prospect", {})
            sender_email = extract_email_address(sender)
            partner_id, partner_code = resolve_partner_for_email(turso, sender_email)
            if partner_id:
                prospect.setdefault("partner", {})["identifier"] = partner_id
                print(f"  orderer herkend via email_partner_map: {sender_email} -> {partner_code} ({partner_id})")
            else:
                if sender_email:
                    print(f"  geen mapping voor {sender_email} in email_partner_map -- "
                          f"voeg een rij toe (email_address, scope_partner_code) zodat dit "
                          f"automatisch herkend wordt. Val voorlopig terug op naam-matchen.")
                search_text = f"{sender} {customer_guess or ''}"
                fallback_id = find_partner_by_text(search_text)
                if fallback_id and "partner" not in prospect:
                    prospect.setdefault("partner", {})["identifier"] = fallback_id
        except Exception as e:  # noqa: BLE001
            print("  partner-lookup faalde (niet fataal):", e)

        try:
            resp = create_quotation(payload_for_scope)
        except requests.RequestException as e:
            print("  Scope-aanroep faalde (netwerk):", e)
            save_error(turso, msg, "scope_netwerk", e, payload_for_scope)
            gmail.modify_labels(msg_id, add=[label_ids[LABEL_ERROR]])
            continue

        if resp.status_code >= 200 and resp.status_code < 300:
            try:
                resp_json = resp.json()
            except ValueError:
                resp_json = {"raw": resp.text}
            quotation_id = (
                resp_json.get("identifier")
                or resp.headers.get("Location", "").rstrip("/").rsplit("/", 1)[-1]
                or None
            )
            print(f"  offerte aangemaakt in Scope: {quotation_id}")
            save_success(
                turso, msg, quotation_id,
                payload_for_scope.get("externalIdentifier"),
                {**payload_for_scope, "_customer_name_guess": customer_guess, "_shipment_summary": summary},
                resp_json,
            )
            gmail.modify_labels(msg_id, add=[label_ids[LABEL_DONE]])
        else:
            print(f"  Scope wees de offerte af: HTTP {resp.status_code} -- {resp.text[:500]}")
            save_error(
                turso, msg, f"scope_http_{resp.status_code}", resp.text[:2000], payload_for_scope, resp.text,
            )
            gmail.modify_labels(msg_id, add=[label_ids[LABEL_ERROR]])

    turso.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        print("FATAL:", exc, file=sys.stderr)
        raise
