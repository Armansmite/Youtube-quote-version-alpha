#!/usr/bin/env python3
"""
v3_roman_shorts_upload.py

Creates 7‑second YouTube Shorts (9:16 vertical) from Roman‑theme quotes,
schedules them at 05:00 or 17:00 UTC (one video per slot per day).

- Random background image (already 9:16) from images/
- Random music from music/ (first 7 seconds used)
- Quote & writer text overlay (Garamond/serif, white with black stroke)
- Smooth fade from black over 1 second
- Tracks processed lines in processed.txt
- Stops on upload failure
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
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from moviepy.editor import ImageClip, AudioFileClip

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

VIDEO_SIZE = (1080, 1920)   # 9:16 vertical
FPS = 30
TOTAL_DURATION = 7          # seconds
FADE_DURATION = 1           # seconds (fade from black)
MAX_QUOTE_LEN = 50

FONT_FILE = "Garamond.ttf"

TEXT_COLOR = (255, 255, 255)  # white
STROKE_COLOR = (0, 0, 0)      # black
STROKE_WIDTH = 4
MARGIN = 100

QUOTE_Y = 700
WRITER_Y = 850
WRITER_FONT_RATIO = 0.6

# YouTube settings
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.pickle"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]
CATEGORY_ID = "22"          # People & Blogs
# The two daily upload slots (UTC)
SLOT_HOURS = [(5, 0), (17, 0)]   # (hour, minute)
BASE_TAGS = ["shorts", "quotes", "motivation", "wisdom", "roman"]

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
def get_random_image():
    if not os.path.isdir(IMAGE_DIR):
        return None
    patterns = [
        os.path.join(IMAGE_DIR, "*.jpg"),
        os.path.join(IMAGE_DIR, "*.jpeg"),
        os.path.join(IMAGE_DIR, "*.png"),
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    return random.choice(candidates) if candidates else None

def get_random_music():
    if not os.path.isdir(MUSIC_DIR):
        return None
    patterns = [
        os.path.join(MUSIC_DIR, "*.mp3"),
        os.path.join(MUSIC_DIR, "*.m4a"),
    ]
    candidates = []
    for pat in patterns:
        candidates.extend(glob.glob(pat))
    return random.choice(candidates) if candidates else None

# ----------------------------------------------------------------------
# Font helper
# ----------------------------------------------------------------------
def load_font(size):
    if os.path.isfile(FONT_FILE):
        try:
            return ImageFont.truetype(FONT_FILE, size)
        except Exception:
            pass
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

# ----------------------------------------------------------------------
# Quote wrapping
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

def best_font_size(lines, max_width):
    font_path = None
    if os.path.isfile(FONT_FILE):
        font_path = FONT_FILE
    else:
        for p in [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
            "C:/Windows/Fonts/times.ttf",
            "C:/Windows/Fonts/georgia.ttf",
            "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
            "/System/Library/Fonts/Georgia.ttf",
        ]:
            if os.path.isfile(p):
                font_path = p
                break
    low, high = 10, 200
    best = low
    while low <= high:
        mid = (low + high) // 2
        try:
            font = ImageFont.truetype(font_path, mid) if font_path else load_font(mid)
        except Exception:
            font = load_font(mid)
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        fits = True
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            w = bbox[2] - bbox[0]
            if w > max_width:
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
# Build composite frame
# ----------------------------------------------------------------------
def build_text_frame(image, quote, writer):
    img = image.copy()
    draw = ImageDraw.Draw(img)
    max_width = VIDEO_SIZE[0] - 2 * MARGIN
    lines = split_quote_two_lines(quote)
    quote_size = best_font_size(lines, max_width)
    quote_font = load_font(quote_size)
    writer_size = max(int(quote_size * WRITER_FONT_RATIO), 12)
    writer_font = load_font(writer_size)
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
    writer_text = f"- {writer}"
    writer_bbox = draw.textbbox((0, 0), writer_text, font=writer_font)
    writer_w = writer_bbox[2] - writer_bbox[0]
    writer_x = (VIDEO_SIZE[0] - writer_w) // 2
    writer_y = start_y + total_height + 30
    draw_text_with_stroke(draw, writer_text, (writer_x, writer_y),
                          writer_font, TEXT_COLOR, STROKE_COLOR, STROKE_WIDTH)
    return img

# ----------------------------------------------------------------------
# YouTube authentication (Colab-friendly via auth_code.txt)
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
# Get existing scheduled times (private videos with future publishAt)
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
            pageToken=next_page_token
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

# ----------------------------------------------------------------------
# Find next free slot among the daily 5:00 and 17:00 UTC times
# ----------------------------------------------------------------------
def next_free_slot(occupied_set):
    now = datetime.utcnow()
    # Start from today
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    while True:
        for h, m in SLOT_HOURS:
            candidate = day.replace(hour=h, minute=m, second=0, microsecond=0)
            if candidate > now and candidate not in occupied_set:
                return candidate
        day += timedelta(days=1)  # move to next day

# ----------------------------------------------------------------------
# Upload video (thumbnail failure is non-fatal)
# ----------------------------------------------------------------------
def upload_video(video_path, title, description, tags, category_id, publish_at):
    youtube = get_authenticated_service()
    privacy_status = "private"

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id
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

    # Thumbnail – optional (failure not critical)
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
# Video creation (returns thumbnail path)
# ----------------------------------------------------------------------
def create_video(quote, writer, image_path, music_path, output_path):
    pil_img = Image.open(image_path).convert("RGB")
    img_w, img_h = pil_img.size
    target_w, target_h = VIDEO_SIZE
    scale = max(target_w / img_w, target_h / img_h)
    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    pil_img = pil_img.crop((left, top, left + target_w, top + target_h))

    final_frame = build_text_frame(pil_img, quote, writer)

    # Save thumbnail
    thumb_path = output_path.replace(".mp4", "_thumb.jpg")
    final_frame.save(thumb_path, "JPEG", quality=90)
    logger.info(f"Thumbnail saved: {thumb_path}")

    # Video clip
    img_array = np.array(final_frame)
    clip = ImageClip(img_array).set_duration(TOTAL_DURATION).fadein(FADE_DURATION)

    audio = AudioFileClip(music_path)
    if audio.duration > TOTAL_DURATION:
        audio = audio.subclip(0, TOTAL_DURATION)
    final = clip.set_audio(audio)

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
    clip.close()
    return thumb_path

# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    processed = load_processed()
    logger.info(f"Already processed lines: {sorted(processed)}")

    if not os.path.exists(QUOTE_FILE):
        logger.error(f"Quote file {QUOTE_FILE} not found.")
        sys.exit(1)

    with open(QUOTE_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    to_process = []
    for idx, line in enumerate(lines, start=1):
        if idx in processed:
            continue
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            if all(k in data for k in ("qoute", "writer")):
                to_process.append((idx, line, data))
        except json.JSONDecodeError:
            logger.error(f"Line {idx}: invalid JSON, skipping.")

    if not to_process:
        logger.info("No unprocessed lines. Exiting.")
        return

    # Get already occupied slots
    logger.info("Fetching existing scheduled videos...")
    occupied = get_upcoming_scheduled_times()
    logger.info(f"Found {len(occupied)} already scheduled slots.")

    next_slot = next_free_slot(occupied)
    logger.info(f"First free slot: {next_slot.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    for line_idx, line, data in to_process:
        quote = data["qoute"]
        writer = data["writer"]

        if len(quote) > MAX_QUOTE_LEN:
            logger.info(f"Line {line_idx}: quote too long ({len(quote)} chars), marking processed.")
            mark_processed(line_idx)
            continue

        image_path = get_random_image()
        if not image_path:
            logger.error(f"Line {line_idx}: no images found in {IMAGE_DIR}. Skipping.")
            continue

        music_path = get_random_music()
        if not music_path:
            logger.error(f"Line {line_idx}: no music found in {MUSIC_DIR}. Skipping.")
            continue

        next_idx = get_next_output_index()
        video_name = f"{OUTPUT_PREFIX}_{next_idx:03d}{OUTPUT_EXT}"
        video_path = os.path.join(os.getcwd(), video_name)

        try:
            create_video(quote, writer, image_path, music_path, video_path)
        except Exception as e:
            logger.error(f"Line {line_idx}: video creation failed – {e}. Skipping.")
            continue

        # Upload
        try:
            title = f"{quote} – {writer}"[:100]
            description = (
                f"{quote} – {writer}\n\n"
                f"✨ Topic: Roman Wisdom\n"
                f"🔖 #quotes #roman #motivation #wisdom\n\n"
                f"🎵 Music from YouTube Audio Library\n"
                f"📌 Subscribe for daily quotes"
            )
            tags = list(set(BASE_TAGS + [writer]))

            upload_video(
                video_path=video_path,
                title=title,
                description=description,
                tags=tags,
                category_id=CATEGORY_ID,
                publish_at=next_slot
            )
            # Success
            mark_processed(line_idx)
            occupied.add(next_slot)
            next_slot = next_free_slot(occupied)
            logger.info(f"Line {line_idx}: scheduled at {next_slot.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        except Exception as e:
            logger.error(f"Line {line_idx}: upload/scheduling failed – {e}. Stopping execution.")
            break

    logger.info("Script finished.")

if __name__ == "__main__":
    main()
