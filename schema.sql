-- Turso (libSQL) schema voor MailOfferte.
-- Wordt automatisch aangemaakt door check_offertes.py (CREATE TABLE IF NOT EXISTS),
-- dit bestand is puur ter documentatie / handmatige inspectie.

CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gmail_message_id TEXT UNIQUE NOT NULL,
    gmail_thread_id TEXT,
    email_subject TEXT,
    email_from TEXT,
    email_date TEXT,
    scope_quotation_identifier TEXT,
    scope_external_identifier TEXT,
    customer_name TEXT,
    departure TEXT,
    destination TEXT,
    shipment_type TEXT,
    request_json TEXT,
    response_json TEXT,
    created_at TEXT NOT NULL
);

-- Mislukte pogingen (voor handmatige controle) -- deze mails krijgen het
-- Gmail-label "Offerte/Fout" en worden dus niet opnieuw geprobeerd.
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
