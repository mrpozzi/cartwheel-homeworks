"""Authentication tests for the Homework 2 endpoint.

Offline: no Langfuse, Docker, or model provider key. Tracing itself is
verified through the recorded spans in Langfuse (Part E).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from server import app as server_app


@pytest.fixture(autouse=True)
def clear_sessions() -> None:
    server_app._SESSIONS.clear()


def test_create_session_rejects_role_that_differs_from_database(world: dict) -> None:
    # User 9002 is a merchant in the seeded world; claiming support must fail.
    with pytest.raises(HTTPException) as excinfo:
        server_app.create_session(server_app.SessionCreate(user_id=9002, role="support"))
    assert excinfo.value.status_code == 403
    assert server_app._SESSIONS == {}


def test_token_for_one_session_cannot_authorize_another(
    world: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RunnerMustNotRun:
        @staticmethod
        async def run(*args: object, **kwargs: object) -> None:
            raise AssertionError("the agent ran despite a token for another session")

    monkeypatch.setattr(server_app, "Runner", RunnerMustNotRun)
    first = server_app.create_session(server_app.SessionCreate(user_id=1, role="shopper"))
    second = server_app.create_session(server_app.SessionCreate(user_id=2, role="shopper"))

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            server_app.post_message(
                second["session_id"],
                server_app.MessageIn(message="Show my recent orders."),
                authorization=f"Bearer {first['token']}",
            )
        )
    assert excinfo.value.status_code == 403
