import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image


def image_file(name="a.png", fmt="PNG", size=(20, 10)):
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 100, 100)).save(buf, fmt)
    ctype = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
    return SimpleUploadedFile(name, buf.getvalue(), content_type=ctype[fmt])
