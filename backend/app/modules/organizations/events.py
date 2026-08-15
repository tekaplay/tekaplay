"""Events published by the organizations module.

Invitation tokens travel only inside events consumed server-side (the
notifications module renders them into emails) — never returned in API
responses, mirroring app.modules.auth.events."""
INVITATION_CREATED = "organization.invitation_created"
MEMBER_JOINED = "organization.member_joined"
MEMBER_REMOVED = "organization.member_removed"
