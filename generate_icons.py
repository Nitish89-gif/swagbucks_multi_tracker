import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

static_dir = Path("static")
static_dir.mkdir(exist_ok=True)

def generate_icon(size: int, filename: str):
    # Dark modern gradient-like background
    img = Image.new("RGBA", (size, size), (9, 13, 22, 255))
    draw = ImageDraw.Draw(img)
    
    # Outer rounded rect / glow border
    margin = int(size * 0.05)
    draw.rounded_rectangle(
        [(margin, margin), (size - margin, size - margin)],
        radius=int(size * 0.22),
        fill=(17, 24, 39, 255),
        outline=(59, 130, 246, 255),
        width=int(size * 0.03)
    )
    
    # Circle badge in center
    center_r = int(size * 0.32)
    center = (size // 2, size // 2)
    draw.ellipse(
        [(center[0] - center_r, center[1] - center_r),
         (center[0] + center_r, center[1] + center_r)],
        fill=(37, 99, 235, 255),
        outline=(16, 185, 129, 255),
        width=int(size * 0.02)
    )
    
    # Draw "SB" text or graphic
    try:
        font = ImageFont.truetype("arial.ttf", int(size * 0.30))
    except Exception:
        font = ImageFont.load_default()
        
    text = "SB"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    text_x = (size - text_w) // 2
    text_y = (size - text_h) // 2 - int(size * 0.02)
    draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)
    
    out_path = static_dir / filename
    img.save(out_path, "PNG")
    print(f"Generated {out_path} ({size}x{size})")

generate_icon(180, "apple-touch-icon.png")
generate_icon(192, "icon-192.png")
generate_icon(512, "icon-512.png")
