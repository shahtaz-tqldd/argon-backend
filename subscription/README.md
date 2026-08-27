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
| POST | `/api/v1/subscriptions/checkout/?chatbot=<slug>` | Create or reuse an embedded Stripe Checkout Session. |
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

For a new subscription, checkout creates a Session with Stripe's
`embedded_page` UI mode and returns a `client_secret` for mounting Embedded
Checkout. Free activation uses the same body shape with the zero-price local
`plan_price_id`.

The checkout response also contains `action` and `requires_checkout`:

```json
{
  "subscription_id": "00000000-0000-0000-0000-000000000000",
  "subscription_status": "incomplete",
  "client_secret": "cs_test_..._secret_...",
  "requires_checkout": true,
  "reused": false,
  "action": "checkout"
}
```

Mount Embedded Checkout only when `requires_checkout` is `true`. A response
with `action: subscription_activated` means the API reconciled a completed,
paid Session whose webhook had not updated the local row yet. A response with
`action: already_active` needs no further billing action.

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

`STRIPE_WEBHOOK_SECRET` must be the endpoint signing secret shown for this
webhook destination in Stripe Workbench, or the `whsec_...` secret printed by
`stripe listen` during local development. Do not use an `sk_...` secret API key
or a `pk_...` publishable key in this setting.

Stripe plans, products, and Price IDs do not need to be configured in advance.
Checkout creates inline recurring price data from the local `PlanPrice` amount,
currency, billing interval, and plan name. The client sends only the local
`plan_price_id`; it never sends or controls the amount charged.

An open Checkout Session is reused to prevent duplicate payment attempts. If
that Session has expired, the checkout endpoint creates a replacement Session
and returns its new `client_secret`. A completed and paid Session is reconciled
directly from Stripe and returns HTTP 200 instead of a conflict. A completed
Session with an asynchronous payment still processing returns HTTP 409; poll
the current-subscription endpoint and do not create a duplicate payment. After
Stripe sends `checkout.session.async_payment_failed`, the same checkout API can
cancel the failed Stripe subscription and create a fresh Session.

Posting a different paid `plan_price_id` while a Stripe subscription is active
changes that existing Stripe subscription instead of creating another one.
The backend sends inline price data, immediately invoices prorations, and
returns `action: plan_changed`, `requires_checkout: false` on success. If
Stripe cannot charge the saved payment method, the API returns HTTP 402; send
the user to the billing-portal endpoint to update the payment method, then
retry the plan change.

Knowledge entitlements are also local. `file_size_limit_mb` and
`knowledge_chunk_limit` are copied into the chatbot subscription snapshot when
the plan is selected. Knowledge uploads and training enforce those snapshot
limits and require the plan's `knowledge_base` feature.

Configure the webhook destination for:

- `checkout.session.completed`
- `checkout.session.async_payment_failed`
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
idempotency, and updates the immutable local subscription snapshot. Embedded
Checkout's completion callback only closes the checkout UI; the client uses the
current subscription endpoint to observe activation.
