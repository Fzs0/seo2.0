from __future__ import annotations

import pytest

from app.core.database_version import PostgreSQLVersionError, require_postgresql_17


class _ScalarResult:
    def __init__(self, value: int):
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _Connection:
    def __init__(self, version_num: int):
        self.version_num = version_num

    async def execute(self, statement):
        assert str(statement) == "SHOW server_version_num"
        return _ScalarResult(self.version_num)


class _Begin:
    def __init__(self, version_num: int):
        self.connection = _Connection(version_num)

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Engine:
    def __init__(self, version_num: int):
        self.version_num = version_num

    def connect(self):
        return _Begin(self.version_num)


@pytest.mark.asyncio
async def test_postgresql_17_is_accepted():
    assert await require_postgresql_17(_Engine(170006)) == 17


@pytest.mark.asyncio
@pytest.mark.parametrize("version_num", [140012, 150008, 160004, 180001])
async def test_non_postgresql_17_is_rejected_with_clear_error(version_num: int):
    with pytest.raises(PostgreSQLVersionError, match="requires PostgreSQL 17"):
        await require_postgresql_17(_Engine(version_num))


@pytest.mark.asyncio
async def test_application_lifespan_refuses_to_start_when_database_gate_fails(monkeypatch):
    import app.main as main

    async def reject(_engine):
        raise PostgreSQLVersionError("requires PostgreSQL 17")

    monkeypatch.setattr(main, "require_postgresql_17", reject)
    with pytest.raises(PostgreSQLVersionError, match="requires PostgreSQL 17"):
        async with main.lifespan(main.app):
            pytest.fail("unsupported database must fail before application startup")
