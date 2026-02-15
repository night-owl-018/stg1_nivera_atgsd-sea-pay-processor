"""
Signature Variation Module - Makes each signature unique per document
Simulates natural hand-signing variations (position, rotation, scale)
"""

import random
from PIL import Image, ImageDraw, ImageFilter, ImageOps
import io
import base64


def apply_natural_variation(signature_pil, seed=None):
    """
    Apply subtle natural variations to signature to make it look
    hand-signed rather than stamped.
    
    Args:
        signature_pil: PIL Image of the signature
        seed: Optional seed for reproducibility (use document ID)
        
    Returns:
        PIL Image with natural variations applied
    """
    if seed is not None:
        random.seed(seed)
    
    # Create a copy to avoid modifying original
    sig = signature_pil.copy()
    
    # 1. SUBTLE ROTATION (-2° to +2°)
    # Simulates hand angle variation
    rotation = random.uniform(-2.0, 2.0)
    sig = sig.rotate(rotation, expand=True, fillcolor=(255, 255, 255, 0))
    
    # 2. SLIGHT SCALE VARIATION (95% to 105%)
    # Simulates pressure/size variation
    scale = random.uniform(0.95, 1.05)
    new_width = int(sig.width * scale)
    new_height = int(sig.height * scale)
    sig = sig.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # 3. TINY POSITION OFFSET (±2 pixels)
    # Simulates placement variation
    offset_x = random.randint(-2, 2)
    offset_y = random.randint(-2, 2)
    
    # Create new image with padding for offset
    padded = Image.new('RGBA', (sig.width + 8, sig.height + 8), (255, 255, 255, 0))
    padded.paste(sig, (4 + offset_x, 4 + offset_y))
    
    # 4. SUBTLE THICKNESS VARIATION (via slight blur)
    # Simulates ink flow variation
    blur_amount = random.uniform(0, 0.3)
    if blur_amount > 0.1:
        padded = padded.filter(ImageFilter.GaussianBlur(radius=blur_amount))
    
    # 5. SLIGHT OPACITY VARIATION (98% to 100%)
    # Simulates ink consistency
    opacity = random.uniform(0.98, 1.0)
    if opacity < 1.0:
        alpha = padded.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity))
        padded.putalpha(alpha)
    
    return padded


def get_varied_signature_base64(signature_pil, document_identifier):
    """
    Get a signature with natural variations as base64.
    
    Args:
        signature_pil: Original signature PIL Image
        document_identifier: Unique ID for this document (for consistent variation)
        
    Returns:
        Base64 encoded PNG string
    """
    # Use document identifier as seed for consistent variation per document
    seed = hash(str(document_identifier)) % (2**32)
    
    # Apply variations
    varied_sig = apply_natural_variation(signature_pil, seed=seed)
    
    # Convert to base64
    buffer = io.BytesIO()
    varied_sig.save(buffer, format='PNG')
    buffer.seek(0)
    
    return base64.b64encode(buffer.getvalue()).decode('utf-8')


def add_signature_variation_to_config():
    """
    Add this function to config.py to enable signature variations.
    
    Usage in pdf_writer.py:
        from app.core.signature_variation import apply_natural_variation
        
        # When drawing signature:
        sig_image = get_signature_for_location('pg13_certifying_official')
        if sig_image:
            # Apply variation based on document ID
            varied_sig = apply_natural_variation(sig_image, seed=hash(member_name + ship))
            _draw_signature_image(c, varied_sig, x, y, ...)
    """
    pass


# Example usage in pdf_writer.py:
"""
# At top of file:
from app.core.signature_variation import apply_natural_variation

# When drawing signature:
sig_image = get_signature_for_location('pg13_certifying_official')
if sig_image is not None:
    # Create unique variation for THIS document
    document_id = f"{name}_{ship}_{start_date}"  # Unique per document
    varied_sig = apply_natural_variation(sig_image, seed=hash(document_id))
    
    _draw_signature_image(
        c,
        varied_sig,  # Use varied signature instead of original
        sig_left_x - 10,
        sig_bottom_y,
        max_width=170,
        max_height=35
    )
"""
