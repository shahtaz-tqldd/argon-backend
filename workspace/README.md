# Workspace domain

The current workspace schema contains:

- `Workspace`: an owned workspace with an automatically generated, stable slug.
- `WorkspaceUser`: an active/inactive membership with `admin` or `member` role.
- `WorkspaceInvitation`: a single-use, expiring invitation stored as a token hash.

Direct password and Google signup call
`accounts.services.onboarding.provision_direct_signup`. It creates a default
workspace only when the new account has no active workspace/chatbot membership
and no valid pending workspace/chatbot invitation.

Invitations never create or update an account. A recipient signs in (or uses the
normal registration flow first), then accepts the invitation as that authenticated
user. Acceptance verifies that the account email matches the invitation and creates
only the membership. Users without an automatically created workspace can create
one later through the normal workspace creation endpoint.

Workspace admins can call `add_workspace_user` to add or reactivate members.

Client API routes:

- `POST /api/v1/workspaces/create/` creates the user's single owned workspace and
  its owner membership. Users may belong to any number of shared workspaces but
  can own only one workspace.
- `GET /api/v1/workspaces/list/` returns a paginated list of every active
  workspace the authenticated user owns or belongs to, with their owned
  workspace first. Users with only chatbot access receive an empty list.
- `GET /api/v1/workspaces/?workspace=<slug>` gets a selected accessible workspace
  (the query parameter is optional for backward compatibility).
- `PUT/PATCH /api/v1/workspaces/update/?workspace=<slug>` owner-updates a workspace.
- `DELETE /api/v1/workspaces/delete/?workspace=<slug>` soft-deletes a workspace.
- `GET /api/v1/workspaces/team/list/?workspace=<slug>` lists active and invited members.
- `GET /api/v1/workspaces/team/details/?workspace=<slug>&member_email=<email>` gets a member.
- `POST /api/v1/workspaces/team/invite/?workspace=<slug>` emails an invitation.
- `GET/PATCH /api/v1/workspaces/team/role/?workspace=<slug>&member_email=<email>` manages a member role.
- `DELETE /api/v1/workspaces/team/remove-member/?workspace=<slug>&member_email=<email>` removes a member.
- `POST /api/v1/workspaces/team/accept-invite/` accepts an emailed token for the authenticated user.

`GET /api/v1/chatbots/list/` returns only chatbots created by the authenticated
user by default. Pass `shared_with_me=true` to return chatbots shared through an
active workspace membership or direct chatbot assignment instead. Each item
includes its workspace, and `workspace=<slug>` restricts either list mode to one
workspace.


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
