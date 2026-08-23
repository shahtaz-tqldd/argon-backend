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

- `POST /api/v1/workspaces/create/` creates a workspace and its owner membership.
- `GET /api/v1/workspaces/` gets the authenticated owner's or member's workspace.
- `PUT/PATCH /api/v1/workspaces/update/?workspace=<slug>` owner-updates a workspace.
- `DELETE /api/v1/workspaces/delete/?workspace=<slug>` soft-deletes a workspace.
- `GET /api/v1/workspaces/team/list/?workspace=<slug>` lists active and invited members.
- `GET /api/v1/workspaces/team/details/?workspace=<slug>&member_email=<email>` gets a member.
- `POST /api/v1/workspaces/team/invite/?workspace=<slug>` emails an invitation.
- `GET/PATCH /api/v1/workspaces/team/role/?workspace=<slug>&member_email=<email>` manages a member role.
- `DELETE /api/v1/workspaces/team/remove-member/?workspace=<slug>&member_email=<email>` removes a member.
- `POST /api/v1/workspaces/team/accept-invite/` registers from an emailed token.


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
