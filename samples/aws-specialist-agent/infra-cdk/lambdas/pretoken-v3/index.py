"""
Pre-Token Generation Lambda (V3) for M2M flows.

Injects CUSTOM claims into M2M access tokens for AgentCore Policy
enforcement. This Lambda fires on BOTH user login and M2M token generation.
Only M2M flows (Client Credentials grant) are processed; user login flows
are passed through unchanged.

Custom claims injected (application-defined, not standard JWT/OIDC claims):
  - user_id:    The authenticated user's ID (e.g., "yourname@company.com")
  - department: The user's department (e.g., "finance")
  - role:       The user's role (e.g., "admin")

These claim names are arbitrary — you can define any names you need.
Just ensure the names match between this Lambda's output and the Cedar
policy's principal.getTag() references.

The identity is read from clientMetadata. The runtime nests the user's JWT
`sub` and their `cognito:groups` inside the reserved `aws_client_metadata`
custom_parameters key (see agent/strands-single-agent/tools/gateway.py).
AgentCore GetResourceOauth2Token passes that JSON through to Cognito's
/oauth2/token request unchanged; Cognito (not AgentCore) is what forwards the
`aws_client_metadata` body parameter to this trigger as ClientMetadata, which
surfaces here as clientMetadata.verified_user_id / verified_groups. This only
happens for M2M (client credentials) flows with a v3+ pre-token trigger.

Department/role assignment is driven by the user's Cognito group membership,
which the runtime already read from the validated access token:
  - `cognito:groups` is present in the access token (groups are an
    authorization concept), so the runtime forwards it as verified_groups
  - this Lambda maps the first recognised group name to the `department` claim
    and derives `role` via GROUP_ROLES; users in no group are "guest"

Why groups-from-token instead of an API lookup: the access token already proves
the membership, so we avoid an AdminListGroupsForUser call. That removes the
Lambda's need for cognito-idp:AdminListGroupsForUser IAM permission and the
UserPool <-> Lambda circular dependency it caused in the CDK stack. (The earlier
email-substring logic never worked here: the pool uses
UsernameAttributes=["email"], so `sub` is a UUID and substring checks against it
silently classified everyone as "guest".)
"""

# Maps a Cognito group name -> the role claim. The group name itself is used as
# the `department` claim (Cedar evaluates principal.getTag("department")).
GROUP_ROLES = {
    "finance": "admin",
    "engineering": "developer",
    "guest": "viewer",
}


def _resolve_department_role(groups: list[str]) -> tuple[str, str]:
    """Map the user's Cognito groups to a (department, role) pair.

    Returns ("guest", "viewer") when the user is in no recognised group,
    matching Cedar deny-by-default for unknown principals.
    """
    for name in groups:
        if name in GROUP_ROLES:
            return name, GROUP_ROLES[name]
    return "guest", "viewer"


def _parse_verified_groups(meta: dict) -> list[str]:
    """Read verified_groups (a comma-separated string) from clientMetadata.

    The runtime joins the user's groups with commas because Cognito's
    ClientMetadata is a flat string->string map and rejects nested array values
    (`invalid_aws_client_metadata`). Returns an empty list (-> guest) when the
    value is missing.
    """
    raw = meta.get("verified_groups", "")
    return [g.strip() for g in raw.split(",") if g.strip()]


def lambda_handler(event: dict, context: dict) -> dict:
    """
    Cognito V3 Pre-Token Generation trigger handler.

    Args:
        event: Cognito trigger event containing triggerSource and request metadata.
        context: Lambda context object.

    Returns:
        Modified event with user identity claims injected into the M2M access token.
    """
    print(f"[PRE-TOKEN] Trigger source: {event.get('triggerSource')}")

    # Only process M2M flows (Client Credentials grant)
    if event["triggerSource"] != "TokenGeneration_ClientCredentials":
        print("[PRE-TOKEN] Not a Client Credentials flow - skipping")
        return event

    # Read the verified identity from clientMetadata. The runtime nests both
    # values inside the reserved aws_client_metadata custom_parameters key;
    # Cognito forwards that body parameter to this trigger as ClientMetadata.
    meta = event["request"].get("clientMetadata", {})
    user_id = meta.get("verified_user_id", "")
    groups = _parse_verified_groups(meta)

    if not groups:
        print("[PRE-TOKEN] no verified_groups in metadata - defaulting to guest")

    # Map the user's groups (already proven by the validated token) to the
    # department/role claims. No AdminListGroupsForUser call needed.
    department, role = _resolve_department_role(groups)
    print(
        f"[PRE-TOKEN] Assigned from groups {groups}: department={department}, role={role}"
    )

    # Inject CUSTOM claims into the M2M Access Token.
    # These are application-defined claims
    # added via Cognito V3 Pre-Token Generation trigger (claimsToAddOrOverride).
    #
    # At the AgentCore Gateway, the JWT Authorizer maps ALL token claims
    # (both standard and custom) to Cedar principal tags:
    #   Custom claim "user_id"    → principal.getTag("user_id")
    #   Custom claim "department" → principal.getTag("department")
    #   Custom claim "role"       → principal.getTag("role")
    #
    # Standard claims (sub, iss, client_id, exp, etc.) are also available as tags
    # but are managed automatically by Cognito and cannot be overridden here.
    event["response"]["claimsAndScopeOverrideDetails"] = {
        "accessTokenGeneration": {
            "claimsToAddOrOverride": {
                "user_id": user_id,
                "department": department,
                "role": role,
            }
        }
    }

    print("[PRE-TOKEN] Claims injected successfully")
    return event
