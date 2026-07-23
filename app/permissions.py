"""
Permission control module.

Maps API keys and Feishu user IDs to permission profiles that define:
- Which BQCA agent to use
- Which tables are accessible
- Column-level restrictions per table
- Row-level filter conditions

Enforcement layers:
1. Soft: Inject access rules into BQCA systemInstruction (prompt-based)
2. Hard: Post-check generated SQL against allowed tables/columns
3. Hard: Filter forbidden columns from returned result rows
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AccessRules:
    """Column/row level access rules for a permission profile."""
    allowed_tables: list[str] | None = None
    column_restrictions: dict[str, list[str]] = field(default_factory=dict)
    row_restrictions: str = ""

    @property
    def has_restrictions(self) -> bool:
        return bool(self.allowed_tables or self.column_restrictions or self.row_restrictions)


@dataclass
class PermissionProfile:
    """A named set of access rules bound to a specific BQCA agent."""
    name: str
    agent_id: str
    description: str = ""
    access_rules: AccessRules | None = None

    @property
    def is_admin(self) -> bool:
        return self.access_rules is None or not self.access_rules.has_restrictions


# ---------------------------------------------------------------------------
# Permission profiles — edit this dict to add/modify roles
# ---------------------------------------------------------------------------

PROFILES: dict[str, PermissionProfile] = {
    "admin": PermissionProfile(
        name="admin",
        agent_id="ecommerce-analyst-cn",
        description="Full access to all tables and columns",
    ),
    "sales": PermissionProfile(
        name="sales",
        agent_id="ecommerce-analyst-cn",
        description="Sales team - orders and products only, no PII",
        access_rules=AccessRules(
            allowed_tables=["orders", "products", "order_items"],
            column_restrictions={
                "users": ["id", "first_name", "last_name"],
            },
            row_restrictions="",
        ),
    ),
    "marketing": PermissionProfile(
        name="marketing",
        agent_id="ecommerce-analyst-cn",
        description="Marketing - all tables but no email/phone from users",
        access_rules=AccessRules(
            allowed_tables=None,
            column_restrictions={
                "users": ["id", "first_name", "last_name", "age", "gender", "state", "city", "country"],
            },
            row_restrictions="",
        ),
    ),
}

# API Key -> profile name mapping
API_KEY_MAP: dict[str, str] = {
    "UC-q_q4prqeYrb41-F8PRljuj29asWE4": "admin",
}

# Feishu user open_id -> profile name mapping
FEISHU_USER_MAP: dict[str, str] = {}


def get_profile_by_api_key(api_key: str) -> PermissionProfile | None:
    """Look up the permission profile for an API key."""
    profile_name = API_KEY_MAP.get(api_key)
    if profile_name and profile_name in PROFILES:
        return PROFILES[profile_name]
    return None


def get_profile_by_feishu_user(open_id: str) -> PermissionProfile | None:
    """Look up the permission profile for a Feishu user."""
    profile_name = FEISHU_USER_MAP.get(open_id)
    if profile_name and profile_name in PROFILES:
        return PROFILES[profile_name]
    return None


def get_default_profile() -> PermissionProfile:
    """Return the default profile (admin) for unrecognised users."""
    return PROFILES["admin"]


# ---------------------------------------------------------------------------
# System instruction builder
# ---------------------------------------------------------------------------

def build_access_system_instruction(profile: PermissionProfile) -> str:
    """
    Build a system instruction suffix that encodes the access rules.
    This gets injected into the BQCA request to guide the agent.
    """
    if profile.is_admin:
        return ""

    rules = profile.access_rules
    parts: list[str] = ["ACCESS CONTROL RULES - You MUST strictly follow these rules:"]

    if rules.allowed_tables:
        tables_str = ", ".join(rules.allowed_tables)
        parts.append(
            f"- You can ONLY query data from these tables: [{tables_str}]. "
            "If a user asks about other tables, reply: "
            "'Sorry, you do not have permission to view this data.'"
        )

    if rules.column_restrictions:
        for table, columns in rules.column_restrictions.items():
            cols_str = ", ".join(columns)
            parts.append(
                f"- For the {table} table, you can ONLY access these columns: [{cols_str}]. "
                "Do NOT query any other columns from this table."
            )

    if rules.row_restrictions:
        parts.append(f"- Row filter: {rules.row_restrictions}")

    parts.append(
        "- If a user asks for data you cannot access, reply: "
        "'Sorry, you do not have permission to view this data. "
        "Please contact the administrator.'"
    )

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# SQL post-check (hard enforcement)
# ---------------------------------------------------------------------------

_TABLE_REF_RE = re.compile(r"`?(?:\w+-\w+-\w+-\w+(?:-\w+)?\.)?(\w+)\.(\w+)`?")
_SIMPLE_TABLE_RE = re.compile(
    r"(?:FROM|JOIN|from|join)\s+`?(\w+)`?(?:\s+AS\s+\w+)?",
    re.IGNORECASE,
)


def extract_tables_from_sql(sql: str) -> list[str]:
    """Extract table names referenced in a SQL query (best-effort)."""
    tables: list[str] = []
    for m in _TABLE_REF_RE.finditer(sql):
        tables.append(m.group(2))
    for m in _SIMPLE_TABLE_RE.finditer(sql):
        name = m.group(1)
        if name.upper() not in ("SELECT", "UNNEST", "SUBQUERY") and name not in tables:
            tables.append(name)
    return tables


def check_sql_access(sql: str, profile: PermissionProfile) -> tuple[bool, str]:
    """
    Post-check: verify the generated SQL doesn't violate access rules.
    Returns (allowed, reason).
    """
    if profile.is_admin:
        return True, ""

    rules = profile.access_rules
    if not rules:
        return True, ""

    if rules.allowed_tables:
        referenced = extract_tables_from_sql(sql)
        for table in referenced:
            if table.lower() not in [t.lower() for t in rules.allowed_tables]:
                return False, f"SQL references forbidden table: {table}"

    return True, ""


# ---------------------------------------------------------------------------
# Result column filter (hard enforcement)
# ---------------------------------------------------------------------------

SENSITIVE_COLUMNS = frozenset({
    "email", "phone", "address", "street_address",
    "postal_code", "ip_address", "credit_card",
    "password", "ssn", "date_of_birth",
})


def filter_result_columns(
    fields: list[str],
    rows: list[dict],
    profile: PermissionProfile,
) -> tuple[list[str], list[dict]]:
    """
    Remove columns from result data that the user doesn't have access to.
    Returns (filtered_fields, filtered_rows).
    """
    if profile.is_admin or not profile.access_rules:
        return fields, rows

    # Collect all explicitly allowed columns from column_restrictions
    all_allowed: set[str] = set()
    for cols in profile.access_rules.column_restrictions.values():
        all_allowed.update(c.lower() for c in cols)

    if not all_allowed:
        return fields, rows

    # Identify denied columns: sensitive columns NOT in any allowed list
    deny_columns: set[str] = set()
    for f in fields:
        f_lower = f.lower()
        if f_lower in SENSITIVE_COLUMNS and f_lower not in all_allowed:
            deny_columns.add(f)

    if not deny_columns:
        return fields, rows

    logger.info("Filtering denied columns from result: %s", deny_columns)
    filtered_fields = [f for f in fields if f not in deny_columns]
    filtered_rows = [
        {k: v for k, v in row.items() if k not in deny_columns}
        for row in rows
    ]
    return filtered_fields, filtered_rows
