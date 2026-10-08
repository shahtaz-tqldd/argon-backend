# Pricing & Plans Configuration

This document outlines the system configurations and feature entitlements for our subscription tiers, mapping the database configuration directly to user-facing features and limits.

## Overview

| Feature | Free | Starter | Growth | Premium (Pro) | Enterprise |
| --- | --- | --- | --- | --- | --- |
| **Monthly Price** | $0 | $59 | $119 | $229 | Custom |
| **Annual Price** | $0 | $588 | $1,188 | $2,388 | Custom |
| **AI Messages** | 100 | 1,000 | 2,500 | 5,000 | Unlimited |
| **Message Overage** | N/A | $0.030 / msg | $0.025 / msg | $0.020 / msg | N/A |
| **Knowledge Chunks** | 25 | 625 | 1,250 | 2,500 | Unlimited |
| **Max File Size** | 10 MB | 25 MB | 50 MB | 100 MB | Unlimited |
| **Team Members** | 1 | 5 | 10 | 15 | Unlimited |
| **Billing Provider** | Manual | Stripe | Stripe | Stripe | Custom |

---

## Plan Details

### 1. Free

* **Target:** Trial users and individuals testing the platform.
* **Features:**: Knowledge Base, Human Handoff
* **Limits:**
* 100 AI messages per month (No overage allowed)
* 25 knowledge chunks
* 10 MB max file size
* 1 team member



### 2. Starter

* **Target:** Small teams needing basic lead collection.
* **Features:** Knowledge Base, Human Handoff, Lead Capture

* **Limits:**
* 1,000 AI messages per month
* 625 knowledge chunks (approx. 500k tokens)
* 25 MB max file size (Total storage: 30 MB)
* 5 team members



### 3. Growth

* **Target:** Growing businesses needing appointment scheduling and CRM sync.
* **Features:** Knowledge Base, Human Handoff, Lead Capture, Lead Insights, Appointment Booking

* **Limits:**
* 2,500 AI messages per month
* 1,250 knowledge chunks (approx. 750k tokens)
* 50 MB max file size (Total storage: 50 MB)
* 10 team members



### 4. Premium (Pro)

* **Target:** Established businesses requiring advanced analytics.
* **Features:** Knowledge Base, Human Handoff, Lead Capture, Lead Insights, Appointment Booking, AI Recommendations


* **Limits:**
* 5,000 AI messages per month
* 2,500 knowledge chunks (approx. 1 Million tokens)
* 100 MB max file size (Total storage: 100 MB)
* 15 team members



### 5. Enterprise

* **Target:** Large organizations with custom needs. Requires sales contact (`requires_sales_contact: True`).
* **Features:** Full feature access.
* **Limits:**
* Custom AI messages
* Custom knowledge chunks
* Custom file sizes and storage
* Custom team members



---

## Technical Notes

* **Overage Billing:** Overage billing is enabled automatically for the Starter, Growth, and Premium tiers. When users exceed their `ai_message_limit`, they are charged the `ai_message_overage_unit_price` per additional message. Overages are disabled for Free and Enterprise plans.
* **Sort Order:** Plans are ordered in the UI based on the `sort_order` integer (Free: 0, Starter: 10, Growth: 20, Premium: 30, Enterprise: 40).
* **Payment Gateways:** Paid standard tiers are handled via `PaymentProvider.STRIPE`. The Free tier uses `PaymentProvider.MANUAL`.
