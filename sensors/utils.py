import qrcode
import qrcode.image.svg
from io import BytesIO


def generate_qr_png(url: str) -> bytes:
    """
    Generates a QR code PNG for the given URL.
    Returns raw bytes — ready to serve as an HTTP response.
    """
    qr = qrcode.QRCode(
        version           = 1,
        error_correction  = qrcode.constants.ERROR_CORRECT_H,  # high error correction
        box_size          = 10,
        border            = 4,
    )
    qr.add_data(url)
    qr.make(fit=True)

    img    = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return buffer.getvalue()