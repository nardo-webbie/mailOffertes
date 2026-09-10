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
3. Probeert de bekende klant (partner) en verkoper (salesperson) aan te vullen
   via Scope's Partner-/Salesperson-API's -- best effort, geen harde eis.
4. `POST`'t naar `/v1/quotations/` op je Scope-omgeving (Basic Auth).
   - **Gelukt:** offerte + ruwe request/response worden opgeslagen in Turso,
     mail krijgt label `Offerte/Verwerkt`.
   - **Mislukt:** fout wordt gelogd in Turso (tabel `quotation_errors`), mail
     krijgt label `Offerte/Fout` (zodat 'ie niet steeds opnieuw geprobeerd
     wordt -- voor handmatige controle).

## Bekende beperking

De voorbeeldschema's van Scope's quotation-API tonen geen "owner"-identifier
(vestiging/branch) -- als Scope die verplicht stelt, zal de aanmaak in eerste
instantie falen met een duidelijke foutmelding (terug te vinden in
`quotation_errors.error_message` in Turso, of in de Actions-run-log). Zodra
je de juiste owner-identifier hebt (te vinden in de Scope-UI, of vraag het
Riege-support), kan die als extra stap aan `check_offertes.py` toegevoegd
worden (`payload_for_scope["owner"] = {"identifier": "..."}`).

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
