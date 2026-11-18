"""Pictures in both directions.

Incoming: photos texted to the number (MMS) are downloaded from Twilio, shrunk to a
size Claude reads well, and turned into image blocks for the Claude API.

Outgoing: Claude draws simple pictures (diagrams, charts, sketches) as SVG with the
send_picture tool. They are rendered to PNG here, kept for a day, and served at
/media/<id>.png so Twilio can attach them to the reply text.

Rendering uses resvg (resvg_py), which ships as a ready-made wheel with no system
libraries to install. If the server has no fonts, a small font that comes with
Pillow is used so text in pictures still shows up.
"""

import base64
import glob
import io
import os
import re
import tempfile
from urllib.parse import urlparse

import requests
import resvg_py
from PIL import Image, ImageFont, ImageOps

try:  # iPhone photos can arrive as HEIC; this lets Pillow open them.
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional
    pillow_heif = None

# Refuse absurdly large images before decoding them (a "decompression bomb").
Image.MAX_IMAGE_PIXELS = 60_000_000

MAX_PHOTOS = 5                       # photos read from one text
MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 1568                # Claude reads images up to about this size without shrinking
MAX_SVG_CHARS = 300_000
PICTURE_SIDE = 1200                  # outgoing pictures are scaled so the long side is this many pixels
MAX_PICTURES_PER_REPLY = 2

SEND_PICTURE_TOOL = {
    "name": "send_picture",
    "description": (
        "Text this person a picture you draw, attached to your reply. Write a complete, "
        "self-contained SVG: diagrams, charts and graphs, flowcharts, simple maps, labeled "
        "sketches, math figures, tables, icons. No photos or external images or links."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "svg": {"type": "string", "description": "The full SVG document, starting with <svg"},
            "caption": {"type": "string", "description": "A few words saying what the picture shows"},
        },
        "required": ["svg", "caption"],
    },
}

PICTURE_PROMPT = (
    " You can text pictures with the send_picture tool by writing an SVG. Use it when they ask "
    "for a picture, diagram, chart or drawing, or when one would clearly explain something "
    "better than words. Draw on a white background with a viewBox (about 800 wide), large "
    "readable text (at least 18 units tall), plain sans-serif fonts and basic shapes. You can't "
    "make realistic photos or edit their photos; if asked, say so kindly and offer a simple "
    "drawing instead. After sending a picture, keep your text short and never paste SVG code "
    "into the text."
)

PHOTO_PROMPT = (
    " People can text you photos, which you see as images. If a photo has no words with it, "
    "say briefly what you see and answer what they most likely want to know."
)


# ---------- incoming photos ----------

def _is_twilio_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "twilio.com" or host.endswith(".twilio.com"))


def _download(url, account_sid, auth_token):
    """Fetch one Twilio media file. Twilio's media links need the account's login unless
    that setting was turned off. The login is only ever sent to twilio.com (requests drops
    it when Twilio redirects to its file storage)."""
    if not _is_twilio_url(url):
        raise ValueError("not a Twilio media link")
    auth = (account_sid, auth_token) if account_sid and auth_token else None
    with requests.get(url, auth=auth, timeout=20, stream=True) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data += chunk
            if len(data) > MAX_DOWNLOAD_BYTES:
                raise ValueError("file too big")
        return bytes(data)


def to_claude_jpeg(data):
    """Open any image Pillow can read and return it as a reasonably sized JPEG."""
    img = Image.open(io.BytesIO(data))
    img.seek(0)  # first frame of a GIF
    img = ImageOps.exif_transpose(img)  # phones store rotation separately
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGB", img.size, "white")
        background.paste(img, mask=img.getchannel("A"))
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def incoming_media(form, account_sid, auth_token, log=None):
    """Read the media on a Twilio text. Returns (image_blocks, skipped) where skipped is a
    list of short descriptions of files that couldn't be used (videos, audio, broken)."""
    try:
        count = int(form.get("NumMedia", "0") or 0)
    except ValueError:
        count = 0
    blocks, skipped = [], []
    for i in range(min(count, 10)):
        url = form.get(f"MediaUrl{i}", "")
        kind = (form.get(f"MediaContentType{i}", "") or "").lower()
        if not kind.startswith("image/"):
            skipped.append(_describe(kind))
            continue
        if len(blocks) >= MAX_PHOTOS:
            skipped.append("an extra photo (I read up to %d at a time)" % MAX_PHOTOS)
            continue
        try:
            jpeg = to_claude_jpeg(_download(url, account_sid, auth_token))
        except Exception as e:
            if log:
                log("Couldn't read photo %d (%s): %s", i, kind, type(e).__name__)
            skipped.append("a photo I couldn't open")
            continue
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg",
            "data": base64.standard_b64encode(jpeg).decode("ascii")}})
    return blocks, skipped


def _describe(kind):
    if kind.startswith("video/"):
        return "a video"
    if kind.startswith("audio/"):
        return "an audio clip"
    if "vcard" in kind:
        return "a contact card"
    if kind == "application/pdf":
        return "a PDF"
    return "a file"


def has_media(form):
    try:
        return int(form.get("NumMedia", "0") or 0) > 0
    except ValueError:
        return False


# ---------- outgoing pictures ----------

_FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
              os.path.expanduser("~/.local/share/fonts")]
_font_options = None


def _fonts():
    """Use the server's fonts if it has any; otherwise the small font bundled with Pillow."""
    global _font_options
    if _font_options is None:
        has_system_fonts = any(glob.glob(os.path.join(d, "**", "*.[ot]tf"), recursive=True)
                               for d in _FONT_DIRS if os.path.isdir(d))
        if has_system_fonts:
            _font_options = {}
            # Prefer a clean sans-serif font when the server has one (the built-in default is serif).
            files = " ".join(os.path.basename(f) for d in _FONT_DIRS if os.path.isdir(d)
                             for f in glob.glob(os.path.join(d, "**", "*.[ot]tf"), recursive=True))
            for family, file_start in (("Liberation Sans", "LiberationSans-"), ("DejaVu Sans", "DejaVuSans."),
                                       ("Noto Sans", "NotoSans-"), ("FreeSans", "FreeSans.")):
                if file_start in files:
                    _font_options = {"font_family": family, "sans_serif_family": family}
                    break
        else:
            path = os.path.join(tempfile.gettempdir(), "sms-claude-fallback.ttf")
            with open(path, "wb") as f:
                f.write(ImageFont.load_default(size=20).font_bytes)
            _font_options = {"skip_system_fonts": True, "font_files": [path]}
            for option in ("font_family", "sans_serif_family", "serif_family", "monospace_family",
                           "cursive_family", "fantasy_family"):
                _font_options[option] = "Aileron"
    return _font_options


# Anything that could make the renderer read a file or reach the network is refused.
_UNSAFE_SVG = [
    re.compile(r"<!DOCTYPE|<!ENTITY", re.I),
    re.compile(r"<\s*(script|foreignObject|iframe)\b", re.I),
    re.compile(r"(?:href|src)\s*=\s*[\"'](?!\s*(?:#|data:image/))", re.I),
    re.compile(r"url\((?![\s\"']*(?:#|data:image/))", re.I),
    re.compile(r"@import", re.I),
]


def render_svg(svg):
    """Turn Claude's SVG into PNG bytes. Raises ValueError with a short reason Claude can fix."""
    svg = (svg or "").strip()
    if svg.startswith("```"):
        svg = re.sub(r"^```\w*\s*|\s*```$", "", svg)
    if len(svg) > MAX_SVG_CHARS:
        raise ValueError("The SVG is too long. Make it simpler.")
    for pattern in _UNSAFE_SVG:
        if pattern.search(svg):
            raise ValueError("The SVG uses scripts, external files or links, which aren't allowed. "
                             "Use only shapes, paths and text.")
    start = svg.find("<svg")
    if start < 0:
        raise ValueError("That isn't an SVG. Send a full document starting with <svg.")
    svg = svg[start:]
    if "xmlns=" not in svg[:500]:
        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    try:
        # A width and height together scale the picture to fit inside that box.
        png = resvg_py.svg_to_bytes(svg_string=svg, background="white",
                                    width=PICTURE_SIDE, height=PICTURE_SIDE, **_fonts())
    except BaseException as e:  # the renderer raises ValueError, or a panic for very broken input
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        raise ValueError(f"The SVG couldn't be drawn ({str(e)[:120]}). Fix it and try again.")
    if not png or len(png) < 100:
        raise ValueError("The picture came out empty. Check the SVG's size and viewBox.")
    return bytes(png)
