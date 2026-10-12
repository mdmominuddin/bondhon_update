# Bondhon Management and Auditing System

Upgrade of the Bondhon society app: management + auditing + member portal + SMS + online deposits, with a Tailwind CSS interface.

## Run it

```bash
python -m venv venv && source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate          # BACK UP db.sqlite3 FIRST - this upgrades your existing data in place
python manage.py runserver
```
Sign in with an existing account (superusers become **Admin**; other existing users become **Treasurer** - change under *Users & Roles*).
The compiled CSS (`static/css/app.css`) and JS (`static/vendor/`) are included - **Node is only needed if you edit the design**:
`npm install && npm run build:css` (`npm run watch:css` while designing).

Production: copy `.env.example` to `.env`, set `DJANGO_DEBUG=0` and a real `DJANGO_SECRET_KEY`, run `python manage.py collectstatic`, serve `/static/` and `/media/`.

## Roles
| Role | Can do |
|---|---|
| **Admin** | Everything: users & roles, settings, approve/reject expenses, void entries, re-open closed audits |
| **Treasurer** | Record deposits/expenses (expenses wait for admin approval), verify member payments, send SMS, manage members |
| **Auditor** | Read-only on all records + audit module (analytics, findings, reconciliation, sign-off, audit trail) |
| **Member** | Portal: own statement and receipts, deposit money, notifications, society totals (expense list switchable in Settings) |

## What was added
- **Auditing** (`/audit/`): tamper-evident audit trail (every create/edit/void/approve/login, SHA-256 hash-chained, verify button); 16 automated tests (duplicates, reused TrxIDs, back-dated entries, edits after posting, receipt gaps, undocumented/large expenses, stored-summary mismatch, stale approvals, bad phone numbers, audit-log integrity...); audit engagements with findings, management responses, physical cash/bank reconciliation, opinion, printable report, and **period lock** after sign-off.
- **Controls**: numbered receipts (`BND-2026-00001`); entries are *voided with a reason*, never deleted; expense maker-checker approval; optional bill/voucher attachment; projects with budget vs spend.
- **Client deposit** (`/payments/deposit-money/`): members pay by bKash / Nagad / Rocket / bank and submit the TrxID; the treasurer verifies (TrxID can't be reused); or by gateway (SSLCommerz / sandbox). A confirmed payment becomes a normal deposit.
- **Messaging** (`/messages/`): one place for templates (English + Bangla, placeholders, live segment counter), custom messages, broadcasts (all / dues / selected), outbox with retry. **The receipt SMS fires from the single place every deposit is created**, so it is sent exactly once whether the office typed it or the member paid online (DB constraint guarantees no duplicates).
- **UI**: new Tailwind layout, role-aware sidebar, dashboards with charts, mobile-friendly, print styles for receipts/reports, Bangladeshi digit grouping (3,25,000).

## Scheduled jobs (optional)
```
python manage.py send_queued_sms --retry-failed     # every ~10 min
python manage.py send_due_reminders                 # e.g. monthly
```

## Things to do before going live
1. **Fix member phone numbers** - every member in the supplied data has `123`. Members list -> CSV -> edit -> *Import / update*.
2. Settings: society details, payment accounts (bKash/Nagad/bank), default fund head, SMS provider, then switch SMS backend from *Console* to *HTTP provider*.
3. The legacy monthly-summary table disagrees with the ledger (see Audit overview) - investigate before relying on it.

## Tests
`python manage.py test` (54 tests: access rules per role, receipts, SMS exactly-once, audit chain tamper detection, period lock, payment flows).
