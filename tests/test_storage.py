import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber

from app.core.config import Settings
from app.integrations.storage import create_storage_client, ensure_bucket


@pytest.fixture
def storage():
    client = create_storage_client(Settings(_env_file=None))
    yield client
    client.close()


def test_existing_bucket_is_not_recreated(storage):
    with Stubber(storage) as stub:
        stub.add_response("head_bucket", {}, {"Bucket": "raw-documents"})
        ensure_bucket(storage, "raw-documents")
        stub.assert_no_pending_responses()


def test_missing_bucket_is_created(storage):
    with Stubber(storage) as stub:
        stub.add_client_error(
            "head_bucket", "404", http_status_code=404, expected_params={"Bucket": "raw-documents"}
        )
        stub.add_response("create_bucket", {}, {"Bucket": "raw-documents"})
        ensure_bucket(storage, "raw-documents")
        stub.assert_no_pending_responses()


def test_permission_failure_does_not_attempt_bucket_creation(storage):
    with Stubber(storage) as stub:
        stub.add_client_error(
            "head_bucket", "403", http_status_code=403, expected_params={"Bucket": "raw-documents"}
        )
        with pytest.raises(ClientError):
            ensure_bucket(storage, "raw-documents")
        stub.assert_no_pending_responses()


def test_concurrent_bucket_creation_is_idempotent(storage):
    with Stubber(storage) as stub:
        stub.add_client_error(
            "head_bucket", "404", http_status_code=404, expected_params={"Bucket": "raw-documents"}
        )
        stub.add_client_error(
            "create_bucket",
            "BucketAlreadyOwnedByYou",
            http_status_code=409,
            expected_params={"Bucket": "raw-documents"},
        )
        ensure_bucket(storage, "raw-documents")
        stub.assert_no_pending_responses()
