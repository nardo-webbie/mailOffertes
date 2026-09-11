-- Turso (libSQL) schema voor MailOfferte.
-- Wordt automatisch aangemaakt/gemigreerd door check_offertes.py
-- (CREATE TABLE IF NOT EXISTS + kolom-migraties), dit bestand is puur ter
-- documentatie / handmatige inspectie.

-- 'portal_quotations' is EIGENDOM van de Scope Customer Portal (niet van
-- MailOfferte) -- deze CREATE TABLE staat hier alleen zodat het script ook
-- tegen een lege/nieuwe Turso-database werkt. In de praktijk bestaat de
-- tabel al en is dit een no-op. MailOfferte schrijft hier rechtstreeks in
-- (i.p.v. een eigen losse tabel) zodat per mail aangemaakte offertes ook
-- meteen in de portal-zoekfunctie verschijnen. De kolom 'create_method'
-- (Mail/Portal) wordt door check_offertes.py apart gemigreerd (met een
-- DEFAULT 'Portal' voor bestaande rijen) omdat 'ie niet in het
-- oorspronkelijke portal-schema zit -- zie _ensure_create_method_column().
CREATE TABLE IF NOT EXISTS portal_quotations (
    identifier TEXT PRIMARY KEY,           -- Scope quotation identifier (GUID)
    number TEXT,                           -- bv. Q-RTM-000185 (kan kort na aanmaken nog leeg zijn)
    external_identifier TEXT,              -- 'PORTAL-<timestamp>' (portal) of 'MAIL-<gmail_message_id>'
    shipment_type TEXT,                    -- airExport / airImport / seaExport / seaImport / ...
    status TEXT,
    partner_code TEXT NOT NULL,            -- klant-partnercode, voor autorisatie bij zoeken
    partner_name TEXT,
    contact_name TEXT,
    contact_email TEXT,
    departure TEXT,
    destination TEXT,
    salesperson_name TEXT,
    currency TEXT,
    created_by_user_id TEXT,               -- admin_users.user_id van de portal-gebruiker (NULL bij Mail)
    created_by_naam TEXT,
    quotation_json TEXT NOT NULL,          -- volledige Scope-response op moment van aanmaken
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    create_method TEXT NOT NULL DEFAULT 'Portal'  -- 'Mail' of 'Portal' -- door MailOfferte gemigreerd
);

-- Mislukte pogingen (voor handmatige controle) -- deze mails krijgen het
-- Gmail-label "Offerte/Fout" en worden dus niet opnieuw geprobeerd. Puur
-- van MailOfferte, geen relatie met de portal.
CREATE TABLE IF NOT EXISTS quotation_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id TEXT,
    email_subject TEXT,
    email_from TEXT,
    error_stage TEXT,
    error_message TEXT,
    request_json TEXT,
    response_body TEXT,
    created_at TEXT NOT NULL
);

-- Wie is de "orderer"/klant achter een mailadres? Handmatig onderhouden
-- (email_address + scope_partner_code invullen); scope_partner_identifier
-- wordt door het script zelf via de Partner-API opgezocht en hier
-- gecached zodra alleen de code bekend is.
CREATE TABLE IF NOT EXISTS email_partner_map (
    email_address TEXT PRIMARY KEY,
    scope_partner_code TEXT NOT NULL,
    scope_partner_identifier TEXT,
    updated_at TEXT NOT NULL
);

-- LET OP: de eerdere, losse 'quotations'-tabel (vóór deze samenvoeging met
-- portal_quotations) wordt niet meer gebruikt/gemigreerd. Hij bleek
-- overigens nooit een succesvolle rij te hebben bevat (elke poging liep
-- stuk op een van de eerdere schema-bugs) -- dus er valt niets uit te
-- migreren. Kan handmatig opgeruimd worden met: DROP TABLE quotations;
