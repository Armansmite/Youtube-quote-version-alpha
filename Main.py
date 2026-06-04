#!/usr/bin/env python3
"""
v2_hook_shorts_updated.py

V2: 7‑second shorts with enhanced hook intro.
- Hook font: Anton → Impact → Arial Black → bold fallback, 85 pt.
- Hook text UPPERCASE.
- Semi‑transparent black rectangle behind hook text (80 % width, 25 % height,
  centered, 50 % opacity).
- Hook text slightly above absolute centre.
- Main quote unchanged (Garamond, white with black stroke).
- Schedules at 00:00 UTC and 17:00 UTC.
- Authentication token stored in token.pickle for automatic re‑use.
"""

import json
import os
import sys
import random
import glob
import logging
import re
import pickle
import time
import warnings
from datetime import datetime, timedelta

os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["ALSA_CARD"] = "dummy"
warnings.filterwarnings("ignore", category=SyntaxWarning)

from PIL import Image, ImageDraw, ImageFont
import numpy as np
from moviepy.editor import (
    ImageClip,
    AudioFileClip,
    concatenate_videoclips,
)

# YouTube API
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
QUOTE_FILE = "quote.txt"
PROCESSED_FILE = "processed.txt"
IMAGE_DIR = "images"
MUSIC_DIR = "music"
OUTPUT_PREFIX = "output"
OUTPUT_EXT = ".mp4"

VIDEO_SIZE = (1080, 1920)          # 9:16 vertical
FPS = 30
TOTAL_DURATION = 7                 # seconds
HOOK_DURATION = 2                  # first 2 seconds
MAIN_DURATION = 5                  # remaining 5 seconds
MAX_QUOTE_LEN = 50

# Main text fonts (unchanged)
FONT_FILE = "Garamond.ttf"
FONT_ITALIC_FILE = "Garamond-Italic.ttf"

# Hook font stack (85 pt)
HOOK_FONT_SIZE = 85
HOOK_FONTS = [
    "Anton.ttf",                         # local file if present
    "/usr/share/fonts/truetype/anton/Anton-Regular.ttf",
    "impact.ttf",
    "/usr/share/fonts/truetype/msttcorefonts/Impact.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "Arial Black.ttf",
    "/usr/share/fonts/truetype/arial-black/Arial_Black.ttf",
]

# Semi‑transparent rectangle settings
RECT_WIDTH_PCT = 0.8                  # 80 % of video width
RECT_HEIGHT_PCT = 0.25                # 25 % of video height
RECT_OPACITY = 128                    # 0 = transparent, 255 = opaque (50 %)
HOOK_TEXT_Y_OFFSET = -20              # move up from absolute centre

TEXT_COLOR = (255, 255, 255)          # white
STROKE_COLOR = (0, 0, 0)              # black
STROKE_WIDTH = 4
MARGIN = 100

# Main quote layout (unchanged)
QUOTE_Y = 700
WRITER_Y = 850
WRITER_FONT_RATIO = 0.6

# YouTube settings
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.pickle"           # auto‑created after first login
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
CATEGORY_ID = "22"
SLOT_HOURS = [(0, 0), (17, 0)]        # 00:00 UTC and 17:00 UTC
BASE_TAGS = ["shorts", "quotes", "motivation", "wisdom"]

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------
# Processed lines tracking
# ----------------------------------------------------------------------
def load_processed():
    if not os.path.exists(PROCESSED_FILE):
        return set()
    with open(PROCESSED_FILE, "r") as f:
        return {int(line) for line in f.read().splitlines() if line.strip().isdigit()}

def mark_processed(line_num):
    with open(PROCESSED_FILE, "a") as f:
        f.write(f"{line_num}\n")

def get_next_output_index():
    existing = glob.glob(f"{OUTPUT_PREFIX}_*.mp4")
    max_idx = 0
    for fname in existing:
        m = re.match(rf"{OUTPUT_PREFIX}_(\d+){OUTPUT_EXT}", os.path.basename(fname))
        if m:
            idx = int(m.group(1))
            if idx > max_idx:
                max_idx = idx
    return max_idx + 1

# ----------------------------------------------------------------------
# Media discovery
# ----------------------------------------------------------------------
def find_hook_images():
    folder = IMAGE_DIR
    if not os.path.isdir(folder):
        return []
    patterns = [
        os.path.join(folder, "hook_*.jpg"),
        os.path.join(folder, "hook_*.jpeg"),
        os.path.join(folder, "hook_*.png"),
        os.path.join(folder, "hook_*.JPG"),
        os.path.join(folder, "hook_*.JPEG"),
        os.path.join(folder, "hook_*.PNG"),
    ]
    images = []
    for pat in patterns:
        images.extend(glob.glob(pat))
    return images

def find_main_images(subject):
    subject_folder = os.path.join(IMAGE_DIR, subject)
    if os.path.isdir(subject_folder):
        patterns = [
            os.path.join(subject_folder, "*.jpg"),
            os.path.join(subject_folder, "*.jpeg"),
            os.path.join(subject_folder, "*.png"),
        ]
        images = []
        for pat in patterns:
            images.extend(glob.glob(pat))
        return images
    else:
        all_images = glob.glob(os.path.join(IMAGE_DIR, "*.*"))
        main_images = []
        for img in all_images:
            base = os.path.basename(img)
            if not base.lower().startswith("hook_"):
                if os.path.splitext(img)[1].lower() in (".jpg", ".jpeg", ".png"):
                    main_images.append(img)
        return main_images

def find_music_files():
    folder = MUSIC_DIR
    if not os.path.isdir(folder):
        return []
    patterns = [
        os.path.join(folder, "*.mp3"),
        os.path.join(folder, "*.m4a"),
    ]
    music = []
    for pat in patterns:
        music.extend(glob.glob(pat))
    return music

# ----------------------------------------------------------------------
# Font helpers
# ----------------------------------------------------------------------
def load_main_font(size, italic=False):
    """Load Garamond (or fallback) for main quote."""
    if italic and os.path.isfile(FONT_ITALIC_FILE):
        try:
            return ImageFont.truetype(FONT_ITALIC_FILE, size)
        except Exception:
            pass
    if not italic and os.path.isfile(FONT_FILE):
        try:
            return ImageFont.truetype(FONT_FILE, size)
        except Exception:
            pass
    # Fallback system serif fonts
    system_fonts = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        "C:/Windows/Fonts/times.ttf",
        "C:/Windows/Fonts/georgia.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "/System/Library/Fonts/Georgia.ttf",
    ]
    for path in system_fonts:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("No serif font found, using Pillow default.")
    return ImageFont.load_default()

def load_hook_font(size):
    """
    Load the first available font from the HOOK_FONTS list.
    Fall back to a bold system font, then default.
    """
    for font_path in HOOK_FONTS:
        if os.path.isfile(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    # Try any available bold font on the system
    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",   # Arial Bold
    ]
    for path in bold_candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    logger.warning("No bold hook font found, using Pillow default.")
    return ImageFont.load_default()

# ----------------------------------------------------------------------
# Text sizing / drawing for main quote (unchanged)
# ----------------------------------------------------------------------
def split_quote_two_lines(quote):
    words = quote.split()
    if len(words) <= 1:
        return [quote]
    best_split = None
    for i in range(len(words) - 1, 0, -1):
        first = " ".join(words[:i])
        second = " ".join(words[i:])
        if len(first) >= len(second):
            if best_split is None or (len(first) > len(second) and len(best_split[0]) == len(best_split[1])):
                best_split = (first, second)
            if len(first) > len(second):
                break
    if best_split is None:
        return [quote]
    return list(best_split)

def best_font_size(lines, max_width, italic=False):
    """Auto‑size for the given font stack."""
    font_path = None
    if italic and os.path.isfile(FONT_ITALIC_FILE):
        font_path = FONT_ITALIC_FILE
    elif not italic and os.path.isfile(FONT_FILE):
        font_path = FONT_FILE

    low, high = 10, 200
    best = low
    while low <= high:
        mid = (low + high) // 2
        try:
            if font_path:
                font = ImageFont.truetype(font_path, mid)
            else:
                font = load_main_font(mid, italic=False)
        except Exception:
            font = load_main_font(mid, italic=False)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        fits = True
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            if (bbox[2] - bbox[0]) > max_width:
                fits = False
                break
        if fits:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best

def draw_text_with_stroke(draw, text, xy, font, text_color, stroke_color, stroke_width):
    x, y = xy
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx != 0 or dy != 0:
                draw.text((x + dx, y + dy), text, font=font, fill=stroke_color)
    draw.text((x, y), text, font=font, fill=text_color)

# ----------------------------------------------------------------------
# Build hook frame with rectangle + uppercase text
# ----------------------------------------------------------------------
def build_hook_frame(image, hook_text):
    """Draw semi‑transparent black rectangle and uppercase hook text."""
    img = image.copy().convert("RGBA")
    draw = ImageDraw.Draw(img)
    W, H = VIDEO_SIZE

    # Uppercase text
    hook_text = hook_text.upper()

    # Load hook font (85 pt, will auto‑shrink if needed)
    max_font_size = HOOK_FONT_SIZE
    # Calculate how much space the rectangle provides
    rect_w = int(W * RECT_WIDTH_PCT)
    rect_h = int(H * RECT_HEIGHT_PCT)
    # Leave a small inner padding
    pad = 20
    inner_w = rect_w - 2 * pad
    # Auto‑size text to fit within inner_w
    # Binary search for font size, capped at max_font_size
    low, high = 10, max_font_size
    best_size = low
    while low <= high:
        mid = (low + high) // 2
        font = load_hook_font(mid)
        bbox = draw.textbbox((0, 0), hook_text, font=font)
        w = bbox[2] - bbox[0]
        if w <= inner_w:
            best_size = mid
            low = mid + 1
        else:
            high = mid - 1
    hook_font = load_hook_font(best_size)

    # Rectangle (centred)
    rect_x0 = (W - rect_w) // 2
    rect_y0 = (H - rect_h) // 2
    rect_x1 = rect_x0 + rect_w
    rect_y1 = rect_y0 + rect_h
    # Draw rectangle with semi‑transparency
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        [rect_x0, rect_y0, rect_x1, rect_y1],
        fill=(0, 0, 0, RECT_OPACITY),
    )
    img = Image.alpha_composite(img, overlay)

    # Text position: slightly above absolute centre
    text_bbox = draw.textbbox((0, 0), hook_text, font=hook_font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    # Absolute centre
    center_x, center_y = W // 2, H // 2
    # Target Y = centre_y + offset (negative = up)
    target_y = center_y + HOOK_TEXT_Y_OFFSET
    # Place text such that its vertical centre is at target_y
    text_x = center_x - text_w // 2
    text_y = target_y - text_h // 2

    # Draw text with stroke on the composited image
    draw = ImageDraw.Draw(img)
    draw_text_with_stroke(draw, hook_text, (text_x, text_y),
                          hook_font, TEXT_COLOR, STROKE_COLOR, STROKE_WIDTH)

    # Convert back to RGB for MoviePy
    return img.convert("RGB")

# ----------------------------------------------------------------------
# Build main quote frame (unchanged)
# ----------------------------------------------------------------------
def build_main_frame(image, quote, writer):
    img = image.copy()
    draw = ImageDraw.Draw(img)
    max_width = VIDEO_SIZE[0] - 2 * MARGIN

    lines = split_quote_two_lines(quote)
    quote_size = best_font_size(lines, max_width, italic=False)
    quote_font = load_main_font(quote_size, italic=False)

    writer_size = max(int(quote_size * WRITER_FONT_RATIO), 12)
    writer_font = load_main_font(writer_size, italic=True)

    line_height = quote_font.getbbox("Ag")[3] - quote_font.getbbox("Ag")[1]
    total_height = line_height * len(lines) + (len(lines) - 1) * 10
    start_y = QUOTE_Y - total_height // 2

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=quote_font)
        line_w = bbox[2] - bbox[0]
        line_x = (VIDEO_SIZE[0] - line_w) // 2
        line_y = start_y + i * (line_height + 10)
        draw_text_with_stroke(draw, line, (line_x, line_y),
                              quote_font, TEXT_COLOR, STROKE_COLOR, STROKE_WIDTH)

    writer_text = f"– {writer}"
    writer_bbox = draw.textbbox((0, 0), writer_text, font=writer_font)
    writer_w = writer_bbox[2] - writer_bbox[0]
    writer_x = (VIDEO_SIZE[0] - writer_w) // 2
    writer_y = start_y + total_height + 30
    draw_text_with_stroke(draw, writer_text, (writer_x, writer_y),
                          writer_font, TEXT_COLOR, STROKE_COLOR, STROKE_WIDTH)
    return img

# ----------------------------------------------------------------------
# YouTube authentication (unchanged)
# ----------------------------------------------------------------------
def get_authenticated_service():
    credentials = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as token:
            credentials = pickle.load(token)
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            flow.redirect_uri = "http://localhost:8080"
            auth_url, _ = flow.authorization_url(prompt="consent")

            print("\n" + "="*60)
            print("Please visit this URL to authorize the application:")
            print(auth_url)
            print("="*60)
            print("\nAfter authorizing, you will be redirected to a page that fails to load.")
            print("Look at the address bar – the URL contains a 'code=...' parameter.")
            print("\nCopy the FULL URL and save it to a file named 'auth_code.txt'")
            print("in the same directory as this script.")
            print("Waiting for auth_code.txt ... (Ctrl+C to cancel)")

            auth_file = "auth_code.txt"
            if os.path.exists(auth_file):
                os.remove(auth_file)

            waited = 0
            while not os.path.exists(auth_file) and waited < 300:
                time.sleep(2)
                waited += 2
                if waited % 10 == 0:
                    print(f"  Still waiting... ({waited}s elapsed)")

            if not os.path.exists(auth_file):
                raise Exception("Timed out waiting for auth_code.txt. Please try again.")

            with open(auth_file, "r") as f:
                code = f.read().strip()

            os.remove(auth_file)

            if "code=" in code:
                code = code.split("code=")[1].split("&")[0]

            print(f"\nCode received (length: {len(code)}). Exchanging for token...")
            flow.fetch_token(code=code)
            credentials = flow.credentials
            print("Token obtained successfully!")

        with open(TOKEN_FILE, "wb") as token:
            pickle.dump(credentials, token)
    return build("youtube", "v3", credentials=credentials)

# ----------------------------------------------------------------------
# Slot management (unchanged)
# ----------------------------------------------------------------------
def get_upcoming_scheduled_times():
    youtube = get_authenticated_service()
    channel_response = youtube.channels().list(part="contentDetails", mine=True).execute()
    uploads_playlist_id = channel_response["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]

    video_ids = []
    next_page_token = None
    while True:
        playlist_response = youtube.playlistItems().list(
            part="snippet",
            playlistId=uploads_playlist_id,
            maxResults=50,
            pageToken=next_page_token,
        ).execute()
        video_ids.extend([item["snippet"]["resourceId"]["videoId"] for item in playlist_response["items"]])
        next_page_token = playlist_response.get("nextPageToken")
        if not next_page_token:
            break

    if not video_ids:
        return set()

    scheduled_times = set()
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        vid_response = youtube.videos().list(part="status", id=",".join(batch)).execute()
        for item in vid_response["items"]:
            status = item["status"]
            if status.get("privacyStatus") == "private" and "publishAt" in status:
                dt_str = status["publishAt"]
                dt = datetime.strptime(dt_str.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
                if dt > datetime.utcnow():
                    scheduled_times.add(dt)
    return scheduled_times

def next_free_slot(occupied_set):
    now = datetime.utcnow()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    while True:
        for h, m in SLOT_HOURS:
            candidate = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate > now and candidate not in occupied_set:
                return candidate
        day += timedelta(days=1)

# ----------------------------------------------------------------------
# Upload & thumbnail (unchanged)
# ----------------------------------------------------------------------
def upload_video(video_path, title, description, tags, category_id, publish_at):
    youtube = get_authenticated_service()
    privacy_status = "private"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    publish_str = publish_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    body["status"]["publishAt"] = publish_str

    media = MediaFileUpload(video_path, mimetype="video/mp4", resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info(f"Upload progress: {int(status.progress() * 100)}%")
    video_id = response["id"]
    logger.info(f"Video uploaded. ID: {video_id}")
    logger.info(f"Scheduled for: {publish_str}")

    thumb_path = video_path.replace(".mp4", "_thumb.jpg")
    if os.path.exists(thumb_path):
        try:
            thumb_media = MediaFileUpload(thumb_path, mimetype="image/jpeg")
            youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
            logger.info("Thumbnail set.")
        except Exception as e:
            logger.warning(f"Could not set custom thumbnail: {e}")
    return video_id

# ----------------------------------------------------------------------
# Video creation
# ----------------------------------------------------------------------
def create_hook_clip(hook_image_path, hook_text):
    img = Image.open(hook_image_path).convert("RGB")
    img = img.resize(VIDEO_SIZE, Image.LANCZOS)
    frame = build_hook_frame(img, hook_text)
    array = np.array(frame)
    return ImageClip(array).set_duration(HOOK_DURATION)

def create_main_clip(main_image_path, quote, writer):
    img = Image.open(main_image_path).convert("RGB")
    img = img.resize(VIDEO_SIZE, Image.LANCZOS)
    frame = build_main_frame(img, quote, writer)
    array = np.array(frame)
    return ImageClip(array).set_duration(MAIN_DURATION)

def create_video(hook_image, main_image, music_path, hook_text, quote, writer, output_path):
    hook_clip = create_hook_clip(hook_image, hook_text)
    main_clip = create_main_clip(main_image, quote, writer)

    final = concatenate_videoclips([hook_clip, main_clip], method="compose")
    audio = AudioFileClip(music_path)
    if audio.duration > TOTAL_DURATION:
        audio = audio.subclip(0, TOTAL_DURATION)
    final = final.set_audio(audio)

    logger.info(f"Writing video to {output_path} ...")
    final.write_videofile(
        output_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile="temp-audio.m4a",
        remove_temp=True,
        verbose=False,
        logger=None,
    )
    final.close()
    audio.close()

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    processed = load_processed()
    logger.info(f"Already processed lines: {sorted(processed)}")

    if not os.path.exists(QUOTE_FILE):
        logger.error(f"Quote file {QUOTE_FILE} not found.")
        sys.exit(1)

    hook_images = find_hook_images()
    if not hook_images:
        logger.error("No hook images found (images/hook_*). Cannot create videos.")
        sys.exit(1)

    music_files = find_music_files()
    if not music_files:
        logger.error("No music files found in music/.")
        sys.exit(1)

    with open(QUOTE_FILE, "r") as f:
        lines = f.readlines()

    logger.info("Fetching existing scheduled videos...")
    occupied = get_upcoming_scheduled_times()
    logger.info(f"Found {len(occupied)} already scheduled slots.")
    next_slot = next_free_slot(occupied)
    logger.info(f"First free slot: {next_slot.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    for line_idx, raw_line in enumerate(lines, start=1):
        if line_idx in processed:
            continue

        line = raw_line.strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            logger.error(f"Line {line_idx}: invalid JSON, skipping.")
            continue

        if not all(k in data for k in ("qoute", "writer", "subject", "hook")):
            logger.error(f"Line {line_idx}: missing keys (need qoute, writer, subject, hook). Skipping.")
            continue

        quote = data["qoute"]
        writer = data["writer"]
        subject = data["subject"]
        hook = data["hook"]

        if not hook:
            logger.info(f"Line {line_idx}: empty hook, marking processed.")
            mark_processed(line_idx)
            continue

        if len(quote) > MAX_QUOTE_LEN:
            logger.info(f"Line {line_idx}: quote too long ({len(quote)} chars), marking processed.")
            mark_processed(line_idx)
            continue

        hook_img = random.choice(hook_images)
        main_imgs = find_main_images(subject)
        if not main_imgs:
            logger.error(f"Line {line_idx}: no main images for subject '{subject}'. Skipping.")
            continue
        main_img = random.choice(main_imgs)
        music = random.choice(music_files)

        next_idx = get_next_output_index()
        video_name = f"{OUTPUT_PREFIX}_{next_idx:03d}{OUTPUT_EXT}"
        video_path = os.path.join(os.getcwd(), video_name)

        try:
            create_video(hook_img, main_img, music, hook, quote, writer, video_path)
        except Exception as e:
            logger.error(f"Line {line_idx}: video creation failed – {e}. Skipping.")
            continue

        try:
            title = f"{quote} – {writer}"[:100]
            description = (
                f"{quote} – {writer}\n\n"
                f"✨ Topic: {subject}\n"
                f"🔖 #quotes #{subject} #motivation #wisdom\n\n"
                f"🎵 Music from YouTube Audio Library\n"
                f"📌 Subscribe for daily quotes"
            )
            tags = list(set(BASE_TAGS + [subject, writer]))

            upload_video(video_path, title, description, tags, CATEGORY_ID, publish_at=next_slot)

            mark_processed(line_idx)
            occupied.add(next_slot)
            next_slot = next_free_slot(occupied)
            logger.info(f"Line {line_idx}: scheduled at {next_slot.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        except Exception as e:
            logger.error(f"Line {line_idx}: upload failed – {e}. Stopping.")
            break

    logger.info("Script finished.")

if __name__ == "__main__":
    main()
