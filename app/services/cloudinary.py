from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from rest_framework import serializers

MAX_WIDTH = 2400
MAX_HEIGHT = 1600
QUALITY = 82
THUMBNAIL_WIDTH = 600


def _get_client():
    try:
        import app.services.cloudinary as cloudinary
        import cloudinary.uploader
    except ImportError as exc:
        raise serializers.ValidationError(
            {"image": "Cloudinary dependency is missing. Install requirements and rebuild the container."}
        ) from exc

    if not all(
        [
            settings.CLOUDINARY_CLOUD_NAME,
            settings.CLOUDINARY_API_KEY,
            settings.CLOUDINARY_API_SECRET,
        ]
    ):
        raise serializers.ValidationError(
            {"image": "Cloudinary configuration is incomplete."}
        )

    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True,
    )
    return cloudinary.uploader


def upload_image(file_obj, folder=None, public_id=None):
    uploader = _get_client()
    upload_folder = folder or settings.CLOUDINARY_FOLDER
    options = {
        "folder": upload_folder,
        "resource_type": "image",
    }
    if public_id:
        options["public_id"] = public_id
        options["overwrite"] = True
    result = uploader.upload(_prepare_upload_file(file_obj), **options)
    return {
        "url": result.get("secure_url") or result.get("url"),
        "public_id": result.get("public_id"),
    }


def upload_file(file_obj, folder=None, public_id=None, resource_type="auto"):
    uploader = _get_client()
    upload_folder = folder or settings.CLOUDINARY_FOLDER
    options = {
        "folder": upload_folder,
        "resource_type": resource_type,
    }
    if public_id:
        options["public_id"] = public_id
        options["overwrite"] = True
    result = uploader.upload(file_obj, **options)
    return {
        "url": result.get("secure_url") or result.get("url"),
        "public_id": result.get("public_id"),
    }


def _prepare_upload_file(file_obj):
    try:
        original_position = file_obj.tell()
    except (AttributeError, OSError):
        original_position = None

    try:
        file_obj.seek(0)
        original_bytes = file_obj.read()
    except (AttributeError, OSError):
        return file_obj
    finally:
        if original_position is not None:
            file_obj.seek(original_position)

    if not original_bytes:
        return file_obj

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))

    try:
        image = Image.open(BytesIO(original_bytes))
        if getattr(image, "is_animated", False):
            return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))
        image = ImageOps.exif_transpose(image)
        image.load()
    except (UnidentifiedImageError, OSError):
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))

    original_size = image.size
    image.thumbnail((MAX_WIDTH, MAX_HEIGHT), Image.Resampling.LANCZOS)
    resized = image.size != original_size

    candidates = []
    webp_bytes = _encode_image(image, "WEBP", quality=QUALITY, method=6)
    if webp_bytes:
        candidates.append(("image.webp", webp_bytes))

    if image.mode not in ("RGBA", "LA", "P"):
        jpeg_bytes = _encode_image(
            image.convert("RGB"),
            "JPEG",
            quality=QUALITY,
            optimize=True,
            progressive=True,
        )
        if jpeg_bytes:
            candidates.append(("image.jpg", jpeg_bytes))

    if not candidates:
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))

    candidate_name, candidate_bytes = min(candidates, key=lambda item: len(item[1]))
    if not resized and len(candidate_bytes) >= len(original_bytes):
        return _bytes_upload_file(original_bytes, getattr(file_obj, "name", "image"))
    return _bytes_upload_file(candidate_bytes, candidate_name)


def _encode_image(image, image_format, **options):
    output = BytesIO()
    try:
        image.save(output, format=image_format, **options)
    except OSError:
        return None
    return output.getvalue()


def _bytes_upload_file(content, name):
    upload_file = BytesIO(content)
    upload_file.name = name
    upload_file.seek(0)
    return upload_file


def delete_image(public_id=None, image_url=None):
    resolved_public_id = public_id or extract_public_id(image_url)
    if not resolved_public_id:
        return {"result": "skipped"}

    uploader = _get_client()
    result = uploader.destroy(resolved_public_id, resource_type="image")
    return {"result": result.get("result"), "public_id": resolved_public_id}


def delete_file(public_id=None, file_url=None):
    resolved_public_id = public_id or extract_public_id(file_url)
    if not resolved_public_id:
        return {"result": "skipped"}

    uploader = _get_client()
    resource_type = extract_resource_type(file_url) or "image"
    result = uploader.destroy(resolved_public_id, resource_type=resource_type)
    return {
        "result": result.get("result"),
        "public_id": resolved_public_id,
        "resource_type": resource_type,
    }


def extract_resource_type(file_url):
    if not file_url:
        return None

    parsed = urlparse(file_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    for resource_type in ("image", "raw", "video"):
        if resource_type in path_parts and "upload" in path_parts:
            return resource_type
    return None


def extract_public_id(image_url):
    if not image_url:
        return None

    parsed = urlparse(image_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    try:
        upload_index = path_parts.index("upload")
    except ValueError:
        return None

    public_parts = path_parts[upload_index + 1 :]
    if public_parts and public_parts[0].startswith("v"):
        public_parts = public_parts[1:]
    if not public_parts:
        return None

    public_parts[-1] = str(Path(public_parts[-1]).with_suffix(""))
    return "/".join(public_parts)


def cloudinary_thumbnail_url(image_url, width=THUMBNAIL_WIDTH):
    if not image_url:
        return image_url

    parsed = urlparse(image_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    try:
        upload_index = path_parts.index("upload")
    except ValueError:
        return image_url

    transformation = f"c_scale,w_{int(width)}"
    transformed_parts = path_parts[: upload_index + 1] + [transformation] + path_parts[upload_index + 1 :]
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            "/" + "/".join(transformed_parts),
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
