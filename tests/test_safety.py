import pytest
from app.engine.safety import check_sql_safety, enforce_limit


def test_allows_select():
   assert check_sql_safety("SELECT * FROM t") is True


def test_blocks_drop():
   assert check_sql_safety("DROP TABLE t") is False


def test_blocks_delete():
   assert check_sql_safety("DELETE FROM t WHERE 1=1") is False


def test_blocks_update():
   assert check_sql_safety("UPDATE t SET a=1") is False


def test_blocks_insert():
   assert check_sql_safety("INSERT INTO t VALUES (1)") is False


def test_blocks_alter():
   assert check_sql_safety("ALTER TABLE t ADD COLUMN x INT") is False


def test_blocks_mixed_case():
   assert check_sql_safety("drop table t") is False


def test_enforce_limit_adds_limit():
   sql = "SELECT * FROM t"
   result = enforce_limit(sql, 1000)
   assert "LIMIT 1000" in result


def test_enforce_limit_reduces_existing():
   sql = "SELECT * FROM t LIMIT 5000"
   result = enforce_limit(sql, 1000)
   assert "LIMIT 1000" in result
   assert "5000" not in result


def test_enforce_limit_keeps_smaller():
   sql = "SELECT * FROM t LIMIT 100"
   result = enforce_limit(sql, 1000)
   assert "LIMIT 100" in result
