"""Private original objects and durable, retryable cleanup."""

import hashlib
import io
import logging
from pathlib import Path
from uuid import uuid4

import certifi
from minio import Minio
from urllib3 import PoolManager, Retry, Timeout

logger = logging.getLogger(__name__)
CONTENT_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class ObjectStorage:
    def __init__(self, settings):
        self.settings = settings
        self.client = None

    def initialize(self):
        access = self.settings.minio_access_key.get_secret_value()
        secret = self.settings.minio_secret_key.get_secret_value()
        if not access or not secret:
            raise RuntimeError(
                "Configure KBAGENT_MINIO_ACCESS_KEY and KBAGENT_MINIO_SECRET_KEY in .env"
            )
        self.client = Minio(
            self.settings.minio_endpoint,
            access_key=access,
            secret_key=secret,
            secure=self.settings.minio_secure,
            region=self.settings.minio_region,
            http_client=PoolManager(
                timeout=Timeout(connect=5, read=30),
                retries=Retry(total=2, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]),
                cert_reqs="CERT_REQUIRED",
                ca_certs=certifi.where(),
            ),
        )
        if not self.client.bucket_exists(self.settings.minio_bucket):
            if not self.settings.minio_create_bucket:
                raise RuntimeError("Configured MinIO bucket does not exist")
            self.client.make_bucket(self.settings.minio_bucket)

    def health(self):
        if not self.client.bucket_exists(self.settings.minio_bucket):
            return False
        return True

    def upload(self, store, space_id, title, content, filename, data):
        key = f"originals/{space_id}/{uuid4().hex}"
        metadata = dict(
            object_key=key,
            filename=filename,
            content_type=CONTENT_TYPES[Path(filename).suffix.lower()],
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )
        store.reserve_object(key)
        try:
            self.client.put_object(
                self.settings.minio_bucket,
                key,
                io.BytesIO(data),
                len(data),
                content_type=metadata["content_type"],
            )
            return store.save_document(
                space_id,
                title,
                content,
                filename,
                original_file=metadata,
            )
        except Exception:
            try:
                store.abandon_object(key)
                self.collect(store)
            except Exception as exc:
                logger.warning("Original cleanup deferred (%s)", type(exc).__name__)
            raise

    def download(self, key):
        return self.client.get_object(self.settings.minio_bucket, key)

    def collect(self, store):
        for key in store.pending_deletions():
            try:
                self.client.remove_object(self.settings.minio_bucket, key)
                store.finish_deletion(key)
            except Exception as exc:
                logger.warning("Original deletion deferred (%s)", type(exc).__name__)
