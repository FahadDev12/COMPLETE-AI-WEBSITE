# ShopEasy deployment notes

## Architecture
Flask server-rendered app, SQLite, session cart and Cash on Delivery. A separate REST API is not present or required for the current form-driven UI.

## Local setup
Install dependencies with `pip install -r requirements.txt`. Configure environment variables from `.env.example` (set a strong SECRET_KEY, ADMIN_EMAIL and ADMIN_PASSWORD of at least 14 characters), then run `python app.py`. Do not commit secrets.

## Production baseline
1. Set APP_ENV=production and all secrets in the host secret manager.
2. Deploy behind HTTPS. Secure session cookies are enabled in production.
3. Use a production WSGI server, e.g. `waitress-serve --listen=127.0.0.1:8000 app:app`, behind a reverse proxy. Do not expose Flask development server.
4. Keep persistent database storage. SQLite is for a small single-instance shop; use managed PostgreSQL for serious traffic and add a reviewed migration process.
5. Configure TLS, firewall, health checks, monitoring, logs, backup schedules and restore drills. Run `python backup_db.py` and copy encrypted backups off-host.
6. Add legal pages, privacy/data retention, tax, delivery and returns policies before launch.

## External services not configured
- Payment gateway: none. Cash on Delivery only; no card/JazzCash/Easypaisa/webhook/refund support.
- Email/SMS: none. Contact requests are stored in the admin dashboard; order emails are not sent.
- Courier: none. Shipping charges and tracking are manual.
- Managed database, monitoring and off-site backup destination: none.

Do not accept live online payments or represent this as production-ready until those services and operational controls are configured and independently tested.
