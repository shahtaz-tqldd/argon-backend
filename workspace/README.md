# Workspace domain

The current workspace schema contains:

- `Workspace`: an owned workspace with an automatically generated, stable slug.
- `WorkspaceUser`: an active/inactive membership with `admin` or `member` role.
- `WorkspaceInvitation`: a single-use, expiring invitation stored as a token hash.

Direct password and Google signup call
`accounts.services.onboarding.provision_direct_signup`. It idempotently creates
a default workspace and an admin membership for the new user.

An invitation acceptance endpoint must validate its invitation first, then use:

- `create_user_from_workspace_invitation` for a new account, or
- `join_workspace_from_invitation` for an existing account.

Neither invitation path creates another workspace. This separation is
intentional; invitation behavior must not be controlled by an untrusted request
boolean.

Workspace admins can call `add_workspace_user` to add or reactivate members.

Client API routes:

- `GET/PATCH /api/v1/workspaces/` gets or owner-updates the current workspace.
- `GET /api/v1/workspaces/<slug>/` gets workspace information for an active member.
- `PATCH /api/v1/workspaces/<slug>/` updates workspace information as its owner.
- `POST /api/v1/workspaces/invitations/` invites to the owner's current workspace.
- `POST /api/v1/workspaces/<slug>/invitations/` emails a registration invitation.
- `POST /api/v1/workspaces/invitations/accept/` registers from the emailed token.


---
Workspace
│
├── Members
│
└── Chatbot
    │
    ├── Basic identity
    │   ├── name
    │   ├── logo
    │   ├── status
    │   └── created_by
    │
    ├── ChatbotSettings
    │   ├── language
    │   ├── welcome message
    │   └── general behavior
    │
    ├── ChatbotWidgetSettings
    │   ├── color
    │   ├── position
    │   ├── branding
    │   └── launcher
    │
    ├── ChatbotFeatures
    │   ├── knowledge
    │   ├── appointments
    │   ├── catalog
    │   ├── quotation
    │   └── lead collection
    │
    ├── ChatbotLeadSettings
    │
    ├── KnowledgeBases
    │   └── Documents
    │
    ├── Channels
    │   ├── Web
    │   ├── Facebook
    │   ├── Instagram
    │   └── WhatsApp
    │
    └── Integrations
        ├── Google Calendar
        ├── Outlook
        ├── Shopify
        └── HubSpot
