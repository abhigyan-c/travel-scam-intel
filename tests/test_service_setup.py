import io

import pytest
from botocore.exceptions import ClientError

from deploy.check_services import check_bucket
from deploy.seed_vault import existing_values, planned_values


class Storage:
    def __init__(self, *, enforce_condition=True, anonymous=False):
        self.body = None
        self.enforce_condition = enforce_condition
        self.anonymous = anonymous

    def head_bucket(self, **kwargs):
        pass

    def put_object(self, *, Body, **kwargs):
        if self.body is not None and self.enforce_condition:
            raise ClientError(
                {
                    "Error": {"Code": "PreconditionFailed"},
                    "ResponseMetadata": {"HTTPStatusCode": 412},
                },
                "PutObject",
            )
        self.body = Body

    def get_object(self, **kwargs):
        if self.anonymous:
            raise ClientError(
                {"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}},
                "GetObject",
            )
        return {"Body": io.BytesIO(self.body)}

    def delete_object(self, **kwargs):
        self.body = None

    def head_object(self, **kwargs):
        if self.body is None:
            raise ClientError(
                {"Error": {"Code": "NotFound"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
                "HeadObject",
            )


def test_live_check_detects_broken_immutability_and_cleans_up():
    signed = Storage(enforce_condition=False)
    outcome = check_bucket(signed, Storage(anonymous=True), "synthetic-bucket")
    assert outcome["error"] == "ValueError"
    assert outcome["cleanup"] == "passed"
    assert signed.body is None


def test_live_check_verifies_roundtrip_condition_and_cleanup():
    outcome = check_bucket(Storage(), Storage(anonymous=True), "synthetic-bucket")
    assert outcome == {
        "roundtrip": "passed",
        "conditional_write": "passed",
        "anonymous_s3_access": "denied (HTTP 403)",
        "cleanup": "passed",
    }


def test_seed_preserves_existing_database_password_and_secrets():
    local = {
        key: "synthetic" for key in ("R2_ACCESS", "R2_SECRET", "SERPER_API_KEY", "SARVAM_API_KEY")
    }
    existing = {"POSTGRES_PASSWORD": "password with @ delimiters", "ADMIN_API_TOKEN": "keep"}
    planned = planned_values(local, existing, backup=False)
    assert "POSTGRES_PASSWORD" not in planned
    assert "ADMIN_API_TOKEN" not in planned
    assert "password%20with%20%40%20delimiters" in planned["DATABASE_URL"]
    assert planned_values(local, {**existing, **planned}, backup=False) == {}
    with pytest.raises(ValueError, match="matching_password"):
        planned_values(local, {"DATABASE_URL": "existing"}, backup=False)


def test_seed_rejects_duplicate_storage_aliases():
    with pytest.raises(ValueError, match="duplicate"):
        existing_values(
            [{"key": "R2_ACCESS", "value": "one"}, {"key": "S3_ACCESS_KEY", "value": "two"}]
        )
