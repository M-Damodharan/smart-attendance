import os
import base64
import io
import logging
from typing import Optional, Union
from PIL import Image
from flask import current_app
from app.db import get_db

logger = logging.getLogger(__name__)


def _get_selfie_dir() -> str:
    s_dir = current_app.config.get("SELFIE_DIR")
    if not s_dir:
        s_dir = os.path.join(current_app.instance_path, "selfies")
    os.makedirs(s_dir, exist_ok=True)
    return s_dir


def save_photo(
    image_or_bytes: Union[Image.Image, bytes, bytearray],
    filename: str,
    emp_code: str = "",
    mime_type: str = "image/jpeg"
) -> str:
    """
    Saves an image to local storage cache AND replicates to durable private cloud storage.
    Ensures images survive Render server restarts and redeployments.
    """
    safe_name = os.path.basename(filename)
    s_dir = _get_selfie_dir()
    local_path = os.path.join(s_dir, safe_name)

    # 1. Convert to bytes and save to local disk
    if isinstance(image_or_bytes, Image.Image):
        buf = io.BytesIO()
        image_or_bytes.save(buf, format="JPEG", quality=82)
        raw_bytes = buf.getvalue()
        with open(local_path, "wb") as f:
            f.write(raw_bytes)
    else:
        raw_bytes = bytes(image_or_bytes)
        with open(local_path, "wb") as f:
            f.write(raw_bytes)

    # 2. Replicate to durable cloud database (Turso LibSQL Cloud)
    try:
        db = get_db()
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS durable_photos (
                filename TEXT PRIMARY KEY,
                emp_code TEXT,
                photo_data TEXT,
                mime_type TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', '+5 hours', '+30 minutes'))
            )
            """
        )
        b64_content = base64.b64encode(raw_bytes).decode("ascii")
        db.execute(
            """
            INSERT OR REPLACE INTO durable_photos (filename, emp_code, photo_data, mime_type)
            VALUES (?, ?, ?, ?)
            """,
            (safe_name, emp_code.upper() if emp_code else "", b64_content, mime_type)
        )
        db.commit()
    except Exception as e:
        logger.warning(f"[photo_storage] Failed replicating photo {safe_name} to cloud DB: {e}")

    # 3. Optional S3 / R2 Object Storage upload if environment variables configured
    s3_bucket = os.environ.get("S3_BUCKET")
    if s3_bucket:
        try:
            import boto3
            s3_client = boto3.client(
                "s3",
                endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                region_name=os.environ.get("AWS_REGION", "ap-south-1")
            )
            s3_client.put_object(
                Bucket=s3_bucket,
                Key=f"selfies/{safe_name}",
                Body=raw_bytes,
                ContentType=mime_type
            )
        except Exception as e:
            logger.warning(f"[photo_storage] Failed uploading to S3 bucket {s3_bucket}: {e}")

    return safe_name


def get_photo_path(filename: str, emp_code: Optional[str] = None) -> Optional[str]:
    """
    Returns the absolute path to a photo.
    If absent from local ephemeral disk (e.g., after server restart),
    automatically fetches from durable cloud storage and repopulates the local cache.
    """
    safe_name = os.path.basename(filename)
    s_dir = _get_selfie_dir()
    local_path = os.path.join(s_dir, safe_name)

    if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
        return local_path

    # Check secondary instance/selfies path
    inst_path = os.path.join(current_app.instance_path, "selfies", safe_name)
    if os.path.exists(inst_path) and os.path.getsize(inst_path) > 0:
        return inst_path

    # Local file missing: Restore from cloud database
    try:
        db = get_db()
        row = db.execute(
            "SELECT photo_data, mime_type FROM durable_photos WHERE filename=?",
            (safe_name,)
        ).fetchone()
        if row and row["photo_data"]:
            raw_bytes = base64.b64decode(row["photo_data"])
            with open(local_path, "wb") as f:
                f.write(raw_bytes)
            logger.info(f"[photo_storage] Restored photo {safe_name} from cloud DB into local cache.")
            return local_path
    except Exception as e:
        logger.warning(f"[photo_storage] Failed retrieving photo {safe_name} from cloud DB: {e}")

    # Optional S3 / R2 Object Storage retrieval
    s3_bucket = os.environ.get("S3_BUCKET")
    if s3_bucket:
        try:
            import boto3
            s3_client = boto3.client(
                "s3",
                endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                region_name=os.environ.get("AWS_REGION", "ap-south-1")
            )
            resp = s3_client.get_object(Bucket=s3_bucket, Key=f"selfies/{safe_name}")
            raw_bytes = resp["Body"].read()
            with open(local_path, "wb") as f:
                f.write(raw_bytes)
            return local_path
        except Exception as e:
            logger.warning(f"[photo_storage] Failed downloading from S3 bucket {s3_bucket}: {e}")

    return None


def delete_photo(filename: str) -> bool:
    """Deletes photo from local disk cache and durable cloud storage."""
    safe_name = os.path.basename(filename)
    s_dir = _get_selfie_dir()
    local_path = os.path.join(s_dir, safe_name)
    if os.path.exists(local_path):
        try:
            os.remove(local_path)
        except Exception:
            pass

    try:
        db = get_db()
        db.execute("DELETE FROM durable_photos WHERE filename=?", (safe_name,))
        db.commit()
    except Exception as e:
        logger.warning(f"[photo_storage] Failed deleting photo {safe_name} from cloud DB: {e}")

    s3_bucket = os.environ.get("S3_BUCKET")
    if s3_bucket:
        try:
            import boto3
            s3_client = boto3.client(
                "s3",
                endpoint_url=os.environ.get("S3_ENDPOINT_URL"),
                aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
                aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
                region_name=os.environ.get("AWS_REGION", "ap-south-1")
            )
            s3_client.delete_object(Bucket=s3_bucket, Key=f"selfies/{safe_name}")
        except Exception:
            pass
    return True
