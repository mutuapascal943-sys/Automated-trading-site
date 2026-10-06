# Safaricom Daraja Setup

No credentials belong in source code. Configure these values in the deployment environment or a locally ignored `.env` file:

```dotenv
DARAJA_CONSUMER_KEY=<Safaricom Daraja Consumer Key>
DARAJA_CONSUMER_SECRET=<Safaricom Daraja Consumer Secret>
DARAJA_SHORTCODE=<Sandbox or production Paybill/Till shortcode>
DARAJA_PASSKEY=<STK Push passkey for the shortcode>
DARAJA_CALLBACK_URL=https://<public-host>/api/payments/daraja/callback/
DARAJA_ENVIRONMENT=sandbox
DARAJA_BASIC_AMOUNT_KES=<server-approved whole-KES BASIC subscription price>
```

The application reads these settings through `python-decouple` in `forex_ai_pro/settings.py`. The current BASIC product price is configured server-side in Kenyan shillings; checkout ignores any client-provided amount. Do not set a conversion rate or price until the business-approved KES price is known.

## API Flow

The service uses Safaricom Daraja OAuth client credentials:

- Sandbox base: `https://sandbox.safaricom.co.ke`
- Production base: `https://api.safaricom.co.ke`
- OAuth: `/oauth/v1/generate?grant_type=client_credentials`
- STK Push: `/mpesa/stkpush/v1/processrequest`
- STK Query verification: `/mpesa/stkpushquery/v1/query`

Select sandbox with `DARAJA_ENVIRONMENT=sandbox`; set `production` only after production credentials, shortcode/passkey, and public HTTPS callback have been verified. The app caches OAuth tokens shortly before expiry.

`POST /api/subscription/checkout/` accepts a product tier (`BASIC`) and Kenyan phone number. It does not accept an amount or payment result. An accepted STK Push creates a pending `PaymentTransaction` only and does not activate the subscription. The customer must approve the prompt on the handset.

Safaricom calls the configured callback URL. The callback must correlate to the stored merchant/checkout request IDs and a Daraja STK Query must confirm success. Callback metadata must also match the server-stored amount and phone and include a receipt before the existing subscription is activated. Failed/cancelled requests do not grant access. Duplicate callbacks are idempotent.

## Sandbox Testing

1. Obtain sandbox Consumer Key/Secret, shortcode, and passkey from the Safaricom Daraja developer portal for the sandbox app.
2. Configure all variables above and use a publicly reachable HTTPS callback URL registered/allowed by the sandbox setup. Never use a localhost callback URL unless you intentionally expose it through a secure development tunnel.
3. Restart the Django service after setting environment variables.
4. Submit a BASIC checkout with a Kenyan sandbox test phone. The API response means only that an STK request was accepted; it is not proof of payment.
5. Complete/cancel the handset prompt and verify that the callback reaches the backend and STK Query confirms its status.
6. Check `PaymentTransaction` and `Subscription`: only a Daraja-confirmed successful payment activates/extends BASIC.

No sandbox or production transaction has been performed by implementing this integration. Do not configure production mode until Safaricom has issued production credentials and the callback URL and product amount have been formally confirmed.
