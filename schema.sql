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
