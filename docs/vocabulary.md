# NovaStream Billing — Vocabulary Doc

The single source of truth shared by the log generator, ingestion pipeline,
investigation engine, and code correlator. Every name and message string below
exists verbatim in the demo-app repo (`novastream-billing`). **If you change
the app, update this doc in the same change.**

## The product story

NovaStream is a streaming platform. This service handles subscription
renewals: charge the card via the StrivePay gateway, flip the plan `ACTIVE`
in `subscriptions-db`, queue a confirmation email via the SMTP relay.

**Planted bug (Scenario C):** `SubscriptionService.renew()` wraps the DB
update *and* the confirmation email in one `try/except Exception: pass`
([subscription_service.py:38-40](app/services/subscription_service.py)). When the
`subscriptions-db` pool is exhausted, the payment has already settled, the
exception is swallowed, no email goes out, and the API still returns 200.

## Identifiers

| Thing | Format | Example |
|---|---|---|
| User id | `USR-<5 digits>` | `USR-10442` |
| Transaction id | `TXN-<12 hex, upper>` | `TXN-50B9886417EA` |
| Payment method id | `PM-<5 digits>` | `PM-88123` |
| Gateway auth code | `AUTH-<8 hex, upper>` | `AUTH-F596D314` |
| Plan codes | — | `BASIC_MONTHLY` $9.99 · `STANDARD_MONTHLY` $15.49 · `PREMIUM_MONTHLY` $22.99 · `PREMIUM_ANNUAL` $50.00 |

## Services / components

| Logger name | Class | File | Key methods |
|---|---|---|---|
| `novastream.api` | (router) | `app/routers/subscriptions.py` | `renew_subscription()` L24, `get_subscription()` |
| `novastream.payments` | `PaymentService` | `app/services/payment_service.py` | `charge()` L18, `new_txn_id()` |
| `novastream.subscriptions` | `SubscriptionService` | `app/services/subscription_service.py` | `renew()` L18 **← bug at L38-40**, `get_status()` |
| `novastream.notifications` | `NotificationService` | `app/services/notification_service.py` | `send_renewal_confirmation()` L12, `send_payment_failed()` |
| `novastream.db` | `ConnectionPool` / `Database` | `app/database.py` | `acquire()` L29, `set_subscription_active()`, `get_subscription()` |

## Endpoints

- `POST /api/v1/subscriptions/renew` — body `{user_id, plan_code, payment_method_id}`, returns `{status, txn_id, message}` (200 even when broken; 402 on card decline)
- `GET /api/v1/subscriptions/{user_id}` — returns `{user_id, plan_code, status, current_period_end}`
- `GET /healthz`

## Exceptions (`app/exceptions.py`)

`NovaStreamError` (base) → `PaymentDeclinedError`, `PaymentGatewayTimeoutError`,
`DatabaseConnectionError`, `QueryExecutionError`, `NotificationDispatchError`.

The broken flow raises `DatabaseConnectionError` with message:
`connection pool 'subscriptions-db' exhausted (waited 5000ms)`

## Log format (app.log)

```
%(asctime)s %(levelname)s [%(name)s] %(message)s
2026-07-09 20:21:40,128 INFO [novastream.payments] Payment gateway responded APPROVED [txn: TXN-50B9886417EA, auth: AUTH-F596D314, amount: $50.00]
```

## Exact log message templates

Placeholders: `{user}`, `{txn}`, `{plan}`, `{amount}`, `{method}`, `{auth}`, `{smtp}`=`smtp-relay.internal.novastream.io`.

**`novastream.api`**
1. `INFO  Renewal request received [user: {user}, plan: {plan}, txn: {txn}]`
2. `INFO  Renewal request completed with status 200 [user: {user}, txn: {txn}]`
3. `WARN  Renewal aborted, payment declined [user: {user}, txn: {txn}]: {reason}`

**`novastream.payments`**
4. `INFO  Initiating charge of ${amount} via gateway [user: {user}, txn: {txn}, method: {method}]`
5. `INFO  Payment gateway responded APPROVED [txn: {txn}, auth: {auth}, amount: ${amount}]`
6. `WARN  Unknown plan code '{plan}' [user: {user}, txn: {txn}]`

**`novastream.subscriptions`**
7. `INFO  Renewing subscription [user: {user}, plan: {plan}, txn: {txn}]`
8. `INFO  Subscription status set to ACTIVE [user: {user}, txn: {txn}]`

**`novastream.db`**
9. `ERROR Failed to acquire connection from pool 'subscriptions-db' after 5000ms: pool exhausted (10/10 connections in use)`

**`novastream.notifications`**
10. `INFO  Renewal confirmation email queued via {smtp} [user: {user}, txn: {txn}, plan: {plan}]`
11. `INFO  Payment failed email queued via {smtp} [user: {user}, txn: {txn}]`

## Flow signatures (what the log generator emits per transaction)

**Normal renewal** — messages 1 → 4 → 5 → 7 → 8 → 10 → 2, all sharing one `{txn}`.

**Broken renewal (Scenario C)** — messages 1 → 4 → 5 → 7 → **9** → 2.
Messages 8 and 10 are **absent**: that absence + the lone `novastream.db`
ERROR is the evidence trail. Note message 2 still reports status 200.

**Card declined (noise, optional)** — messages 1 → 4 → 6 → 3 → 11, endpoint returns 402.

## Answer key fields (per broken txn)

`{txn_id, user_id, failure_point: "SubscriptionService.renew", file: "app/services/subscription_service.py", line: 38, error: "DatabaseConnectionError", root_cause: "swallowed exception after successful payment"}`
