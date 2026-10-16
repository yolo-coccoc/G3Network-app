"""Smoke test of the OpenAPI export behind the portal's generated client."""

import json

from app.api.openapi_export import serialize_openapi_document


def test_openapi_export_is_stable_json_with_the_auth_routes() -> None:
    """The export is valid JSON, identical on each call, and lists the login route."""
    document_text = serialize_openapi_document()

    assert document_text == serialize_openapi_document()
    document = json.loads(document_text)
    assert "/api/v1/auth/login" in document["paths"]
