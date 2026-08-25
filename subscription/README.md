# Subscription and Stripe API

Subscription entitlements belong to a chatbot. Plan discovery is public; every
chatbot-scoped billing action requires authentication, and checkout, payment
history, portal, free-plan activation, and cancellation require a chatbot or
workspace administrator.

## Client endpoints

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/v1/subscriptions/plans/` | List active public plans and purchasable prices. |
| GET | `/api/v1/subscriptions/plans/details/?plan=<slug>` | Fetch one active public plan. |
| POST | `/api/v1/subscriptions/checkout/?chatbot=<slug>` | Create or reuse a hosted Stripe Checkout Session. |
| POST | `/api/v1/subscriptions/activate-free/?chatbot=<slug>` | Activate a zero-price free plan without Stripe. |
| GET | `/api/v1/subscriptions/current/?chatbot=<slug>` | Fetch the chatbot's open subscription. |
| GET | `/api/v1/subscriptions/payments/?chatbot=<slug>` | List recorded Stripe invoices/payments. |
| POST | `/api/v1/subscriptions/billing-portal/?chatbot=<slug>` | Create a Stripe customer portal Session. |
| POST | `/api/v1/subscriptions/cancellation/?chatbot=<slug>` | Schedule or remove end-of-period cancellation. |
| POST | `/api/v1/subscriptions/stripe/webhook/` | Receive signed Stripe events. |

Checkout body:

```json
{
  "plan_price_id": "00000000-0000-0000-0000-000000000000"
}
```

Cancellation body (use `false` to resume a scheduled cancellation):

```json
{
  "cancel_at_period_end": true
}
```

## Stripe configuration

Set these environment variables:

```dotenv
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_BILLING_PORTAL_RETURN_URL=http://localhost:5173/settings/billing
```

Every Stripe `PlanPrice` must contain an active recurring Stripe Price ID in
`provider_price_id`. If AI-message overage billing is enabled, put its metered
Stripe Price ID in `provider_overage_price_id`.

Configure the webhook destination for:

- `checkout.session.completed`
- `checkout.session.async_payment_succeeded`
- `checkout.session.expired`
- `customer.subscription.created`
- `customer.subscription.updated`
- `customer.subscription.deleted`
- `invoice.paid`
- `invoice.payment_failed`
- `invoice.payment_action_required`
- `charge.refunded`

The webhook verifies the raw request signature, stores every Stripe event for
idempotency, and updates the immutable local subscription snapshot. The client
must treat the checkout redirect as navigation only and use the current
subscription endpoint to observe activation.
