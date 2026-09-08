# argon backend

## Logging

Import the shared logger anywhere in the application:

```python
from app.utils.logger import logger

logger.info("Training started for chatbot %s", chatbot.id)
logger.warning("Retrying upload %s", upload.id)
logger.error("Upload failed for file %s", filename)
```

For module names in the output, use `get_logger(__name__)`:

```python
from app.utils.logger import get_logger

logger = get_logger(__name__)

try:
    process_upload()
except Exception:
    logger.exception("Upload processing failed")  # Includes the traceback.
```

Logs go to the console (stderr) with the level, timestamp, logger name, source
file and line number. Django configures logging at startup for the web app,
management commands, and Celery tasks. Set `LOG_LEVEL=DEBUG`, `INFO` (default),
`WARNING`, `ERROR`, or `CRITICAL` in `.env` and restart the process to change
verbosity. `logger.debug()` and `logger.critical()` are also available.
Avoid logging passwords, tokens, or sensitive user data.

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


---
What I have used
- R2 from cloudflare to Upload Image and Files // third party services
- Gemini API // LLM
- Gemini Embedding // Embedding 
- Google Firebase // Google Authentication
- Stripe // payment gateway -> has webhook

- postgres + pgvector - db + vector database // self hosted -> HSNW index, 1536 dimension
- Playwright for web scrapping // open source
- google ADK // open source framework