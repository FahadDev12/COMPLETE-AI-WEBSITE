# ShopEasy dashboard, customer accounts and notifications upgrade

## Included

- Sidebar navigation is larger and scrollable. On small screens it becomes a readable, scrollable block above the dashboard content.
- `/admin/workspace` is a dedicated page with large cards for all workspace destinations.
- Dashboard overview now contains a lightweight CSS animated ShopEasy mascot and a direct workspace shortcut.
- Product creation accepts multiple local image files, click-to-select or drag-and-drop. Uploads are limited to JPG/JPEG/PNG/WEBP/GIF, 8 MB per file, with basic file-signature checks and random server filenames. Product pages display a thumbnail gallery; click the main image to zoom.
- Customer registration and login are available at `/account/register` and `/account/login`. Customer login requires password plus a six-digit email one-time code (10-minute expiry, five code attempts). Signed-in customers can see their account order history at `/account`.
- Transactional emails are attempted after checkout and when the administrator changes an order to Confirmed, Processing, Shipped, Out for delivery, Delivered or Cancelled. The Out for delivery message asks the customer to prepare the COD amount.
- Admin dashboard includes Email tracking with recipient, event, subject, timestamp and result (`sent`, `not_configured`, or `failed: ...`).
- Existing SQLite databases are upgraded additively with new tables and columns when the app starts.

## Required email configuration

Copy `.env.example` to `.env` and replace the SMTP placeholders with credentials from a real email provider. Set `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM`. Use the provider's app password or transactional SMTP credential, not a personal account password where the provider advises against it. Restart the Flask app after editing `.env`.

Without valid SMTP configuration, order messages are logged as `not_configured`; customers cannot receive the second-factor code and therefore cannot complete customer sign-in. A dashboard log entry records an attempt, not proof of inbox delivery. Use a provider with delivery/bounce webhooks if you need confirmed delivery/open tracking.

## Important boundaries

- “Out for delivery” is a manual order status selected by the store administrator. No courier API or GPS tracking is integrated, so the system does not automatically know when a parcel is near a customer's location.
- Email open tracking/pixel tracking is not included. The email log records send attempts and SMTP outcomes only.
- Customer 2FA is email OTP, not an authenticator-app TOTP flow. Customer account password reset, verified-email recovery, resend-code UI and admin 2FA are not included yet.
- Customer order history links orders placed while signed in. Guest checkout remains available; past guest orders are not automatically attached to a customer account.
- Product upload files are stored locally in `static/uploads`. Production deployments need persistent storage, backups, malware scanning, image re-encoding and CDN/object storage planning.
- Existing admin login provisioning and the prior security controls remain in place.

## Verification

- Python syntax compilation passed for the application and bundled utility/test modules.
- SQLite schema creation and additive migration checks passed against an in-memory database.
- Jinja template syntax parsing passed for all HTML templates.
- ZIP archive integrity checked after packaging.
- Flask integration/browser tests were not run because Flask is not installed in the build environment and package download was unavailable. Configure SMTP and run the project locally to test full end-to-end flows before public launch.


## ShopEasy visual and catalogue refresh (October 2026)
- The homepage is now a dedicated landing page with a hero, animated CSS mascot, promotional content, categories and a small featured-products row.
- Shop Electronics opens a separate catalogue at `/shop` (also available at `/electronics`) with a left filter sidebar, category links, search, sorting and product cards.
- Category tiles route into the catalogue and keep category selection on the separate shop page.
- The global search and footer Shop all links now open the catalogue.
