import re


def check_sql_safety(sql: str) -> bool:
    """Only allow SELECT statements."""
    cleaned = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    cleaned = re.sub(r"/\*.*?\*/", "", cleaned, flags=re.DOTALL)
    cleaned = cleaned.strip()
    first_word = cleaned.split()[0].upper() if cleaned.split() else ""
    return first_word == "SELECT"


def enforce_limit(sql: str, max_rows: int) -> str:
    """Ensure SQL has LIMIT and it does not exceed max_rows."""
    limit_pattern = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
    match = limit_pattern.search(sql)
    if match:
        current = int(match.group(1))
        if current > max_rows:
            sql = limit_pattern.sub(f"LIMIT {max_rows}", sql)
    else:
        sql = f"{sql.rstrip(';')} LIMIT {max_rows}"
    return sql
