"""
blob_storage.py

Uploads generated demo guide PDFs to Azure Blob Storage so results land in
one shared place instead of only on whoever's browser triggered the capture.
Also tracks async job status (queued/running/done/error) as small JSON blobs,
so the HTTP-facing functions can stay fast and a background function can do
the actual work without hitting Azure's ~230s HTTP response limit.

Reuses the storage account the Function App already has wired up via the
AzureWebJobsStorage connection string - no new secret or resource needed.

Local testing (outside of Azure) needs an Azurite emulator or a real storage
connection string in AzureWebJobsStorage; if that's not set, upload_pdf just
skips silently so local development isn't blocked by this.
"""

import datetime
import json
import logging
import os
import re

from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError

CONTAINER_NAME = "demo-guides"
STATUS_CONTAINER_NAME = "demo-job-status"
DOWNLOAD_URL_TTL_MINUTES = 60


def _sanitize_filename(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")
    return text or "meeting"


def _get_client() -> BlobServiceClient:
    connection_string = os.environ.get("AzureWebJobsStorage")
    if not connection_string:
        raise RuntimeError("AzureWebJobsStorage is not set - blob storage is not configured.")
    return BlobServiceClient.from_connection_string(connection_string)


def _get_container(client: BlobServiceClient, name: str):
    container_client = client.get_container_client(name)
    try:
        container_client.create_container()
    except ResourceExistsError:
        pass
    return container_client


def upload_pdf(pdf_bytes: bytes, subject: str = None, meeting_id: str = None) -> str:
    """Uploads the PDF to Blob Storage. Returns the blob name, or None if
    storage isn't configured (e.g. running locally without Azurite).
    """
    try:
        client = _get_client()
    except RuntimeError:
        logging.warning("AzureWebJobsStorage not set - skipping blob upload.")
        return None

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    label = _sanitize_filename(subject or meeting_id or "meeting")
    blob_name = f"{timestamp}_{label}.pdf"

    container_client = _get_container(client, CONTAINER_NAME)
    container_client.upload_blob(name=blob_name, data=pdf_bytes, overwrite=True)
    logging.info("Uploaded PDF to blob storage: %s/%s", CONTAINER_NAME, blob_name)
    return blob_name


def generate_download_url(blob_name: str) -> str:
    """Returns a time-limited, no-login-required URL the browser can hit
    directly to download the PDF. Sets content-disposition so it downloads
    as a file instead of opening inline.
    """
    client = _get_client()
    account_name = client.account_name
    account_key = client.credential.account_key
    sas_token = generate_blob_sas(
        account_name=account_name,
        container_name=CONTAINER_NAME,
        blob_name=blob_name,
        account_key=account_key,
        permission=BlobSasPermissions(read=True),
        expiry=datetime.datetime.utcnow() + datetime.timedelta(minutes=DOWNLOAD_URL_TTL_MINUTES),
        content_disposition='attachment; filename="demo_guide.pdf"',
    )
    return f"{client.url.rstrip('/')}/{CONTAINER_NAME}/{blob_name}?{sas_token}"


def write_job_status(job_id: str, status: str, **extra) -> None:
    """Writes/overwrites the status blob for a job. status is one of
    'queued', 'running', 'done', 'error'. Extra fields (e.g. error, blob_name)
    are merged into the stored JSON.
    """
    client = _get_client()
    container_client = _get_container(client, STATUS_CONTAINER_NAME)
    payload = {
        "job_id": job_id,
        "status": status,
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        **extra,
    }
    container_client.upload_blob(
        name=f"{job_id}.json", data=json.dumps(payload), overwrite=True
    )


def read_job_status(job_id: str) -> dict:
    """Returns the stored status dict for a job, or None if it doesn't exist."""
    client = _get_client()
    container_client = _get_container(client, STATUS_CONTAINER_NAME)
    try:
        data = container_client.download_blob(f"{job_id}.json").readall()
    except ResourceNotFoundError:
        return None
    return json.loads(data)
