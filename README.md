# MailOfferte

Monitort het Gmail-label **"Offerte"** (nardo@webplekkie.nl). Voor elke nieuwe
mail daarin probeert het een offerte aan te maken in Scope (Riege
quotation-API) en, als dat lukt, slaat het de offerte op in `portal_quotations`
-- dezelfde Turso-tabel die de **Scope Customer Portal** zelf gebruikt voor
zijn zoekfunctie. Een kolom `create_method` (`Mail` / `Portal`) onderscheidt
waar een offerte vandaan komt; verder is een per-mail-aangemaakte offerte
gewoon een rij zoals elke andere, en dus direct zichtbaar in de portal.

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
   - **Gelukt:** offerte wordt opgeslagen in `portal_quotations` (met
     `create_method = 'Mail'`), mail krijgt label `Offerte/Verwerkt`.
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

Bestaande mappings controleren:

```bash
turso db shell scope-orders "SELECT * FROM email_partner_map"
```

(de vergelijking met het mailadres uit de mail is hoofdletterongevoelig,
dus schrijfwijze maakt niet uit -- wél moet het adres exact overeenkomen,
zonder spaties ervoor/erna).

## Eén tabel met de portal: `portal_quotations`

`portal_quotations` is EIGENDOM van de Scope Customer Portal, niet van
MailOfferte -- dit script schrijft er alleen in. De kolommen worden zo
gevuld:

| kolom | vulling vanuit MailOfferte |
|---|---|
| `identifier` | Scope's quotation-GUID (primary key -- zonder deze wordt niks opgeslagen) |
| `number`, `status` | rechtstreeks uit de Scope-response, indien aanwezig |
| `external_identifier` | `MAIL-<gmail_message_id>` (portal gebruikt zelf `PORTAL-<timestamp>`) |
| `partner_code`, `partner_name` | via `email_partner_map` (zie hieronder), anders via naam-matchen |
| `contact_name`, `contact_email` | uit de door Claude geëxtraheerde offerteaanvraag |
| `departure`, `destination` | leesbaar label (unlocode/iataCode + naam) |
| `salesperson_name` | via de Salesperson-API (best effort) |
| `currency` | uit de offerteaanvraag |
| `quotation_json` | volledige Scope-response |
| `created_by_user_id`, `created_by_naam` | blijven leeg (geen portal-gebruiker bij een mail-offerte) |
| `create_method` | altijd `'Mail'` |

`partner_code` is in de portal verplicht (voor autorisatie bij zoeken) --
zonder mapping in `email_partner_map` wordt de rij toch opgeslagen (anders
verdwijnt de offerte in het niets), maar met een lege `partner_code` en een
duidelijke waarschuwing in de log, want dan kan de portal 'm niet aan een
klant tonen.

Dedupliceren gaat via `ON CONFLICT(identifier) DO NOTHING` -- Scope geeft
sowieso een nieuwe GUID per aanmaak, dus dit is vooral een vangnet.

## Schema-migratie

`CREATE TABLE IF NOT EXISTS` doet niets als een tabel al bestaat -- ook niet
als 'ie een ouder schema heeft (ontbrekende kolommen, of juist extra
verplichte kolommen die dit script niet kent). Het script checkt dit
daarom apart bij elke run (`PRAGMA table_info`):

- **Ontbrekende kolommen** die het script nodig heeft, worden automatisch
  toegevoegd via `ALTER TABLE ... ADD COLUMN`.
- **`create_method`** wordt apart gemigreerd mét een `DEFAULT 'Portal'`,
  zodat bestaande (door de portal zelf aangemaakte) rijen met
  terugwerkende kracht `'Portal'` krijgen, en de kolom nooit een NOT
  NULL-fout geeft voor rijen die de portal zelf (zonder deze kolom te
  kennen) blijft invoegen.
- **Onverwachte NOT NULL-kolommen** die al in de tabel bestaan maar dit
  script niet kent, worden bij het wegschrijven zelf automatisch gevuld met
  een neutrale waarde (`{}` voor kolommen die op `_json` eindigen, anders
  een lege string) zodat de INSERT nooit crasht op een kolom die het
  script niet kent. Je ziet dit terug als een waarschuwing in de
  Actions-log.

Dit gebeurt stil in de log tenzij er daadwerkelijk iets ontbreekt of
opgevuld moest worden.

## Foutafhandeling bij het opslaan

Zodra een offerte succesvol in Scope is aangemaakt, proberen we 'm ook in
`portal_quotations` op te slaan. Lukt dát onverhoopt niet (bijv. een
tijdelijke Turso-storing), dan wordt dat alleen gelogd in de Actions-run --
de mail krijgt **toch** het label `Offerte/Verwerkt`. Dat is bewust: de
offerte staat al in Scope en is niet meer terug te draaien, dus opnieuw
verwerken zou een **dubbele offerte in Scope** opleveren. Zie je zo'n
waarschuwing in de log, controleer dan handmatig of de offerte in Turso
staat en vul de rij zo nodig zelf aan (het genoemde identifier staat in de
log).

**Bekend, opgelost via backfill:** de drie offertes die zijn aangemaakt
tijdens het uitwerken van deze koppeling
(`a6d33cfe-93b9-4a7f-9472-9d1973774bc9`, `c75f8584-cbd4-4f7d-9d0e-5bede1cc7bd1`,
`fd5ae391-25ab-4e1b-a67e-8b66b4d26177`) stonden wél in Scope maar waren nooit
in Turso beland. `backfill_orphaned_quotations.py` haalt ze alsnog op via
`GET /v1/quotations/{id}` en zet ze in `portal_quotations` (met
`create_method = 'Mail'`). Draai 'm éénmalig via **Actions → Backfill
verweesde offertes → Run workflow** -- daarna kun je dit script en die
workflow weer verwijderen, het is geen onderdeel van de doorlopende
automatisering. (De partnercode is hardcoded op `LIFE`, want de
Scope-quotation-response zelf bevat geen partnercode -- alle drie kwamen
tijdens het testen van dezelfde afzender met die code. Override zo nodig
met de repo-secret/env-var `BACKFILL_PARTNER_CODE`.)

De eerdere, losse `quotations`-tabel (van vóór deze samenvoeging) wordt niet
meer gebruikt en bevatte sowieso nooit een succesvolle rij. Kan handmatig
opgeruimd worden met:

```bash
turso db shell scope-orders "DROP TABLE quotations"
```

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
- `backfill_orphaned_quotations.py` -- eenmalig script, zie hierboven; kan
  weg na een succesvolle run.
- `schema.sql` -- Turso-tabellen (worden ook automatisch aangemaakt/
  gemigreerd door het script zelf).
- `.github/workflows/check-offertes.yml` -- de cronjob.
- `.github/workflows/backfill-orphaned-quotations.yml` -- de eenmalige,
  met de hand te starten backfill-job.
- `requirements.txt` -- Python-dependencies.
