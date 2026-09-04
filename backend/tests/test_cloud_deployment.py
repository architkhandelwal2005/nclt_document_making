"""Focused checks for the cloud hosting boundary; no external service is contacted."""

import server


def test_production_same_origin_does_not_enable_development_cors_defaults():
    assert server.configured_cors_origins("PRODUCTION", "") == []


def test_explicit_cors_origins_are_preserved_for_a_deliberate_separate_frontend():
    assert server.configured_cors_origins(
        "PRODUCTION", "https://app.example.test, https://review.example.test "
    ) == ["https://app.example.test", "https://review.example.test"]


def test_development_keeps_the_local_react_server_origins():
    assert server.configured_cors_origins("DEVELOPMENT", "") == [
        "http://localhost:3000", "http://127.0.0.1:3000"
    ]
