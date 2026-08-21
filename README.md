# argon backend

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
        feature: 
        - apppointment booking
            - set which info to take
            - set schedule
            - implment calendar -> later
        - taking lead
            - which info to take
            - implment hubspot -> later
    - upload knowledge
    - set chatbot behavior
    - implement channel: widget design
    - test
