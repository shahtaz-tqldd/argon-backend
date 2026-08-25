# argon backend

## Cloudflare R2 storage

The application uses the `argon-chatbot` R2 bucket with this object layout:

```text
files/{chatbot_id}/{upload_id}/{filename}
images/users/{image}
images/workspaces/{image}
images/chatbots/{image}
images/config/{image}
```

Image URLs are returned as `R2_PUBLIC_URL/{object_key}`, for example
`https://assets.example.com/images/users/user-id.webp`. Set the remaining
`R2_*` variables documented in `.env.example`. Images use unique object names
and configurable `R2_IMAGE_CACHE_CONTROL` metadata, so the `images/*` path is
ready for long-lived Cloudflare caching.

The public domain must only serve `images/*`. Block `files/*` at Cloudflare (or
route the domain through a Worker that only permits `images/*`) and disable the
bucket's `r2.dev` public URL. Knowledge files under `files/*` are delivered using
short-lived presigned S3 API URLs.

## Accounts
- User
- User Profile


## IMPORTANT Safe Guard Rule for Knowledge Base

- Starter: 39usd
Total storage: 30 MB (3 MB max file size)
Chunks Limit: 2000
1000 AI message

- Growth: 59 usd
Total Storage: 50 MB (5MB max file size)
chunks limit: 4000
2000 AI message 

- Pro: 99 usd
Total Storage: 100 MB (10MB max file size)
chunks limit: 7500
5000 AI message


# FLow
- create new chatbot with (bot name, description, plan)
- Starting Free

- Choosing a Plan
    - Create bot
    - Create stripe payment intent, customer intent 
    - Payment complete stripe end
    - update with payment info
    - based on plan update chatbot's feature settings

- Go to chatbot config
    - general info settings (fallback, welcome message, language, timezone)
        - set chatbot behavior
        feature: 
        - apppointment booking
            - set which info to take
            - set schedule
            - implment calendar -> later
        - taking lead
            - which info to take
            - implment hubspot -> later
    - upload knowledge
    - widget design
    - implement channel
    - test
---
- Chatbot Configuration
- Knowlegde Fields
- Subscription
- Leads
- Appointment
- Chat Session
