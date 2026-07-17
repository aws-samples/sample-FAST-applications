#!/usr/bin/env python3

"""Create (or verify / clean up) demo Cognito users for an on-site demo.

A demo is run on multiple PCs, each of which needs its own login for every
role. This script provisions ``SET_COUNT`` sets of one user per role, with a
shared password, against the Cognito User Pool of a deployed FAST stack:

    <prefix>+finance-1@<domain>   -> group "finance"      (department finance)
    <prefix>+engineer-1@<domain>  -> group "engineering"  (department engineering)
    <prefix>+guest-1@<domain>     -> (no group)           (department guest)
    <prefix>+finance-2@<domain>   -> ...
    ...
    <prefix>+guest-<SET_COUNT>@<domain>

Group membership is the source of truth for authorization: the Pre-Token
Generation Lambda maps a user's Cognito group to the Cedar ``department`` claim
(see infra-cdk/lambdas/pretoken-v3/index.py). "guest" is NOT a group -- it is
the fallback the Lambda assigns to a user in no recognised group, and Cedar's
deny-by-default then rejects every Gateway tool for them. So ``finance`` and
``engineer`` users are added to a group while ``guest`` users are left
group-less on purpose.

Secrets and environment-specific values are read from a ``.env`` file
(scripts/.env, git-ignored) so they never live in this script. The target
User Pool / Client IDs are resolved at runtime from the CloudFormation stack
outputs -- never hard-coded -- so the script follows a redeploy automatically.

Required .env keys (see scripts/.env.example):
    DEMO_EMAIL_PREFIX   local-part before "+role-no" (e.g. an email alias)
    DEMO_EMAIL_DOMAIN   email domain (e.g. example.com)
    DEMO_PASSWORD       shared permanent password (must satisfy the pool policy)
    DEMO_STACK_NAME     CloudFormation stack whose Cognito pool is targeted

Usage:
    uv run scripts/create-demo-users.py create     # provision all users
    uv run scripts/create-demo-users.py verify     # log in as each, check groups
    uv run scripts/create-demo-users.py cleanup     # delete all demo users
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

# boto3's generated service clients expose methods dynamically, so there is no
# static type for "a cognito-idp client". Aliasing to Any keeps the call sites
# readable while letting both the stub-less environment (CI runs ruff only) and
# a boto3-stubs install type-check cleanly.
CognitoIdpClient = Any

# scripts/utils.py provides get_stack_config() (CloudFormation outputs lookup)
# and the shared coloured-output helpers used across the deployment scripts.
sys.path.insert(0, str(Path(__file__).parent))
from utils import get_stack_config, print_msg, print_section  # noqa: E402

# Role -> Cognito group. None means "create the user but add it to no group",
# which the Pre-Token Lambda classifies as department=guest. "engineer" is the
# demo-facing role label; the actual group created by the CDK stack is
# "engineering" (infra-cdk/lib/cognito-stack.ts), so the two differ on purpose.
ROLE_TO_GROUP: dict[str, str | None] = {
    "finance": "finance",
    "engineer": "engineering",
    "guest": None,
}

# Number of demo sets (one set = one user per role). One set per demo PC.
SET_COUNT = 4

# Substring that identifies the admin user created by the CDK stack. The
# cleanup path refuses to touch any user matching it, so an operator can never
# delete the admin by pointing this script at the wrong prefix.
PROTECTED_USERNAME_SUBSTRING = "+fastprojectadmin@"

# .env keys. DEMO_PASSWORD / DEMO_EMAIL_* are secrets or PII and must never be
# committed; DEMO_STACK_NAME is environment-specific.
ENV_KEYS = (
    "DEMO_EMAIL_PREFIX",
    "DEMO_EMAIL_DOMAIN",
    "DEMO_PASSWORD",
    "DEMO_STACK_NAME",
)


class DemoConfig:
    """Resolved demo settings: secrets from .env, IDs from CloudFormation."""

    def __init__(
        self,
        prefix: str,
        domain: str,
        password: str,
        stack_name: str,
        region: str,
        user_pool_id: str,
        client_id: str,
    ) -> None:
        self.prefix = prefix
        self.domain = domain
        self.password = password
        self.stack_name = stack_name
        self.region = region
        self.user_pool_id = user_pool_id
        self.client_id = client_id

    def email(self, role: str, number: int) -> str:
        """Build the alias email for a (role, set number) pair.

        Args:
            role: One of the keys of ROLE_TO_GROUP.
            number: 1-based set number.

        Returns:
            Address of the form ``<prefix>+<role>-<number>@<domain>``.
        """
        return f"{self.prefix}+{role}-{number}@{self.domain}"

    def all_users(self) -> list[tuple[str, str | None]]:
        """List every (email, group) pair this script manages.

        Returns:
            ``SET_COUNT`` x ``len(ROLE_TO_GROUP)`` pairs; group is None for guest.
        """
        users: list[tuple[str, str | None]] = []
        for number in range(1, SET_COUNT + 1):
            for role, group in ROLE_TO_GROUP.items():
                users.append((self.email(role, number), group))
        return users


def load_config() -> DemoConfig:
    """Load secrets from scripts/.env and resolve Cognito IDs from CloudFormation.

    Returns:
        A fully populated DemoConfig.

    Exits:
        With code 1 if a required .env key is missing or the stack lacks the
        expected Cognito outputs.
    """
    # Load scripts/.env explicitly so the script works regardless of CWD.
    env_path = Path(__file__).parent / ".env"
    load_dotenv(dotenv_path=env_path)

    missing = [key for key in ENV_KEYS if not os.environ.get(key)]
    if missing:
        print_msg(
            f"Missing required .env keys: {', '.join(missing)}. "
            f"Copy scripts/.env.example to scripts/.env and fill it in.",
            "error",
        )
        sys.exit(1)

    stack_name = os.environ["DEMO_STACK_NAME"]
    # get_stack_config() reads the named stack's CloudFormation outputs and
    # derives the region from the stack ARN, so the User Pool / Client IDs are
    # always the live ones -- not a hard-coded value that breaks on redeploy.
    stack = get_stack_config(stack_name)
    outputs = stack["outputs"]

    try:
        user_pool_id = outputs["CognitoUserPoolId"]
        client_id = outputs["CognitoClientId"]
    except KeyError as exc:
        print_msg(
            f"Stack '{stack_name}' is missing output {exc}. "
            "Is it the correct FAST stack?",
            "error",
        )
        sys.exit(1)

    return DemoConfig(
        prefix=os.environ["DEMO_EMAIL_PREFIX"],
        domain=os.environ["DEMO_EMAIL_DOMAIN"],
        password=os.environ["DEMO_PASSWORD"],
        stack_name=stack_name,
        region=stack["region"],
        user_pool_id=user_pool_id,
        client_id=client_id,
    )


def _idp(cfg: DemoConfig) -> CognitoIdpClient:
    """Return a cognito-idp client pinned to the stack's region."""
    return boto3.client("cognito-idp", region_name=cfg.region)


def _ensure_user(
    idp: CognitoIdpClient, cfg: DemoConfig, email: str, group: str | None
) -> None:
    """Create the user (idempotent), set the shared password, assign the group.

    Args:
        idp: cognito-idp client.
        cfg: Resolved demo configuration.
        email: User's email / username.
        group: Cognito group to add the user to, or None to leave group-less.
    """
    try:
        idp.admin_create_user(
            UserPoolId=cfg.user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "email_verified", "Value": "true"},
            ],
            # Suppress the invitation email: the password is shared out of band.
            MessageAction="SUPPRESS",
        )
        print_msg(f"created: {email}", "success")
    except idp.exceptions.UsernameExistsException:
        print_msg(f"exists : {email}", "info")

    # Permanent password (CONFIRMED state) so USER_PASSWORD_AUTH works without a
    # forced reset on first login.
    idp.admin_set_user_password(
        UserPoolId=cfg.user_pool_id,
        Username=email,
        Password=cfg.password,
        Permanent=True,
    )

    if group:
        idp.admin_add_user_to_group(
            UserPoolId=cfg.user_pool_id, Username=email, GroupName=group
        )
        print(f"   group: {email} -> {group}")
    else:
        print(f"   group: {email} -> (none, guest)")


def create(cfg: DemoConfig) -> int:
    """Provision every demo user.

    Returns:
        Process exit code (0 on success).
    """
    print_section(
        f"Creating {SET_COUNT * len(ROLE_TO_GROUP)} demo users in {cfg.user_pool_id}"
    )
    idp = _idp(cfg)
    for email, group in cfg.all_users():
        _ensure_user(idp, cfg, email, group)
    print_msg("All demo users provisioned.", "success")
    return 0


def _decode_groups(access_token: str) -> list[str]:
    """Decode the cognito:groups claim from an access token (no verification).

    The token is already validated by Cognito at issuance; here we only read it
    to confirm group assignment, so signature verification is unnecessary.

    Args:
        access_token: A Cognito access token (JWT).

    Returns:
        The cognito:groups claim, or an empty list if absent.
    """
    payload_b64 = access_token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    groups = payload.get("cognito:groups", [])
    return list(groups)


def verify(cfg: DemoConfig) -> int:
    """Log in as every demo user and confirm group membership.

    Returns:
        Process exit code (0 if all users behave as expected, 1 otherwise).
    """
    print_section(f"Verifying {SET_COUNT * len(ROLE_TO_GROUP)} demo users")
    idp = _idp(cfg)
    failures = 0
    for email, expected_group in cfg.all_users():
        try:
            result = idp.initiate_auth(
                ClientId=cfg.client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": email, "PASSWORD": cfg.password},
            )
            groups = _decode_groups(result["AuthenticationResult"]["AccessToken"])
            expected = [expected_group] if expected_group else []
            if groups == expected:
                print_msg(
                    f"login OK: {email} -> groups={groups or '(none, guest)'}",
                    "success",
                )
            else:
                print_msg(
                    f"login OK but groups mismatch: {email} "
                    f"-> got {groups}, expected {expected or '(none)'}",
                    "error",
                )
                failures += 1
        except ClientError as exc:
            print_msg(
                f"login FAIL: {email} -> {exc.response['Error']['Code']}", "error"
            )
            failures += 1

    if failures:
        print_msg(f"{failures} user(s) failed verification.", "error")
        return 1
    print_msg("All demo users verified.", "success")
    return 0


def cleanup(cfg: DemoConfig) -> int:
    """Delete every demo user. Never touches the protected admin user.

    Returns:
        Process exit code (0 on success).
    """
    print_section(f"Deleting demo users from {cfg.user_pool_id}")
    idp = _idp(cfg)
    for email, _ in cfg.all_users():
        if PROTECTED_USERNAME_SUBSTRING in email:
            print_msg(f"SKIP (protected): {email}", "info")
            continue
        try:
            idp.admin_delete_user(UserPoolId=cfg.user_pool_id, Username=email)
            print_msg(f"deleted: {email}", "success")
        except idp.exceptions.UserNotFoundException:
            print_msg(f"absent : {email}", "info")
    print_msg("Cleanup complete.", "success")
    return 0


def main() -> int:
    """Parse the subcommand and dispatch.

    Returns:
        Process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        default="create",
        choices=("create", "verify", "cleanup"),
        help="create (default): provision users; verify: log in + check groups; "
        "cleanup: delete demo users",
    )
    args = parser.parse_args()

    cfg = load_config()
    dispatch = {"create": create, "verify": verify, "cleanup": cleanup}
    return dispatch[args.command](cfg)


if __name__ == "__main__":
    sys.exit(main())
