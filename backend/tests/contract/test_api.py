"""Contract tests for the HTTP API, including security policies."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kpir_converter.api.deps import CSRF_COOKIE, CSRF_HEADER
from kpir_converter.application.services import ExtractionService
from kpir_converter.main import create_app


@pytest.fixture()
def client(ctx, settings):
    app = create_app(settings, ctx)
    with TestClient(app) as test_client:
        # Prime the CSRF cookie the way a browser would.
        test_client.get("/api/v1/health")
        token = test_client.cookies.get(CSRF_COOKIE)
        test_client.headers.update({CSRF_HEADER: token or ""})
        yield test_client


@pytest.fixture()
def loaded(client, ctx, sample_pdf: Path):
    batch = client.post("/api/v1/batches", json={"displayName": "Paczka"}).json()
    response = client.post(
        f"/api/v1/batches/{batch['id']}/documents",
        files=[("files", ("kpir.pdf", sample_pdf.read_bytes(), "application/pdf"))],
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["documents"][0]["id"]
    ExtractionService(ctx).run(document_id)
    return batch["id"], document_id


class TestHealthAndProfiles:
    def test_health_reports_offline(self, client) -> None:
        body = client.get("/api/v1/health").json()
        assert body["status"] == "ok"
        assert body["offline"] is True
        assert "kpir_pl_2018@1" in body["profiles"]

    def test_profiles_expose_columns(self, client) -> None:
        profiles = client.get("/api/v1/profiles").json()
        profile = profiles[0]
        keys = [c["key"] for c in profile["columns"]]
        assert "total_expenses" in keys
        assert profile["defaultFilterColumn"] == "total_expenses"
        # camelCase contract for the TypeScript client.
        assert "defaultExportColumns" in profile

    def test_openapi_is_served(self, client) -> None:
        spec = client.get("/api/v1/openapi.json").json()
        assert spec["info"]["title"] == "KPiR Converter API"
        assert "/api/v1/export-previews" in spec["paths"]
        assert "/api/v1/exports" in spec["paths"]


class TestSecurity:
    def test_security_headers_present(self, client) -> None:
        response = client.get("/api/v1/health")
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp
        assert "connect-src 'self'" in csp
        # No external origins may appear anywhere in the policy.
        assert "http://" not in csp and "https://" not in csp
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"

    def test_csrf_required_for_mutations(self, ctx, settings) -> None:
        app = create_app(settings, ctx)
        with TestClient(app) as bare:
            bare.get("/api/v1/health")
            response = bare.post("/api/v1/batches", json={"displayName": "x"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_TOKEN_INVALID"

    def test_host_header_allowlist(self, client) -> None:
        response = client.get("/api/v1/health", headers={"Host": "evil.example.com"})
        assert response.status_code == 421
        assert response.json()["error"]["code"] == "HOST_NOT_ALLOWED"

    def test_cross_origin_mutation_blocked(self, client) -> None:
        response = client.post(
            "/api/v1/batches",
            json={"displayName": "x"},
            headers={"Origin": "http://evil.example.com"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"

    def test_non_pdf_upload_rejected(self, client) -> None:
        batch = client.post("/api/v1/batches", json={"displayName": "p"}).json()
        response = client.post(
            f"/api/v1/batches/{batch['id']}/documents",
            files=[("files", ("evil.pdf", b"<html>nope</html>", "application/pdf"))],
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "NOT_A_PDF"

    def test_errors_have_stable_shape(self, client) -> None:
        body = client.get("/api/v1/documents/does-not-exist").json()
        assert set(body["error"]) >= {"code", "message", "correlationId"}
        assert body["error"]["code"] == "DOCUMENT_NOT_FOUND"


class TestDocumentsApi:
    def test_document_lifecycle(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        document = client.get(f"/api/v1/documents/{document_id}").json()
        assert document["status"] == "ready"
        assert document["recordCount"] == 5
        assert document["pageCount"] == 2
        assert document["detectedProfileId"] == "kpir_pl_2018@1"

    def test_records_are_paginated(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        first = client.get(f"/api/v1/documents/{document_id}/records?limit=2").json()
        assert len(first["items"]) == 2
        assert first["total"] == 5
        assert first["nextCursor"]
        second = client.get(
            f"/api/v1/documents/{document_id}/records?limit=2&cursor={first['nextCursor']}"
        ).json()
        assert len(second["items"]) == 2

    def test_money_is_a_string_in_json(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        items = client.get(f"/api/v1/documents/{document_id}/records?limit=5").json()["items"]
        cell = items[0]["cells"]["total_income"]
        assert cell["value"] == "1300.50"
        assert isinstance(cell["value"], str)

    def test_cell_exposes_source_span(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        items = client.get(f"/api/v1/documents/{document_id}/records?limit=1").json()["items"]
        source = items[0]["cells"]["business_date"]["source"]
        assert source["page"] == 1
        assert len(source["bbox"]) == 4
        assert all(0.0 <= v <= 1.0 for v in source["bbox"])

    def test_page_image_is_rendered(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        response = client.get(f"/api/v1/documents/{document_id}/pages/1/image")
        assert response.status_code == 200
        assert response.headers["content-type"] in ("image/webp", "image/png")
        assert len(response.content) > 1000

    def test_edit_cell_and_conflict(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        items = client.get(f"/api/v1/documents/{document_id}/records?limit=1").json()["items"]
        cell = items[0]["cells"]["contractor_name"]

        ok = client.patch(
            f"/api/v1/cells/{cell['id']}",
            json={"value": "Nowa Nazwa", "baseRevision": cell["revision"]},
        )
        assert ok.status_code == 200
        assert ok.json()["cell"]["value"] == "Nowa Nazwa"
        assert ok.json()["cell"]["isManual"] is True

        conflict = client.patch(
            f"/api/v1/cells/{cell['id']}",
            json={"value": "Jeszcze inna", "baseRevision": cell["revision"]},
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "REVISION_CONFLICT"
        assert conflict.json()["error"]["currentValue"] == "Nowa Nazwa"

    def test_invalid_money_edit_is_rejected(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        items = client.get(f"/api/v1/documents/{document_id}/records?limit=1").json()["items"]
        cell = items[0]["cells"]["total_income"]
        response = client.patch(
            f"/api/v1/cells/{cell['id']}",
            json={"value": "nie kwota", "baseRevision": cell["revision"]},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_MONEY"

    def test_revert_correction(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        items = client.get(f"/api/v1/documents/{document_id}/records?limit=1").json()["items"]
        cell = items[0]["cells"]["notes"]
        patched = client.patch(
            f"/api/v1/cells/{cell['id']}",
            json={"value": "uwaga", "baseRevision": cell["revision"]},
        ).json()["cell"]
        reverted = client.request(
            "DELETE",
            f"/api/v1/cells/{cell['id']}/correction",
            json={"baseRevision": patched["revision"]},
        )
        assert reverted.status_code == 200
        assert reverted.json()["cell"]["isManual"] is False

    def test_delete_document(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        assert client.delete(f"/api/v1/documents/{document_id}").status_code == 204
        assert client.get(f"/api/v1/documents/{document_id}").status_code == 404


class TestExportApi:
    def _definition(self, document_id: str, **kwargs) -> dict:
        body = {
            "scope": {"type": "documents", "documentIds": [document_id]},
            "columnKeys": ["business_date", "evidence_number", "total_expenses"],
            "policy": "draft",
        }
        body.update(kwargs)
        return body

    def test_preview_returns_counts_and_revision(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        preview = client.post("/api/v1/export-previews", json=self._definition(document_id)).json()
        assert preview["matchingRowCount"] == 5
        assert preview["columnCount"] == 3
        assert preview["sourceRevision"]
        assert len(preview["sampleRows"]) == 5
        assert [c["key"] for c in preview["columnLabels"]] == [
            "business_date",
            "evidence_number",
            "total_expenses",
        ]

    def test_preview_with_money_filter(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        body = self._definition(
            document_id,
            filters=[
                {
                    "type": "money_range",
                    "columnKey": "total_expenses",
                    "min": "400.00",
                    "max": "1000.00",
                    "bounds": "inclusive",
                }
            ],
        )
        preview = client.post("/api/v1/export-previews", json=body).json()
        assert preview["matchingRowCount"] == 2
        assert preview["excludedForNullOrInvalidCount"] == 1

    def test_min_greater_than_max_rejected(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        body = self._definition(
            document_id,
            filters=[
                {"type": "money_range", "columnKey": "total_expenses", "min": "900", "max": "100"}
            ],
        )
        response = client.post("/api/v1/export-previews", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "MIN_GREATER_THAN_MAX"

    def test_filter_on_text_column_rejected(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        body = self._definition(
            document_id,
            filters=[{"type": "money_range", "columnKey": "evidence_number", "min": "1"}],
        )
        response = client.post("/api/v1/export-previews", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "FILTER_COLUMN_TYPE_MISMATCH"

    def test_no_columns_rejected(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        response = client.post(
            "/api/v1/export-previews", json=self._definition(document_id, columnKeys=[])
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NO_COLUMNS_SELECTED"

    def test_stale_revision_returns_409(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        body = self._definition(document_id, sourceRevision="stale")
        response = client.post("/api/v1/exports", json=body)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "STALE_PREVIEW"

    def test_full_export_flow(self, client, ctx, loaded) -> None:
        from kpir_converter.application.services import ExportService

        _batch_id, document_id = loaded
        preview = client.post("/api/v1/export-previews", json=self._definition(document_id)).json()
        body = self._definition(document_id, sourceRevision=preview["sourceRevision"])
        created = client.post("/api/v1/exports", json=body)
        assert created.status_code == 202
        export_id = created.json()["id"]

        # The worker performs the actual build.
        ExportService(ctx).build(export_id)

        status = client.get(f"/api/v1/exports/{export_id}").json()
        assert status["status"] == "ready"
        assert status["rowCount"] == 5
        assert status["downloadUrl"] == f"/api/v1/exports/{export_id}/file"

        download = client.get(status["downloadUrl"])
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/vnd.openxmlformats")
        assert download.content[:2] == b"PK"

    def test_strict_policy_blocks_critical_issues(self, client, ctx, loaded) -> None:
        _batch_id, document_id = loaded
        # Force a critical issue by clearing a required cell.
        items = client.get(f"/api/v1/documents/{document_id}/records?limit=1").json()["items"]
        cell = items[0]["cells"]["evidence_number"]
        client.patch(
            f"/api/v1/cells/{cell['id']}", json={"value": "", "baseRevision": cell["revision"]}
        )
        response = client.post(
            "/api/v1/exports", json=self._definition(document_id, policy="strict")
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "BLOCKING_ISSUES"

    def test_presets_round_trip(self, client, loaded) -> None:
        _batch_id, document_id = loaded
        created = client.post(
            "/api/v1/export-presets",
            json={
                "name": "Bez opisu",
                "profileId": "kpir_pl_2018",
                "definition": {"columnKeys": ["business_date", "total_expenses"]},
            },
        )
        assert created.status_code == 201
        listed = client.get("/api/v1/export-presets").json()
        assert listed[0]["name"] == "Bez opisu"
        assert client.delete(f"/api/v1/export-presets/{created.json()['id']}").status_code == 204


class TestJobsApi:
    def test_jobs_are_listed(self, client, loaded) -> None:
        batch_id, _document_id = loaded
        jobs = client.get(f"/api/v1/jobs?batchId={batch_id}").json()["items"]
        assert jobs
        assert jobs[0]["type"] == "EXTRACT_DOCUMENT"

    def test_cancel_queued_job(self, client, ctx, loaded) -> None:
        batch_id, document_id = loaded
        job_id = ctx.jobs.enqueue("EXTRACT_DOCUMENT", document_id=document_id, batch_id=batch_id)
        response = client.post(f"/api/v1/jobs/{job_id}/cancel")
        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"

    def test_cancel_unknown_job(self, client) -> None:
        assert client.post("/api/v1/jobs/nope/cancel").status_code == 404
