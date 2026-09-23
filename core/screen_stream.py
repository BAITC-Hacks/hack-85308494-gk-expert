import io
import base64
import time
from typing import Dict, List, Optional
from PIL import Image

try:
    import mss
except ImportError:
    mss = None


class ScreenStreamer:
    """
    OBS-style screen and window capture provider.
    Allows capturing display frames as base64 JPEG images for live GUI streaming.
    """

    def __init__(self):
        self.sct = mss.mss() if mss else None

    def get_monitors(self) -> List[Dict]:
        """List available monitors for capture."""
        if not self.sct:
            return [{"id": 0, "name": "Основной дисплей"}]
        monitors = []
        for i, m in enumerate(self.sct.monitors[1:], start=1):
            monitors.append({
                "id": i,
                "name": f"Монитор {i} ({m['width']}x{m['height']})",
                "width": m["width"],
                "height": m["height"]
            })
        if not monitors:
            monitors.append({"id": 0, "name": "Основной экран", "width": 1920, "height": 1080})
        return monitors

    def capture_frame_base64(self, monitor_index: int = 1, quality: int = 40, max_width: int = 640) -> Optional[str]:
        """
        Capture current screen frame and return as base64 JPEG data URL for smooth OBS preview.
        """
        if not self.sct:
            return None
        try:
            monitors = self.sct.monitors
            target_monitor = monitors[monitor_index] if monitor_index < len(monitors) else monitors[0]
            sct_img = self.sct.grab(target_monitor)

            # Convert to PIL Image
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

            # Scale down for ultra-fast preview streaming
            w, h = img.size
            if w > max_width:
                new_h = int(h * (max_width / w))
                img = img.resize((max_width, new_h), Image.Resampling.BILINEAR)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")
            return f"data:image/jpeg;base64,{b64_str}"
        except Exception as e:
            # Fallback if grab failed
            return None
