"""Constants mirroring docs/vocabulary.md — the shared source of truth.

Every string here exists verbatim in the demo-app repo (novastream-billing).
If the app changes, update docs/vocabulary.md and this module together.
"""

SMTP_RELAY = "smtp-relay.internal.novastream.io"

GATEWAY_NAME = "StrivePay"

DB_POOL = "subscriptions-db"

PLANS = {
    "BASIC_MONTHLY": 9.99,
    "STANDARD_MONTHLY": 15.49,
    "PREMIUM_MONTHLY": 22.99,
    "PREMIUM_ANNUAL": 50.00,
}

# Logger names
API = "novastream.api"
PAYMENTS = "novastream.payments"
SUBSCRIPTIONS = "novastream.subscriptions"
DB = "novastream.db"
NOTIFICATIONS = "novastream.notifications"

# Exact message templates, numbered as in docs/vocabulary.md.
# Placeholders: {user} {txn} {plan} {amount} {method} {auth} {smtp} {reason}
MESSAGES = {
    1: (API, "INFO", "Renewal request received [user: {user}, plan: {plan}, txn: {txn}]"),
    2: (API, "INFO", "Renewal request completed with status 200 [user: {user}, txn: {txn}]"),
    3: (API, "WARN", "Renewal aborted, payment declined [user: {user}, txn: {txn}]: {reason}"),
    4: (PAYMENTS, "INFO", "Initiating charge of ${amount} via gateway [user: {user}, txn: {txn}, method: {method}]"),
    5: (PAYMENTS, "INFO", "Payment gateway responded APPROVED [txn: {txn}, auth: {auth}, amount: ${amount}]"),
    6: (PAYMENTS, "WARN", "Unknown plan code '{plan}' [user: {user}, txn: {txn}]"),
    7: (SUBSCRIPTIONS, "INFO", "Renewing subscription [user: {user}, plan: {plan}, txn: {txn}]"),
    8: (SUBSCRIPTIONS, "INFO", "Subscription status set to ACTIVE [user: {user}, txn: {txn}]"),
    9: (DB, "ERROR", "Failed to acquire connection from pool 'subscriptions-db' after 5000ms: pool exhausted (10/10 connections in use)"),
    10: (NOTIFICATIONS, "INFO", "Renewal confirmation email queued via {smtp} [user: {user}, txn: {txn}, plan: {plan}]"),
    11: (NOTIFICATIONS, "INFO", "Payment failed email queued via {smtp} [user: {user}, txn: {txn}]"),
}

# Flow signatures (message numbers per transaction kind)
FLOW_NORMAL = (1, 4, 5, 7, 8, 10, 2)
FLOW_BROKEN = (1, 4, 5, 7, 9, 2)
FLOW_DECLINED = (1, 4, 6, 3, 11)

# Per-broken-txn answer key entry gets these static fields plus txn_id/user_id
ANSWER_KEY_STATIC = {
    "failure_point": "SubscriptionService.renew",
    "file": "app/services/subscription_service.py",
    "line": 38,
    "error": "DatabaseConnectionError",
    "root_cause": "swallowed exception after successful payment",
}
