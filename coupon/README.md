# Coupon API

Coupons are managed by superadmins and redeemed by chatbot administrators
during subscription checkout. A `Discount` holds the monetary definition
(percentage or fixed amount, once/repeating/forever); a `Coupon` is the
redeemable code with eligibility rules; a `CouponRedemption` is the immutable
per-payment record with a discount snapshot and a per-cycle counter.

No schema migrations were required; services and APIs live in this app.

## Admin endpoints (superadmin only)

Mounted at `/api/v1/admin/coupons/`:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/` | List coupons with redemption counts. Filters: `search`, `is_active`, `discount_type`, `validity` (`current`/`scheduled`/`expired`). |
| POST | `/create/` | Create a coupon with nested `discount_data` or an existing `discount_id`. |
| PUT/PATCH | `/<coupon_id>/update/` | Update coupon fields; nested `discount_data` edits the shared discount. |
| DELETE | `/<coupon_id>/delete/` | Delete unless redemptions reference it (409 — deactivate instead). |
| GET | `/<coupon_id>/usage/` | Aggregates: totals, unique users, discount given, per-currency breakdown, remaining redemptions. |
| GET | `/<coupon_id>/redemptions/` | Paginated redemption history. Filters: `is_active`, `currency`. |

Create body:

```json
{
  "code": "save10",
  "name": "Launch offer",
  "discount_data": {
    "name": "Launch 10%",
    "discount_type": "percentage",
    "value": "10.00",
    "duration": "repeating",
    "duration_in_billing_cycles": 3
  },
  "max_redemptions": 100,
  "max_redemptions_per_user": 1,
  "minimum_purchase_amount": "10.00",
  "minimum_purchase_currency": "USD",
  "first_payment_only": false,
  "applies_to_all_plans": false,
  "eligible_plans": ["<plan uuid>"]
}
```

Validation mirrors the models: percentage discounts have no currency, fixed
discounts require one, repeating discounts require a cycle count, expiry must
follow the start time, and non-all-plan coupons need at least one eligible
plan. Codes are case-insensitively unique and normalized to uppercase.

## Client endpoints (chatbot or workspace admin)

Mounted at `/api/v1/subscriptions/coupons/`:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/apply/?chatbot=<slug>` | Validate a code and preview the discounted price. Binds to an open incomplete checkout. Body: `{"code": "SAVE10", "plan_price_id": "<uuid>"}` (`plan_price_id` optional when a subscription is open). |
| GET | `/current/?chatbot=<slug>` | Show the pending checkout coupon and the active redemption. |
| POST | `/remove/?chatbot=<slug>` | Clear the pending binding so the next checkout is undiscounted. |

The current-subscription endpoint (`/api/v1/subscriptions/current/`) also
returns `pending_coupon` and `coupon_redemption` on the subscription.

## Checkout binding

`POST /api/v1/subscriptions/checkout/?chatbot=<slug>` accepts an optional
`coupon_code`:

```json
{
  "plan_price_id": "00000000-0000-0000-0000-000000000000",
  "coupon_code": "SAVE10"
}
```

The flow:

1. The code is validated locally (active window, plan eligibility, currency,
   minimum purchase, total and per-user redemption limits, first-payment-only)
   and stored as `pending_coupon` on the subscription's `provider_metadata`.
2. Checkout creates a Stripe Coupon + Promotion Code (cached on
   `coupon.metadata["stripe_promotion_code"]`) and attaches it to the embedded
   Checkout Session, so Stripe itself charges the discounted amount.
3. An open session is reused only when the plan **and** coupon are unchanged;
   changing or removing the coupon expires the session and builds a new one.
4. `invoice.paid` webhooks convert the pending coupon into a
   `CouponRedemption` bound to the subscription and payment with an immutable
   discount snapshot. Later paid invoices decrement
   `billing_cycles_remaining` for repeating discounts (deactivating the
   redemption when exhausted; `forever` discounts stay active).

Coupons do not apply to free-plan activation or to plan changes of an active
Stripe subscription. If a discount is edited after its Stripe promotion code
was created, checkout rejects it (Stripe promotion codes are immutable);
create a new coupon code instead.

## Tests

```
python manage.py test coupon subscription
```
