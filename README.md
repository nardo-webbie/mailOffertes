# MailOfferte

Monitort het Gmail-label **"Offerte"** (nardo@webplekkie.nl). Voor elke nieuwe
mail daarin probeert het een offerte aan te maken in Scope (Riege
quotation-API) en, als dat lukt, slaat het de offerte op in de Turso-database
die ook door het `riege-logistics-dashboard`-project gebruikt wordt (tabel
`quotations`, naast de bestaande `orders`-tabellen).

Draait als GitHub Actions-cronjob (elke ~20 minuten), omdat de mailbox en de
Scope/Turso-API's niet bereikbaar zijn vanuit de Claude-cloudomgeving.

## Hoe het werkt

1. Zoekt Gmail-berichten met label `Offerte` zonder `Offerte/Verwerkt` of
   `Offerte/Fout`.
2. Laat Claude (Anthropic API) de offerteaanvraag omzetten naar het JSON-formaat
   dat Scope's quotation-API verwacht (lucht/zee/weg, wat van toepassing is).
3. Bepaalt de "orderer" (klant/prospect) via de tabel `email_partner_map`
   (zie hieronder) en vult de verkoper (salesperson) aan via Scope's
   Salesperson-API -- best effort, geen harde eis.
4. `POST`'t naar `/v1/quotations/` op je Scope-omgeving (Basic Auth).
   - **Gelukt:** offerte + ruwe request/response worden opgeslagen in Turso,
     mail krijgt label `Offerte/Verwerkt`.
   - **Mislukt:** fout wordt gelogd in Turso (tabel `quotation_errors`), mail
     krijgt label `Offerte/Fout` (zodat 'ie niet steeds opnieuw geprobeerd
     wordt -- voor handmatige controle).

## Owner (verplicht veld)

Scope eist een `owner.identifier` op elke offerte ("Quotation's owner must be
supplied"). Het script zoekt hiervoor automatisch de partner met code
`SCORTM` op via de Partner-API en gebruikt diens `identifier`. Moet dit ooit
een andere partnercode worden, zet dan de optionele repo-secret
`SCOPE_OWNER_PARTNER_CODE` op de gewenste code.

## Wie is de orderer? (`email_partner_map`)

Scope moet weten welke bestaande klant (partner) een offerte aanvraagt. Dat
lossen we op met een tabel in Turso die je zelf onderhoudt:

| kolom | wie vult 'm |
|---|---|
| `email_address` | jij (het mailadres van de aanvrager, lowercase) |
| `scope_partner_code` | jij (de partnercode zoals in Scope) |
| `scope_partner_identifier` | het script zelf (via de Partner-API, gecached) |

Een rij toevoegen kan via de Turso CLI:

```bash
turso db shell scope-orders "INSERT INTO email_partner_map (email_address, scope_partner_code, updated_at) VALUES ('klant@bedrijf.nl', 'BEDRIJFCODE', datetime('now'))"
```

(of vervang `'BEDRIJFCODE'` door de echte partnercode, en herhaal per
klant-mailadres). Zonder mapping wordt teruggevallen op een onzekere
naam-match, en zie je in de Actions-log een melding welk mailadres nog
toegevoegd moet worden.

## Setup

### 1. Repo-secrets instellen

Ga naar **Settings → Secrets and variables → Actions → New repository
secret** en voeg deze namen toe. Ik geef bewust geen waarden in dit bestand
mee (dit belandt in een repo en dan liever geen secrets in platte tekst) --
de meeste heb je al eerder van mij of uit je eigen bronnen gekregen:

| Secret | Waar haal je 'm vandaan |
|---|---|
| `SCOPE_SERVER` | `demo2.riege.com` |
| `SCOPE_USER` | `bezemer` |
| `SCOPE_PASSWORD` | uit de `.env` van je `riege-logistics-dashboard`-project (Google Drive) |
| `SCOPE_ORGANIZATION_CODE` | `SCO` |
| `SCOPE_LEGAL_ENTITY_CODE` | `NL` |
| `TURSO_DATABASE_URL` | uit dezelfde `.env` |
| `TURSO_AUTH_TOKEN` | uit dezelfde `.env` |
| `ANTHROPIC_API_KEY` | uit dezelfde `.env` |
| `GMAIL_CLIENT_ID` | de Client ID die je in Google Cloud Console hebt aangemaakt |
| `GMAIL_CLIENT_SECRET` | de Client Secret bij diezelfde OAuth-client |
| `GMAIL_REFRESH_TOKEN` | de refresh_token die je via curl hebt opgehaald tijdens het opzetten |

### 2. Actions inschakelen

Nieuwe repo's hebben Actions soms standaard uit -- check **Settings →
Actions → General** dat "Allow all actions" aan staat.

### 3. Testen

**Actions → Check offerte-mails → Run workflow** om 'm handmatig te starten
(niet wachten op de cron). Bekijk de log; zet voor een echte test een
proef-offerteaanvraag-mail in het "Offerte"-label in Gmail.

## Bestanden

- `check_offertes.py` -- het hoofdscript (Gmail, extractie, Scope, Turso).
- `schema.sql` -- Turso-tabellen (worden ook automatisch aangemaakt door het
  script zelf).
- `.github/workflows/check-offertes.yml` -- de cronjob.
- `requirements.txt` -- Python-dependencies.
