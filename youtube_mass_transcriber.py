
import os
import re
import csv
import json
import time
import argparse
import subprocess
import hashlib
from datetime import datetime
from typing import List, Dict, Any, Tuple
from collections import Counter

import requests
from tqdm import tqdm

# PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    ListFlowable,
    ListItem,
)

OUTPUT_ROOT = "trading_dataset"
CONFIG_DIR = "config"
REPORTS_DIR = os.path.join(OUTPUT_ROOT, "reports")
PROMPTS_DIR = "prompts"
DOSSIER_PROMPT_PATH = os.path.join(PROMPTS_DIR, "dossier_prompt.txt")

LOG_DIR = os.path.join(OUTPUT_ROOT, "logs")
PROCESSED_LOG = os.path.join(LOG_DIR, "processed_videos.txt")
SKIPPED_LOG = os.path.join(LOG_DIR, "skipped_videos.txt")
EMPTY_LOG = os.path.join(LOG_DIR, "empty_transcripts.txt")
FAILED_VIDEO_NOTE_LOG = os.path.join(LOG_DIR, "failed_video_note_extractions.txt")
MODEL_TIMEOUT_LOG = os.path.join(LOG_DIR, "ollama_timeouts.txt")
MODEL_JSON_ERROR_LOG = os.path.join(LOG_DIR, "ollama_json_errors.txt")
FAILED_VIDEO_TIMEOUTS_LOG = os.path.join(LOG_DIR, "failed_video_timeouts.txt")
CURRENT_VIDEO_LOG = os.path.join(LOG_DIR, "current_video.txt")
LAST_ACTIVITY_LOG = os.path.join(LOG_DIR, "last_activity.txt")
SIMPLIFY_MANIFEST_NAME = "simplify_manifest.json"

DEFAULT_SETTINGS = {
    "MIN_VIEWS": 5000,
    "MAX_SEARCH_RESULTS": 2000,
    "MIN_TRANSCRIPT_LENGTH": 200,
    "OLLAMA_HOST": "http://localhost:11434",
    "OLLAMA_TIMEOUT_SECONDS": 1800,
    "EXTRACTOR_MODEL": "llama3.1",
    "MAX_RETRIES": 4,
    "SLEEP_BETWEEN_CALLS_MS": 750,
    "SEARCH_MIN_AGE_DAYS": 0,
    "SEARCH_MAX_AGE_DAYS": 730,
    "DOSSIER_IF_EXISTS": "skip",  # skip | replace | ask
    "REPORT_RENDER_EVERY_N_VIDEOS": 1,
    "MAX_TRANSCRIPT_CHARS_PER_VIDEO": 120000,
    "MAX_VIDEO_ANALYSIS_SECONDS": 1200,
    "DATASET_ROOT": "dataset",
    "ACTIVE_CREATOR": "",
    "DOSSIER_SOURCE": "raw",
    "SIMPLIFY_REMOVE_FILLERS": 1,
    "SIMPLIFY_REMOVE_PROMOS": 1,
    "SIMPLIFY_MAX_PROMO_LINES_AT_START": 8,
    "SIMPLIFY_MAX_PROMO_LINES_AT_END": 8,
    "AUTO_SIMPLIFY_ON_MISSING_SIMPLE": 0,
}

DOSSIER_SECTIONS = [
    "Document header",
    "Scope and limitations",
    "Executive overview",
    "Source identity as presented in transcripts",
    "Markets, instruments, and assets mentioned",
    "Timeframes and style",
    "Core beliefs and worldview",
    "Concepts and vocabulary map",
    "Setups, models, and trade structures",
    "Entry logic",
    "Exit logic",
    "Risk management framework",
    "Psychology and trader behavior",
    "Market conditions framework",
    "Tools, indicators, and data sources mentioned",
    "Process and routine",
    "Repeated claims and major themes",
    "Contradictions, tensions, and ambiguity",
    "What is missing or underexplained",
    "Evidence appendix",
    "Verification backlog",
    "Glossary of terms, abbreviations, and mechanisms",
]

CANONICAL_NOTE_FIELDS = [
    "overview",
    "markets",
    "timeframes",
    "style",
    "core_beliefs",
    "concepts",
    "setups",
    "entry_logic",
    "exit_logic",
    "risk_management",
    "psychology",
    "market_conditions",
    "tools",
    "process_routine",
    "repeated_claims",
    "contradictions",
    "missing_or_unclear",
    "verification_backlog",
    "glossary_terms",
    "evidence_quotes",
]

AUDIT_REPORT_NAME = "consistency_audit.json"
NOTES_MANIFEST_NAME = "notes_manifest.json"
DOSSIER_MANIFEST_NAME = "dossier_manifest.json"


def status(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def apply_runtime_paths(settings: Dict[str, Any]) -> None:
    global OUTPUT_ROOT, REPORTS_DIR, LOG_DIR, PROCESSED_LOG, SKIPPED_LOG, EMPTY_LOG
    global FAILED_VIDEO_NOTE_LOG, MODEL_TIMEOUT_LOG, MODEL_JSON_ERROR_LOG
    global FAILED_VIDEO_TIMEOUTS_LOG, CURRENT_VIDEO_LOG, LAST_ACTIVITY_LOG

    requested_root = str(settings.get("DATASET_ROOT", OUTPUT_ROOT) or OUTPUT_ROOT).strip() or OUTPUT_ROOT
    if requested_root == "dataset" and not os.path.exists(requested_root) and os.path.exists("trading_dataset"):
        requested_root = "trading_dataset"
    OUTPUT_ROOT = requested_root
    REPORTS_DIR = os.path.join(OUTPUT_ROOT, "reports")
    LOG_DIR = os.path.join(OUTPUT_ROOT, "logs")
    PROCESSED_LOG = os.path.join(LOG_DIR, "processed_videos.txt")
    SKIPPED_LOG = os.path.join(LOG_DIR, "skipped_videos.txt")
    EMPTY_LOG = os.path.join(LOG_DIR, "empty_transcripts.txt")
    FAILED_VIDEO_NOTE_LOG = os.path.join(LOG_DIR, "failed_video_note_extractions.txt")
    MODEL_TIMEOUT_LOG = os.path.join(LOG_DIR, "ollama_timeouts.txt")
    MODEL_JSON_ERROR_LOG = os.path.join(LOG_DIR, "ollama_json_errors.txt")
    FAILED_VIDEO_TIMEOUTS_LOG = os.path.join(LOG_DIR, "failed_video_timeouts.txt")
    CURRENT_VIDEO_LOG = os.path.join(LOG_DIR, "current_video.txt")
    LAST_ACTIVITY_LOG = os.path.join(LOG_DIR, "last_activity.txt")


def resolve_channel_folder(channel_folder: str | None, settings: Dict[str, Any]) -> str:
    if channel_folder:
        return os.path.abspath(channel_folder)
    creator = str(settings.get("ACTIVE_CREATOR", "")).strip()
    if not creator:
        raise SystemExit("No creator selected. Set ACTIVE_CREATOR in config/settings.txt or pass --channel-folder.")
    return os.path.abspath(os.path.join(OUTPUT_ROOT, "creators", safe_filename(creator)))


def normalize_source_name(source: str | None, settings: Dict[str, Any]) -> str:
    value = str(source or settings.get("DOSSIER_SOURCE", "raw")).strip().lower() or "raw"
    if value in {"simple", "simplified"}:
        return "simple"
    return "raw"


def transcripts_dir_for_source(channel_folder: str, source: str = "raw") -> str:
    return os.path.join(channel_folder, "individual_video_scripts_simple" if source == "simple" else "individual_video_scripts")


def analysis_root_for_source(channel_folder: str, source: str = "raw") -> str:
    return os.path.join(channel_folder, "_analysis_simple" if source == "simple" else "_analysis")


#############################################
# Activity helpers
#############################################

def touch_activity(note: str = "") -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    with open(LAST_ACTIVITY_LOG, "w", encoding="utf-8") as f:
        f.write(f"{stamp} {note}\n")


def set_current_video(transcript_file: str) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(CURRENT_VIDEO_LOG, "w", encoding="utf-8") as f:
        f.write(transcript_file + "\n")


def clear_current_video() -> None:
    if os.path.exists(CURRENT_VIDEO_LOG):
        os.remove(CURRENT_VIDEO_LOG)


def truncate_transcript(text: str, settings: dict) -> str:
    max_chars = int(settings.get("MAX_TRANSCRIPT_CHARS_PER_VIDEO", 120000))
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


#############################################
# Config helpers
#############################################

def ensure_config_templates() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    templates = {
        "channels.txt": "https://www.youtube.com/@SMBCapital/videos\nhttps://www.youtube.com/@Bookmap/videos\n",
        "searches.txt": "order flow trading\nES futures trading\nprop trader interview\n",
        "filters.txt": "crypto signal\nforex signal\nget rich quick\nshorts\n",
        "settings.txt": "\n".join([
            f"MIN_VIEWS={DEFAULT_SETTINGS['MIN_VIEWS']}",
            f"MAX_SEARCH_RESULTS={DEFAULT_SETTINGS['MAX_SEARCH_RESULTS']}",
            f"MIN_TRANSCRIPT_LENGTH={DEFAULT_SETTINGS['MIN_TRANSCRIPT_LENGTH']}",
            f"OLLAMA_TIMEOUT_SECONDS={DEFAULT_SETTINGS['OLLAMA_TIMEOUT_SECONDS']}",
            f"MAX_RETRIES={DEFAULT_SETTINGS['MAX_RETRIES']}",
            f"SLEEP_BETWEEN_CALLS_MS={DEFAULT_SETTINGS['SLEEP_BETWEEN_CALLS_MS']}",
            f"SEARCH_MIN_AGE_DAYS={DEFAULT_SETTINGS['SEARCH_MIN_AGE_DAYS']}",
            f"SEARCH_MAX_AGE_DAYS={DEFAULT_SETTINGS['SEARCH_MAX_AGE_DAYS']}",
            f"REPORT_RENDER_EVERY_N_VIDEOS={DEFAULT_SETTINGS['REPORT_RENDER_EVERY_N_VIDEOS']}",
            f"DOSSIER_IF_EXISTS={DEFAULT_SETTINGS['DOSSIER_IF_EXISTS']}",
            f"OLLAMA_HOST={DEFAULT_SETTINGS['OLLAMA_HOST']}",
            f"EXTRACTOR_MODEL={DEFAULT_SETTINGS['EXTRACTOR_MODEL']}",
            f"MAX_TRANSCRIPT_CHARS_PER_VIDEO={DEFAULT_SETTINGS['MAX_TRANSCRIPT_CHARS_PER_VIDEO']}",
            f"MAX_VIDEO_ANALYSIS_SECONDS={DEFAULT_SETTINGS['MAX_VIDEO_ANALYSIS_SECONDS']}",
        ]) + "\n",
    }
    for filename, content in templates.items():
        path = os.path.join(CONFIG_DIR, filename)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)


def ensure_prompt_dir() -> None:
    os.makedirs(PROMPTS_DIR, exist_ok=True)
    if not os.path.exists(DOSSIER_PROMPT_PATH):
        default_prompt = """You are generating a transcript-based trading dossier.

STRICT RULES:
- Use only the transcript-derived material provided to you.
- Do not use outside knowledge.
- Do not correct the speaker with external facts.
- Do not fill gaps with assumptions.
- If something is unclear, write: not clearly stated in transcripts.
- If something is absent, write: not mentioned.
- If something appears once, label it as isolated.
- If something appears repeatedly, label it as recurring or dominant.
- This dossier is not a truth assessment. It is a structured map of what is said in the transcripts.
"""
        with open(DOSSIER_PROMPT_PATH, "w", encoding="utf-8") as f:
            f.write(default_prompt)


def load_dossier_prompt() -> str:
    if not os.path.exists(DOSSIER_PROMPT_PATH):
        raise FileNotFoundError(f"Missing dossier prompt file: {DOSSIER_PROMPT_PATH}")
    with open(DOSSIER_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def read_lines(path: str) -> List[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [x.strip() for x in f.readlines() if x.strip() and not x.strip().startswith("#")]


def load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    path = os.path.join(CONFIG_DIR, "settings.txt")
    if not os.path.exists(path):
        return settings

    string_keys = {
        "OLLAMA_HOST",
        "EXTRACTOR_MODEL",
        "DOSSIER_IF_EXISTS",
        "DATASET_ROOT",
        "ACTIVE_CREATOR",
        "DOSSIER_SOURCE",
    }

    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k in string_keys:
                settings[k] = v.strip()
            else:
                try:
                    settings[k] = int(v)
                except ValueError:
                    settings[k] = v.strip()
    return settings


#############################################
# Utilities
#############################################

def safe_filename(name: str) -> str:
    cleaned = "".join(c for c in name if c.isalnum() or c in " _-()").rstrip()
    return cleaned[:180] if cleaned else "untitled"


def ensure_logs() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    for file in [
        PROCESSED_LOG,
        SKIPPED_LOG,
        EMPTY_LOG,
        FAILED_VIDEO_NOTE_LOG,
        MODEL_TIMEOUT_LOG,
        MODEL_JSON_ERROR_LOG,
        FAILED_VIDEO_TIMEOUTS_LOG,
    ]:
        if not os.path.exists(file):
            open(file, "w", encoding="utf-8").close()


def log(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")


def load_processed() -> set:
    if not os.path.exists(PROCESSED_LOG):
        return set()
    with open(PROCESSED_LOG, encoding="utf-8") as f:
        return set(f.read().splitlines())


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def make_channel_id(channel_name: str) -> str:
    safe = safe_filename(channel_name) or "channel"
    digest = hashlib.sha256(channel_name.encode("utf-8")).hexdigest()[:8]
    return f"{safe}__{digest}"


def make_note_id(channel_name: str, record_index: int, transcript_file: str) -> str:
    base = safe_filename(channel_name) or "channel"
    transcript_digest = hashlib.sha256(transcript_file.encode("utf-8")).hexdigest()[:10]
    return f"{base}__{record_index:05d}__{transcript_digest}"


#############################################
# Scraper logic
#############################################

def clean_vtt(text: str) -> str:
    text = re.sub(r'<\d{2}:\d{2}:\d{2}\.\d+>', '', text)
    text = re.sub(r'</?c>', '', text)
    text = re.sub(r'WEBVTT.*?\n', '', text)
    text = re.sub(r'Kind: captions\s*', '', text)
    text = re.sub(r'Language: \w+\s*', '', text)
    text = re.sub(r'\d{2}:\d{2}:\d{2}\.\d+ --> .*', '', text)
    text = re.sub(r'^\d+$', '', text, flags=re.MULTILINE)

    lines = []
    seen = set()
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)

    text = " ".join(lines)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def yt_json(url: str) -> Dict[str, Any] | None:
    cmd = ["yt-dlp", "-J", url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        status(f"yt-dlp failed for {url}: {result.stderr[:400]}")
        return None
    return json.loads(result.stdout)


def search_videos(query: str, settings: Dict[str, Any], blacklist: List[str]) -> List[Dict[str, Any]]:
    max_results = settings["MAX_SEARCH_RESULTS"]
    min_views = settings["MIN_VIEWS"]
    url = f"ytsearch{max_results}:{query}"
    status(f"Search scraping started: {query}")
    data = yt_json(url)
    videos = []
    if not data:
        status(f"Search produced no JSON: {query}")
        return videos

    raw_entries = data.get("entries", [])
    status(f"Search raw results for '{query}': {len(raw_entries)}")
    for entry in raw_entries:
        views = entry.get("view_count", 0) or 0
        title = entry.get("title", "") or ""
        if views < min_views:
            continue
        if any(b.lower() in title.lower() for b in blacklist):
            continue
        videos.append({
            "id": entry["id"],
            "title": title,
            "url": f"https://www.youtube.com/watch?v={entry['id']}",
            "uploader": entry.get("uploader") or entry.get("channel") or "",
            "view_count": views,
            "source_type": "search",
            "source_query": query,
        })
    status(f"Search kept results for '{query}': {len(videos)}")
    return videos


def channel_videos(channel_url: str) -> List[Dict[str, Any]]:
    status(f"Fetching channel video list: {channel_url}")
    cmd = ["yt-dlp", "--flat-playlist", "-J", channel_url]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        status(f"Channel fetch failed: {result.stderr[:400]}")
        return []
    data = json.loads(result.stdout)
    videos = []
    for entry in data.get("entries", []):
        vid = entry["id"]
        videos.append({
            "id": vid,
            "title": entry.get("title", vid),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "uploader": "",
            "view_count": None,
            "source_type": "channel",
            "source_query": channel_url,
        })
    status(f"Channel videos found: {len(videos)}")
    return videos


def download_transcript(video: Dict[str, Any], folder: str, processed: set, settings: Dict[str, Any]) -> str | None:
    video_id = video["id"]
    title = video["title"]
    if video_id in processed:
        return None

    safe_title = safe_filename(title)
    base = os.path.join(folder, safe_title)
    txt_file = base + "_transcript.txt"

    if os.path.exists(txt_file):
        log(PROCESSED_LOG, video_id)
        return txt_file

    url = video["url"]
    cmd = [
        "yt-dlp",
        "--skip-download",
        "--write-auto-subs",
        "--write-subs",
        "--sub-lang", "en",
        "--convert-subs", "vtt",
        "-o", base,
        url,
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    vtt_file = base + ".en.vtt"
    if not os.path.exists(vtt_file):
        log(SKIPPED_LOG, video_id)
        return None

    with open(vtt_file, encoding="utf-8") as f:
        vtt = f.read()
    os.remove(vtt_file)
    cleaned = clean_vtt(vtt)
    if len(cleaned) < settings["MIN_TRANSCRIPT_LENGTH"]:
        log(EMPTY_LOG, video_id)
        return None

    with open(txt_file, "w", encoding="utf-8") as f:
        f.write(cleaned)
    log(PROCESSED_LOG, video_id)
    return txt_file


def build_dataset(videos: List[Dict[str, Any]], base_path: str, settings: Dict[str, Any]) -> None:
    video_folder = os.path.join(base_path, "individual_video_scripts")
    os.makedirs(video_folder, exist_ok=True)
    processed = load_processed()
    all_transcripts = []
    titles = []
    video_index = []

    status(f"Starting transcript download for {len(videos)} videos into {base_path}")
    for idx, video in enumerate(tqdm(videos, desc=f"Scraping {os.path.basename(base_path)}"), start=1):
        if idx == 1 or idx % 25 == 0:
            status(f"Working on video {idx}/{len(videos)}: {video['title'][:120]}")

        file = download_transcript(video, video_folder, processed, settings)

        if not file:
            safe_title = safe_filename(video["title"])
            existing_file = os.path.join(video_folder, safe_title + "_transcript.txt")
            if os.path.exists(existing_file):
                file = existing_file

        if file and os.path.exists(file):
            with open(file, encoding="utf-8") as f:
                text = f.read().strip()
            if not text:
                continue

            titles.append(video["title"])
            all_transcripts.append(text)
            video_index.append({
                "video_id": video["id"],
                "title": video["title"],
                "url": video["url"],
                "transcript_file": os.path.basename(file),
                "uploader": video.get("uploader") or "",
                "view_count": video.get("view_count"),
                "source_type": video.get("source_type"),
                "source_query": video.get("source_query"),
            })

    if not all_transcripts:
        status(f"No usable transcripts found in {base_path}")
        return

    save_text(os.path.join(base_path, "channel_alltranscripts.txt"), "\n\n".join(all_transcripts))
    save_text(os.path.join(base_path, "channel_allvideostranscribed.txt"), "\n".join(titles))
    save_json(os.path.join(base_path, "video_index.json"), video_index)

    csv_path = os.path.join(base_path, "video_index.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["video_id", "title", "url", "transcript_file", "uploader", "view_count", "source_type", "source_query"],
        )
        writer.writeheader()
        writer.writerows(video_index)
    status(f"Dataset built successfully for {base_path}")


def scrape_mode(settings: Dict[str, Any]) -> None:
    ensure_config_templates()
    channels = read_lines(os.path.join(CONFIG_DIR, "channels.txt"))
    search_queries = read_lines(os.path.join(CONFIG_DIR, "searches.txt"))
    blacklist = read_lines(os.path.join(CONFIG_DIR, "filters.txt"))

    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    ensure_logs()
    status(f"Config summary: {len(channels)} channel(s), {len(search_queries)} search query(s), blacklist terms={len(blacklist)}")

    for channel in channels:
        status(f"Scraping channel: {channel}")
        videos = channel_videos(channel)
        name = safe_filename(channel.split("@")[-1].split("/")[0])
        base = os.path.join(OUTPUT_ROOT, "creators", name)
        os.makedirs(base, exist_ok=True)
        build_dataset(videos, base, settings)

    for query in search_queries:
        videos = search_videos(query, settings, blacklist)
        folder = safe_filename(query)
        base = os.path.join(OUTPUT_ROOT, "search_results", folder)
        os.makedirs(base, exist_ok=True)
        build_dataset(videos, base, settings)


#############################################
# Local LLM helpers (Ollama)
#############################################

def ollama_chat(model: str, system_prompt: str, user_prompt: str, settings: dict, expect_json: bool = False) -> str:
    host = settings.get("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
    url = f"{host}/api/generate"
    timeout_seconds = int(settings.get("OLLAMA_TIMEOUT_SECONDS", 1800))
    retries = int(settings.get("MAX_RETRIES", 4))
    sleep_ms = int(settings.get("SLEEP_BETWEEN_CALLS_MS", 750))

    full_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}\n"

    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
    }

    last_exc = None

    for attempt in range(retries + 1):
        try:
            touch_activity(f"ollama {model} attempt {attempt + 1}")
            response = requests.post(url, json=payload, timeout=timeout_seconds)
            response.raise_for_status()
            data = response.json()
            text = data.get("response", "")
            touch_activity(f"ollama {model} success")
            return text.strip()

        except requests.exceptions.Timeout as exc:
            last_exc = exc
            log(MODEL_TIMEOUT_LOG, f"{datetime.now().isoformat(timespec='seconds')} | model={model} | attempt={attempt + 1} | timeout")
            touch_activity(f"ollama {model} timeout")
        except Exception as exc:
            last_exc = exc
            touch_activity(f"ollama {model} error: {exc}")

        if attempt < retries:
            time.sleep(sleep_ms / 1000)

    raise RuntimeError(f"Ollama call failed for model {model}: {last_exc}")


def extract_json_object_candidate(text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None

    if text.startswith("{") and text.endswith("}"):
        return text

    fenced = re.search(r"```json\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        return fenced.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1].strip()

    return None


def try_parse_json(text: str) -> Dict[str, Any] | None:
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def extract_json_block(text: str) -> Dict[str, Any]:
    text = (text or "").strip()

    direct = try_parse_json(text)
    if direct is not None:
        return direct

    candidate = extract_json_object_candidate(text)
    if candidate:
        parsed = try_parse_json(candidate)
        if parsed is not None:
            return parsed
        raise RuntimeError(f"Found JSON-like block but could not parse it.\n\nRaw candidate:\n{candidate[:2000]}")

    raise RuntimeError(f"No valid JSON object found in model output.\n\nRaw output:\n{text[:2000]}")


#############################################
# Dossier normalization and merging
#############################################

def coerce_to_list_of_strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            if isinstance(v, list):
                joined = ", ".join(str(x).strip() for x in v if str(x).strip())
                if joined:
                    out.append(f"{k}: {joined}")
            elif v is not None:
                s = str(v).strip()
                if s:
                    out.append(f"{k}: {s}")
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(coerce_to_list_of_strings(item))
        return out
    s = str(value).strip()
    return [s] if s else []


def dedupe_strings(items: List[str]) -> List[str]:
    seen = set()
    out = []
    for item in items:
        cleaned = re.sub(r"\s+", " ", str(item or "").strip())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def normalize_evidence_item(item: Any, record: Dict[str, Any]) -> Dict[str, str] | None:
    if isinstance(item, str):
        excerpt = item.strip()
        if not excerpt:
            return None
        meta = build_note_meta(record)
        return {
            "topic": "unspecified",
            "transcript_file": record.get("transcript_file", ""),
            "title": record.get("title", ""),
            "url": record.get("url", ""),
            "excerpt": excerpt[:600],
            "channel_name": meta["channel_name"],
            "channel_id": meta["channel_id"],
            "record_index": meta["record_index"],
            "note_id": meta["note_id"],
        }
    if isinstance(item, dict):
        excerpt = str(item.get("excerpt", "")).strip()
        topic = str(item.get("topic", "unspecified")).strip() or "unspecified"
        if not excerpt and "quote" in item:
            excerpt = str(item.get("quote", "")).strip()
        if not excerpt:
            return None
        meta = build_note_meta(record)
        return {
            "topic": topic,
            "transcript_file": str(item.get("transcript_file", record.get("transcript_file", ""))).strip(),
            "title": str(item.get("title", record.get("title", ""))).strip(),
            "url": str(item.get("url", record.get("url", ""))).strip(),
            "excerpt": excerpt[:600],
            "channel_name": str(item.get("channel_name", meta["channel_name"])).strip(),
            "channel_id": str(item.get("channel_id", meta["channel_id"])).strip(),
            "record_index": item.get("record_index", meta["record_index"]),
            "note_id": str(item.get("note_id", meta["note_id"])).strip(),
        }
    return None


def normalize_glossary_item(item: Any) -> Dict[str, str] | None:
    if isinstance(item, str):
        term = item.strip()
        if not term:
            return None
        return {
            "term": term,
            "expansion": "not clearly stated in transcripts",
            "meaning_in_transcripts": "not clearly stated in transcripts",
            "how_it_works_in_transcripts": "not clearly stated in transcripts",
            "evidence_basis": "",
        }
    if isinstance(item, dict):
        term = str(item.get("term", "")).strip()
        if not term:
            return None
        return {
            "term": term,
            "expansion": str(item.get("expansion", "not clearly stated in transcripts")).strip() or "not clearly stated in transcripts",
            "meaning_in_transcripts": str(
                item.get("meaning_in_transcripts", item.get("meaning_in_these_transcripts", item.get("meaning", "not clearly stated in transcripts")))
            ).strip() or "not clearly stated in transcripts",
            "how_it_works_in_transcripts": str(
                item.get("how_it_works_in_transcripts", item.get("how_it_works_in_these_transcripts", item.get("how_it_works", "not clearly stated in transcripts")))
            ).strip() or "not clearly stated in transcripts",
            "evidence_basis": str(item.get("evidence_basis", "")).strip(),
        }
    return None


def build_note_meta(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "channel_name": record.get("channel_name", ""),
        "channel_id": record.get("channel_id", ""),
        "record_index": record.get("record_index"),
        "note_id": record.get("note_id", ""),
        "transcript_file": record.get("transcript_file", ""),
        "title": record.get("title", ""),
        "url": record.get("url", ""),
        "video_id": record.get("video_id", ""),
        "transcript_sha256": record.get("transcript_sha256", ""),
    }


def enrich_evidence_items(items: List[Dict[str, Any]], record: Dict[str, Any]) -> List[Dict[str, Any]]:
    meta = build_note_meta(record)
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        enriched = dict(item)
        enriched.setdefault("channel_name", meta["channel_name"])
        enriched.setdefault("channel_id", meta["channel_id"])
        enriched.setdefault("record_index", meta["record_index"])
        enriched.setdefault("note_id", meta["note_id"])
        out.append(enriched)
    return out


def normalize_note_structure(note: Any, record: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(note, dict):
        note = {}

    aliases = {
        "repeated_themes": "repeated_claims",
        "missing_topics": "missing_or_unclear",
        "overview_notes": "overview",
    }

    out: Dict[str, Any] = {}
    for field in CANONICAL_NOTE_FIELDS:
        value = note.get(field)
        if value is None:
            # check reverse aliases
            for k, v in aliases.items():
                if v == field and k in note:
                    value = note.get(k)
                    break
        if field == "overview":
            if value is None:
                value = "not clearly stated in transcripts"
            if isinstance(value, list):
                value = " | ".join(coerce_to_list_of_strings(value)) or "not clearly stated in transcripts"
            elif isinstance(value, dict):
                value = " | ".join(coerce_to_list_of_strings(value)) or "not clearly stated in transcripts"
            else:
                value = str(value).strip() or "not clearly stated in transcripts"
            out[field] = value
        elif field == "glossary_terms":
            norm = []
            for item in note.get(field, value if value is not None else [] ) or []:
                item_norm = normalize_glossary_item(item)
                if item_norm:
                    norm.append(item_norm)
            out[field] = norm
        elif field == "evidence_quotes":
            norm = []
            for item in note.get(field, value if value is not None else [] ) or []:
                item_norm = normalize_evidence_item(item, record)
                if item_norm:
                    norm.append(item_norm)
            out[field] = enrich_evidence_items(norm, record)
        else:
            out[field] = dedupe_strings(coerce_to_list_of_strings(value))

    out["_meta"] = build_note_meta(record)
    return out


def merge_glossary_terms(existing: List[Any], new_items: List[Any]) -> List[Dict[str, str]]:
    merged: Dict[str, Dict[str, str]] = {}

    for source in (existing or []):
        item = normalize_glossary_item(source)
        if item:
            merged[item["term"].lower()] = item

    for source in (new_items or []):
        item = normalize_glossary_item(source)
        if not item:
            continue

        key = item["term"].lower()
        if key not in merged:
            merged[key] = item
            continue

        cur = merged[key]
        for field in ["expansion", "meaning_in_transcripts", "how_it_works_in_transcripts", "evidence_basis"]:
            old = str(cur.get(field, "")).strip()
            new = str(item.get(field, "")).strip()
            if not new:
                continue
            if old in ("", "not clearly stated in transcripts"):
                cur[field] = new
            elif new not in old:
                cur[field] = f"{old} | {new}"
        merged[key] = cur

    return sorted(merged.values(), key=lambda x: x["term"].lower())


def merge_evidence(existing: List[Any], incoming: List[Any], record: Dict[str, Any]) -> List[Dict[str, str]]:
    out = []
    seen = set()
    for raw in (existing or []):
        item = normalize_evidence_item(raw, record)
        if not item:
            continue
        key = (item["topic"], item["transcript_file"], item["excerpt"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)

    for raw in (incoming or []):
        item = normalize_evidence_item(raw, record)
        if not item:
            continue
        key = (item["topic"], item["transcript_file"], item["excerpt"])
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def default_dossier_state(channel_name: str, channel_folder: str, total_videos: int) -> Dict[str, Any]:
    return {
        "channel_name": channel_name,
        "channel_id": make_channel_id(channel_name),
        "channel_folder": channel_folder,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "total_videos": total_videos,
        "processed_transcript_files": [],
        "processed_note_ids": [],
        "source_videos": [],
        "overview_notes": [],
        "markets": [],
        "timeframes": [],
        "style": [],
        "core_beliefs": [],
        "concepts": [],
        "setups": [],
        "entry_logic": [],
        "exit_logic": [],
        "risk_management": [],
        "psychology": [],
        "market_conditions": [],
        "tools": [],
        "process_routine": [],
        "repeated_claims": [],
        "contradictions": [],
        "missing_or_unclear": [],
        "verification_backlog": [],
        "glossary_terms": [],
        "evidence_quotes": [],
    }


def load_or_create_state(channel_name: str, channel_folder: str, analysis_root: str, total_videos: int) -> Dict[str, Any]:
    state_path = os.path.join(analysis_root, "incremental_state.json")
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
    else:
        state = default_dossier_state(channel_name, channel_folder, total_videos)

    defaults = default_dossier_state(channel_name, channel_folder, total_videos)
    for k, v in defaults.items():
        state.setdefault(k, v)
    state["total_videos"] = total_videos
    return state


def merge_video_note_into_state(state: Dict[str, Any], rec: Dict[str, Any], note: Dict[str, Any]) -> None:
    note = normalize_note_structure(note, rec)
    transcript_file = rec["transcript_file"]

    if transcript_file not in state["processed_transcript_files"]:
        state["processed_transcript_files"].append(transcript_file)
    note_id = rec.get("note_id", "")
    if note_id and note_id not in state.get("processed_note_ids", []):
        state.setdefault("processed_note_ids", []).append(note_id)

    source_video = {
        "record_index": rec.get("record_index"),
        "note_id": note_id,
        "channel_id": rec.get("channel_id", ""),
        "transcript_file": transcript_file,
        "title": rec.get("title", ""),
        "url": rec.get("url", ""),
        "video_id": rec.get("video_id", ""),
    }
    existing_files = {x.get("transcript_file") for x in state["source_videos"]}
    if transcript_file not in existing_files:
        state["source_videos"].append(source_video)

    overview = str(note.get("overview", "")).strip()
    if overview:
        state["overview_notes"] = dedupe_strings(state.get("overview_notes", []) + [overview])

    list_fields = [
        "markets",
        "timeframes",
        "style",
        "core_beliefs",
        "concepts",
        "setups",
        "entry_logic",
        "exit_logic",
        "risk_management",
        "psychology",
        "market_conditions",
        "tools",
        "process_routine",
        "repeated_claims",
        "contradictions",
        "missing_or_unclear",
        "verification_backlog",
    ]
    for key in list_fields:
        state[key] = dedupe_strings(state.get(key, []) + note.get(key, []))

    state["glossary_terms"] = merge_glossary_terms(state.get("glossary_terms", []), note.get("glossary_terms", []))
    state["evidence_quotes"] = merge_evidence(state.get("evidence_quotes", []), note.get("evidence_quotes", []), rec)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")


def per_video_extraction_prompt(transcript_text: str, record: Dict[str, Any]) -> str:
    dossier_rules = load_dossier_prompt()
    return f"""
{dossier_rules}

You are extracting structured source-bound notes from ONE transcript file.

STRICT EXTRACTION RULES:
- Use only this transcript.
- Do not use outside knowledge.
- Do not infer beyond what is stated or strongly implied.
- Return exactly one valid JSON object.
- No markdown.
- No commentary.
- If something is not mentioned, use an empty list or "not clearly stated in transcripts".
- Evidence excerpts must be copied from the transcript.

Required JSON schema:
{{
  "overview": "",
  "markets": [],
  "timeframes": [],
  "style": [],
  "core_beliefs": [],
  "concepts": [],
  "setups": [],
  "entry_logic": [],
  "exit_logic": [],
  "risk_management": [],
  "psychology": [],
  "market_conditions": [],
  "tools": [],
  "process_routine": [],
  "repeated_claims": [],
  "contradictions": [],
  "missing_or_unclear": [],
  "verification_backlog": [],
  "glossary_terms": [
    {{
      "term": "",
      "expansion": "",
      "meaning_in_transcripts": "",
      "how_it_works_in_transcripts": "",
      "evidence_basis": ""
    }}
  ],
  "evidence_quotes": [
    {{
      "topic": "",
      "transcript_file": "{record.get("transcript_file", "")}",
      "title": "{record.get("title", "")}",
      "url": "{record.get("url", "")}",
      "excerpt": ""
    }}
  ]
}}

Transcript file: {record.get("transcript_file", "")}
Video title: {record.get("title", "")}
Video url: {record.get("url", "")}

Transcript:
{transcript_text}
""".strip()


def minimal_fallback_video(record: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    short = (raw_text or "").strip()[:2000]
    return normalize_note_structure({
        "overview": short if short else "not clearly stated in transcripts",
        "markets": [],
        "timeframes": [],
        "style": [],
        "core_beliefs": [],
        "concepts": [],
        "setups": [],
        "entry_logic": [],
        "exit_logic": [],
        "risk_management": [],
        "psychology": [],
        "market_conditions": [],
        "tools": [],
        "process_routine": [],
        "repeated_claims": [],
        "contradictions": [],
        "missing_or_unclear": ["Extractor returned malformed JSON; fallback summary retained."] if short else ["Extractor failed; no structured notes available."],
        "verification_backlog": ["Review raw video extraction output manually if needed."],
        "glossary_terms": [],
        "evidence_quotes": [{
            "topic": "fallback_overview",
            "transcript_file": record.get("transcript_file", ""),
            "title": record.get("title", ""),
            "url": record.get("url", ""),
            "excerpt": short[:400],
        }] if short else [],
    }, record)


def repair_json_with_model(bad_text: str, settings: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    repair_prompt = f"""
Convert the following malformed output into exactly one valid JSON object.

Rules:
- Return JSON only
- No markdown
- No commentary
- Use valid JSON syntax only
- Preserve only transcript-derived content
- If information is missing, use empty arrays, empty strings, or "not mentioned"

Required JSON keys:
- overview
- markets
- timeframes
- style
- core_beliefs
- concepts
- setups
- entry_logic
- exit_logic
- risk_management
- psychology
- market_conditions
- tools
- process_routine
- repeated_claims
- contradictions
- missing_or_unclear
- verification_backlog
- glossary_terms
- evidence_quotes

Malformed output:
{bad_text}
""".strip()

    repaired = ollama_chat(
        settings["EXTRACTOR_MODEL"],
        "Return strict JSON only.",
        repair_prompt,
        settings,
        expect_json=True,
    )
    candidate = extract_json_object_candidate(repaired)
    if not candidate:
        raise RuntimeError("Repair model did not return a JSON object")
    parsed = try_parse_json(candidate)
    if parsed is None:
        raise RuntimeError("Repair model returned unparseable JSON")
    return normalize_note_structure(parsed, record)


def parse_or_repair_video_json(raw: str, settings: Dict[str, Any], record: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return normalize_note_structure(extract_json_block(raw), record)
    except Exception as exc:
        log(MODEL_JSON_ERROR_LOG, f"{datetime.now().isoformat(timespec='seconds')} | file={record.get('transcript_file','')} | parse_error={exc}")

    try:
        return repair_json_with_model(raw, settings, record)
    except Exception as exc:
        log(MODEL_JSON_ERROR_LOG, f"{datetime.now().isoformat(timespec='seconds')} | file={record.get('transcript_file','')} | repair_error={exc}")
        return minimal_fallback_video(record, raw)


def extract_note_for_video(rec: Dict[str, Any], settings: Dict[str, Any], per_video_notes_dir: str) -> Dict[str, Any]:
    note_filename = safe_filename(rec["transcript_file"]) + ".json"
    note_out_path = os.path.join(per_video_notes_dir, note_filename)
    raw_save_path = os.path.join(per_video_notes_dir, safe_filename(rec["transcript_file"]) + "_raw.txt")
    fail_path = os.path.join(per_video_notes_dir, safe_filename(rec["transcript_file"]) + "_FAILED.txt")

    if os.path.exists(note_out_path):
        with open(note_out_path, "r", encoding="utf-8") as f:
            normalized_existing = normalize_note_structure(json.load(f), rec)
        save_json(note_out_path, normalized_existing)
        return normalized_existing

    last_exc = None
    last_raw = ""

    for attempt in range(int(settings.get("MAX_RETRIES", 4)) + 1):
        try:
            set_current_video(rec["transcript_file"])
            touch_activity(f"starting {rec['transcript_file']} attempt {attempt + 1}")
            raw = ollama_chat(
                settings["EXTRACTOR_MODEL"],
                "Return strict JSON only. Do not write commentary. Output exactly one valid JSON object.",
                per_video_extraction_prompt(truncate_transcript(rec["text"], settings), rec),
                settings,
                expect_json=True,
            )
            last_raw = raw
            save_text(raw_save_path, raw)
            note = parse_or_repair_video_json(raw, settings, rec)
            save_json(note_out_path, note)

            if os.path.exists(fail_path):
                os.remove(fail_path)
            touch_activity(f"completed {rec['transcript_file']}")
            clear_current_video()
            return note
        except requests.exceptions.Timeout as exc:
            last_exc = exc
            log(MODEL_TIMEOUT_LOG, f"{datetime.now().isoformat(timespec='seconds')} | file={rec['transcript_file']} | attempt={attempt + 1} | timeout")
            status(f"Transcript note extraction timeout for {rec['transcript_file']} on attempt {attempt + 1}: {exc}")
        except Exception as exc:
            last_exc = exc
            status(f"Transcript note extraction failed for {rec['transcript_file']} on attempt {attempt + 1}: {exc}")

        time.sleep(int(settings.get("SLEEP_BETWEEN_CALLS_MS", 750)) / 1000.0)

    fallback = minimal_fallback_video(rec, last_raw)
    save_json(note_out_path, fallback)
    save_text(
        fail_path,
        f"FAILED FILE: {rec['transcript_file']}\n\nLAST ERROR:\n{last_exc}\n\nRAW OUTPUT:\n{last_raw}"
    )
    log(
        FAILED_VIDEO_NOTE_LOG,
        f"{datetime.now().isoformat(timespec='seconds')} | {rec['transcript_file']}"
    )
    clear_current_video()
    return fallback


def simplify_transcript_text(text: str, title: str, settings: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    original = text or ""
    cleaned = original.replace("\r\n", "\n").replace("\r", "\n")
    stats = {"removed_lines": 0, "removed_filler_tokens": 0, "removed_promo_lines": 0}

    lines = [ln.strip() for ln in cleaned.split("\n")]

    def is_promo_line(line: str) -> bool:
        l = line.strip().lower()
        if not l:
            return False
        promo_patterns = [
            "welcome back to the channel", "welcome to the channel", "subscribe to the channel",
            "like and subscribe", "smash the like button", "hit the like button",
            "make sure you subscribe", "don't forget to subscribe", "thanks for watching",
            "see you in the next video", "link in the description", "join my discord",
            "check out my course", "follow me on instagram", "follow me on twitter",
        ]
        return any(p in l for p in promo_patterns)

    max_head = int(settings.get("SIMPLIFY_MAX_PROMO_LINES_AT_START", 8))
    max_tail = int(settings.get("SIMPLIFY_MAX_PROMO_LINES_AT_END", 8))
    if int(settings.get("SIMPLIFY_REMOVE_PROMOS", 1)):
        for idx in range(min(max_head, len(lines))):
            if is_promo_line(lines[idx]):
                stats["removed_lines"] += 1
                stats["removed_promo_lines"] += 1
                lines[idx] = ""
        start_tail = max(0, len(lines) - max_tail)
        for idx in range(start_tail, len(lines)):
            if is_promo_line(lines[idx]):
                stats["removed_lines"] += 1
                stats["removed_promo_lines"] += 1
                lines[idx] = ""

    cleaned = "\n".join(x for x in lines if x)

    if int(settings.get("SIMPLIFY_REMOVE_FILLERS", 1)):
        filler_patterns = [
            r'(?i)(?<!\w)(uh+|um+|erm+|ah+)(?!\w)',
        ]
        for pat in filler_patterns:
            matches = re.findall(pat, cleaned)
            if matches:
                stats["removed_filler_tokens"] += len(matches)
                cleaned = re.sub(pat, " ", cleaned)

    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" ?\n ?", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if not cleaned:
        cleaned = original.strip()
    return cleaned, stats


def simplify_channel_folder(channel_folder: str, settings: Dict[str, Any], overwrite: bool = False) -> Dict[str, Any]:
    channel_folder = os.path.abspath(channel_folder)
    records, _index_by_file, _all_text = load_channel_data(channel_folder, source="raw")
    simple_dir = transcripts_dir_for_source(channel_folder, "simple")
    os.makedirs(simple_dir, exist_ok=True)
    analysis_root = analysis_root_for_source(channel_folder, "simple")
    os.makedirs(analysis_root, exist_ok=True)
    manifest = []
    for rec in records:
        out_path = os.path.join(simple_dir, rec["transcript_file"])
        if os.path.exists(out_path) and not overwrite:
            with open(out_path, "r", encoding="utf-8") as f:
                simple_text = f.read().strip()
            stats = {"reused": True}
        else:
            simple_text, stats = simplify_transcript_text(rec.get("text", ""), rec.get("title", ""), settings)
            save_text(out_path, simple_text)
        manifest.append({
            "transcript_file": rec["transcript_file"],
            "title": rec.get("title", ""),
            "record_index": rec.get("record_index"),
            "channel_id": rec.get("channel_id"),
            "note_id": rec.get("note_id"),
            "raw_sha256": sha256_text(rec.get("text", "")),
            "simple_sha256": sha256_text(simple_text),
            "stats": stats,
        })
    rebuild_channel_summary_files(channel_folder, source="simple")
    save_json(os.path.join(analysis_root, SIMPLIFY_MANIFEST_NAME), manifest)
    return {"channel_folder": channel_folder, "count": len(manifest), "manifest_path": os.path.join(analysis_root, SIMPLIFY_MANIFEST_NAME)}


def batch_simplify_mode(root: str, settings: Dict[str, Any], overwrite: bool = False) -> None:
    creators_root = os.path.join(root, "creators")
    if not os.path.isdir(creators_root):
        raise RuntimeError(f"Missing creators root: {creators_root}")
    for name in sorted(os.listdir(creators_root)):
        folder = os.path.join(creators_root, name)
        if os.path.isdir(folder):
            simplify_channel_folder(folder, settings, overwrite=overwrite)


def batch_repair_mode(root: str, settings: Dict[str, Any], if_exists: str = "replace", source: str | None = None) -> None:
    creators_root = os.path.join(root, "creators")
    if not os.path.isdir(creators_root):
        raise RuntimeError(f"Missing creators root: {creators_root}")
    for name in sorted(os.listdir(creators_root)):
        folder = os.path.join(creators_root, name)
        if os.path.isdir(folder):
            repair_channel_folder(folder, settings, if_exists=if_exists, source=source)
def get_report_paths(channel_name: str, source: str = "raw") -> Tuple[str, str]:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    stem = f"{safe_filename(channel_name)}_{'simple_' if source == 'simple' else ''}dossier"
    return os.path.join(REPORTS_DIR, f"{stem}.txt"), os.path.join(REPORTS_DIR, f"{stem}.pdf")


def should_generate_report(txt_path: str, pdf_path: str, if_exists: str) -> bool:
    existing = [p for p in [txt_path, pdf_path] if os.path.exists(p)]
    if not existing:
        return True

    mode = (if_exists or "skip").strip().lower()
    if mode == "replace":
        return True
    if mode == "ask":
        print("\nA dossier already exists:")
        for p in existing:
            print(f" - {p}")
        answer = input("Replace it? [y/N]: ").strip().lower()
        return answer in {"y", "yes", "r", "replace"}

    status(f"Skipping dossier because it already exists: {existing[0]}")
    return False


def rebuild_video_index_from_files(channel_folder: str, source: str = "raw") -> List[Dict[str, Any]]:
    transcripts_dir = transcripts_dir_for_source(channel_folder, source)
    if not os.path.exists(transcripts_dir):
        raise FileNotFoundError(f"Missing individual_video_scripts folder in {channel_folder}")

    records = []
    for filename in sorted(os.listdir(transcripts_dir)):
        if not filename.endswith("_transcript.txt"):
            continue
        title = filename.replace("_transcript.txt", "")
        records.append({
            "video_id": None,
            "title": title,
            "url": "",
            "transcript_file": filename,
            "uploader": "",
            "view_count": None,
            "source_type": "rebuilt_from_files",
            "source_query": channel_folder,
        })

    save_json(os.path.join(channel_folder, "video_index.json"), records)

    csv_path = os.path.join(channel_folder, "video_index.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["video_id", "title", "url", "transcript_file", "uploader", "view_count", "source_type", "source_query"],
        )
        writer.writeheader()
        writer.writerows(records)

    status(f"Rebuilt video_index.json from transcript files in {channel_folder}")
    return records


def load_channel_data(channel_folder: str, source: str = "raw") -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], str]:
    source = "simple" if source == "simple" else "raw"
    transcripts_dir = transcripts_dir_for_source(channel_folder, source)
    index_path = os.path.join(channel_folder, "video_index_simple.json" if source == "simple" else "video_index.json")
    fallback_index_path = os.path.join(channel_folder, "video_index.json")
    all_tx_path = os.path.join(channel_folder, "channel_alltranscripts_simple.txt" if source == "simple" else "channel_alltranscripts.txt")

    if not os.path.exists(transcripts_dir):
        raise FileNotFoundError(f"Missing transcripts directory: {transcripts_dir}")

    if not os.path.exists(index_path):
        if source == "simple" and os.path.exists(fallback_index_path):
            with open(fallback_index_path, "r", encoding="utf-8") as f:
                video_index_list = json.load(f)
        else:
            status(f"{os.path.basename(index_path)} missing in {channel_folder} — rebuilding from transcript files")
            video_index_list = rebuild_video_index_from_files(channel_folder, source=source)
    else:
        with open(index_path, "r", encoding="utf-8") as f:
            video_index_list = json.load(f)

    if not os.path.exists(all_tx_path):
        status(f"channel_alltranscripts.txt missing in {channel_folder} — rebuilding from transcript files")
        parts = []
        for filename in sorted(os.listdir(transcripts_dir)):
            if not filename.endswith("_transcript.txt"):
                continue
            path = os.path.join(transcripts_dir, filename)
            with open(path, encoding="utf-8") as f:
                text = f.read().strip()
            if text:
                parts.append(text)
        save_text(all_tx_path, "\n\n".join(parts))

    video_index_by_file = {
        item.get("transcript_file"): item
        for item in video_index_list
        if item.get("transcript_file")
    }

    transcript_records = []
    for filename in sorted(os.listdir(transcripts_dir)):
        if not filename.endswith("_transcript.txt"):
            continue
        path = os.path.join(transcripts_dir, filename)
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            continue
        meta = video_index_by_file.get(filename, {})
        record_index = len(transcript_records) + 1
        channel_name = os.path.basename(channel_folder.rstrip(os.sep))
        transcript_records.append({
            "record_index": record_index,
            "channel_name": channel_name,
            "channel_id": make_channel_id(channel_name),
            "note_id": make_note_id(channel_name, record_index, filename),
            "transcript_file": filename,
            "title": meta.get("title", filename.replace("_transcript.txt", "")),
            "url": meta.get("url") or "",
            "video_id": meta.get("video_id") or "",
            "text": text,
            "transcript_sha256": sha256_text(text),
        })

    with open(all_tx_path, "r", encoding="utf-8") as f:
        all_text = f.read()

    return transcript_records, video_index_by_file, all_text


def build_notes_manifest(records: List[Dict[str, Any]], per_video_notes_dir: str) -> Dict[str, Any]:
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "records": [],
    }
    for rec in records:
        note_filename = safe_filename(rec["transcript_file"]) + ".json"
        note_path = os.path.join(per_video_notes_dir, note_filename)
        raw_path = os.path.join(per_video_notes_dir, safe_filename(rec["transcript_file"]) + "_raw.txt")
        manifest["records"].append({
            "record_index": rec.get("record_index"),
            "channel_id": rec.get("channel_id", ""),
            "note_id": rec.get("note_id", ""),
            "transcript_file": rec["transcript_file"],
            "title": rec.get("title", ""),
            "url": rec.get("url", ""),
            "transcript_sha256": rec.get("transcript_sha256", sha256_text(rec.get("text", ""))),
            "note_exists": os.path.exists(note_path),
            "note_sha256": sha256_file(note_path) if os.path.exists(note_path) else "",
            "raw_exists": os.path.exists(raw_path),
            "raw_sha256": sha256_file(raw_path) if os.path.exists(raw_path) else "",
        })
    return manifest


def write_channel_manifests(channel_name: str, channel_folder: str, analysis_root: str, state: Dict[str, Any], records: List[Dict[str, Any]], report_txt_path: str, report_pdf_path: str) -> None:
    per_video_notes_dir = os.path.join(analysis_root, "per_video_notes")
    notes_manifest = build_notes_manifest(records, per_video_notes_dir)
    save_json(os.path.join(analysis_root, NOTES_MANIFEST_NAME), notes_manifest)

    dossier_manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "channel_name": channel_name,
        "channel_id": make_channel_id(channel_name),
        "channel_folder": channel_folder,
        "processed_transcript_files_count": len(state.get("processed_transcript_files", [])),
        "processed_note_ids_count": len(state.get("processed_note_ids", [])),
        "total_records": len(records),
        "state_sha256": sha256_file(os.path.join(analysis_root, "incremental_state.json")) if os.path.exists(os.path.join(analysis_root, "incremental_state.json")) else "",
        "report_txt_path": report_txt_path,
        "report_txt_sha256": sha256_file(report_txt_path) if os.path.exists(report_txt_path) else "",
        "report_pdf_path": report_pdf_path,
        "report_pdf_sha256": sha256_file(report_pdf_path) if os.path.exists(report_pdf_path) else "",
        "source_videos_count": len(state.get("source_videos", [])),
        "evidence_quotes_count": len(state.get("evidence_quotes", [])),
        "glossary_terms_count": len(state.get("glossary_terms", [])),
    }
    save_json(os.path.join(analysis_root, DOSSIER_MANIFEST_NAME), dossier_manifest)


def rebuild_channel_summary_files(channel_folder: str, source: str = "raw") -> None:
    records, video_index_by_file, _all_text = load_channel_data(channel_folder, source=source)
    titles = []
    transcript_blocks = []
    video_index_list = []
    for rec in records:
        titles.append(rec.get("title", "") or rec["transcript_file"].replace("_transcript.txt", ""))
        transcript_blocks.append(rec.get("text", ""))
        meta = video_index_by_file.get(rec["transcript_file"], {})
        video_index_list.append({
            "video_id": meta.get("video_id") or rec.get("video_id", ""),
            "title": rec.get("title", ""),
            "url": meta.get("url") or rec.get("url", ""),
            "transcript_file": rec["transcript_file"],
            "uploader": meta.get("uploader", ""),
            "view_count": meta.get("view_count"),
            "source_type": meta.get("source_type", "rebuilt_from_files"),
            "source_query": meta.get("source_query", channel_folder),
        })

    save_text(os.path.join(channel_folder, "channel_alltranscripts.txt"), "\n\n".join(x for x in transcript_blocks if x))
    save_text(os.path.join(channel_folder, "channel_allvideostranscribed.txt"), "\n".join(x for x in titles if x))
    save_json(os.path.join(channel_folder, "video_index.json"), video_index_list)
    csv_path = os.path.join(channel_folder, "video_index.csv")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["video_id", "title", "url", "transcript_file", "uploader", "view_count", "source_type", "source_query"],
        )
        writer.writeheader()
        writer.writerows(video_index_list)


def reset_analysis_outputs(channel_folder: str, source: str = "raw") -> None:
    analysis_root = analysis_root_for_source(channel_folder, source)
    per_video_notes_dir = os.path.join(analysis_root, "per_video_notes")
    if os.path.isdir(per_video_notes_dir):
        for name in os.listdir(per_video_notes_dir):
            full = os.path.join(per_video_notes_dir, name)
            if os.path.isfile(full):
                os.remove(full)
    for name in ["incremental_state.json", NOTES_MANIFEST_NAME, DOSSIER_MANIFEST_NAME, AUDIT_REPORT_NAME]:
        full = os.path.join(analysis_root, name)
        if os.path.exists(full):
            os.remove(full)


def repair_channel_folder(channel_folder: str, settings: Dict[str, Any], if_exists: str = "replace", source: str | None = None) -> Dict[str, Any]:
    channel_folder = os.path.abspath(channel_folder)
    channel_name = os.path.basename(channel_folder.rstrip(os.sep))
    source = normalize_source_name(source, settings)
    status(f"Repairing channel folder: {channel_folder} (source={source})")
    rebuild_channel_summary_files(channel_folder, source="raw")
    if source == "simple" or int(settings.get("AUTO_SIMPLIFY_ON_MISSING_SIMPLE", 0)):
        simplify_channel_folder(channel_folder, settings, overwrite=False)
    if if_exists == "replace":
        reset_analysis_outputs(channel_folder, source=source)
    dossier_mode(channel_folder, settings, if_exists=if_exists, source=source)
    audit = audit_channel_folder(channel_folder, write_report=True, source=source)
    status(f"Repair complete for {channel_name}: ok={audit.get('ok')} issues={len(audit.get('issues', []))} warnings={len(audit.get('warnings', []))}")
    return audit


def audit_channel_folder(channel_folder: str, write_report: bool = True, source: str = "raw") -> Dict[str, Any]:
    channel_folder = os.path.abspath(channel_folder)
    channel_name = os.path.basename(channel_folder.rstrip(os.sep))
    source = normalize_source_name(source, {"DOSSIER_SOURCE": source})
    analysis_root = analysis_root_for_source(channel_folder, source)
    records, _index_by_file, _all_text = load_channel_data(channel_folder, source=source)
    transcript_files = {r["transcript_file"] for r in records}
    report_txt_path, report_pdf_path = get_report_paths(channel_name, source=source)

    state_path = os.path.join(analysis_root, "incremental_state.json")
    state = {}
    if os.path.exists(state_path):
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)

    processed_files = set(state.get("processed_transcript_files", []))
    processed_note_ids = state.get("processed_note_ids", []) or []
    source_video_files = {x.get("transcript_file") for x in state.get("source_videos", []) if x.get("transcript_file")}
    source_note_ids = [x.get("note_id", "") for x in state.get("source_videos", []) if x.get("note_id")]
    evidence_files = {x.get("transcript_file") for x in state.get("evidence_quotes", []) if x.get("transcript_file")}
    evidence_note_ids = [x.get("note_id", "") for x in state.get("evidence_quotes", []) if x.get("note_id")]

    notes_manifest_path = os.path.join(analysis_root, NOTES_MANIFEST_NAME)
    dossier_manifest_path = os.path.join(analysis_root, DOSSIER_MANIFEST_NAME)
    suffix = "_simple" if source == "simple" else ""
    all_titles_path = os.path.join(channel_folder, f"channel_allvideostranscribed{suffix}.txt")
    index_json_path = os.path.join(channel_folder, f"video_index{suffix}.json")
    index_csv_path = os.path.join(channel_folder, f"video_index{suffix}.csv")
    all_tx_path = os.path.join(channel_folder, f"channel_alltranscripts{suffix}.txt")
    per_video_notes_dir = os.path.join(analysis_root, "per_video_notes")

    issues = []
    warnings = []

    if state and processed_files != transcript_files:
        missing = sorted(transcript_files - processed_files)
        extra = sorted(processed_files - transcript_files)
        if missing:
            issues.append({"type": "missing_processed_files", "files": missing})
        if extra:
            issues.append({"type": "unknown_processed_files", "files": extra})

    if state and not source_video_files.issubset(transcript_files):
        issues.append({"type": "source_videos_reference_missing_transcripts", "files": sorted(source_video_files - transcript_files)})

    if state and not evidence_files.issubset(transcript_files):
        issues.append({"type": "evidence_references_missing_transcripts", "files": sorted(evidence_files - transcript_files)})

    if len(processed_note_ids) != len(set(processed_note_ids)):
        issues.append({"type": "duplicate_processed_note_ids", "duplicates": [k for k, v in Counter(processed_note_ids).items() if v > 1]})
    if len(source_note_ids) != len(set(source_note_ids)):
        issues.append({"type": "duplicate_source_note_ids", "duplicates": [k for k, v in Counter(source_note_ids).items() if v > 1]})
    if len(evidence_note_ids) != len(set(evidence_note_ids)):
        warnings.append("duplicate note identifiers found inside evidence quotes")

    note_file_map = {}
    raw_file_map = {}
    orphan_note_files = []
    orphan_raw_files = []
    if os.path.exists(per_video_notes_dir):
        expected_note_names = {safe_filename(r["transcript_file"]) + ".json" for r in records}
        expected_raw_names = {safe_filename(r["transcript_file"]) + "_raw.txt" for r in records}
        for name in os.listdir(per_video_notes_dir):
            full = os.path.join(per_video_notes_dir, name)
            if not os.path.isfile(full):
                continue
            if name.endswith(".json"):
                note_file_map[name] = full
                if name not in expected_note_names:
                    orphan_note_files.append(name)
            elif name.endswith("_raw.txt"):
                raw_file_map[name] = full
                if name not in expected_raw_names:
                    orphan_raw_files.append(name)

    missing_note_files = []
    missing_raw_files = []
    invalid_note_meta = []
    for rec in records:
        note_name = safe_filename(rec["transcript_file"]) + ".json"
        raw_name = safe_filename(rec["transcript_file"]) + "_raw.txt"
        note_path = os.path.join(per_video_notes_dir, note_name)
        raw_path = os.path.join(per_video_notes_dir, raw_name)
        if not os.path.exists(note_path):
            missing_note_files.append(rec["transcript_file"])
        if not os.path.exists(raw_path):
            missing_raw_files.append(rec["transcript_file"])
        if os.path.exists(note_path):
            try:
                with open(note_path, "r", encoding="utf-8") as f:
                    note_json = json.load(f)
                meta = note_json.get("_meta", {}) if isinstance(note_json, dict) else {}
                if meta.get("note_id") != rec.get("note_id") or meta.get("transcript_file") != rec.get("transcript_file"):
                    invalid_note_meta.append(rec["transcript_file"])
            except Exception:
                invalid_note_meta.append(rec["transcript_file"])

    if missing_note_files:
        issues.append({"type": "missing_note_files", "files": missing_note_files})
    if missing_raw_files:
        warnings.append(f"missing raw extraction files for {len(missing_raw_files)} transcript(s)")
    if invalid_note_meta:
        issues.append({"type": "invalid_note_metadata", "files": invalid_note_meta})
    if orphan_note_files:
        warnings.append(f"orphan note files present: {len(orphan_note_files)}")
    if orphan_raw_files:
        warnings.append(f"orphan raw files present: {len(orphan_raw_files)}")

    if not os.path.exists(state_path):
        warnings.append("incremental_state.json is missing")
    if not os.path.exists(report_txt_path):
        warnings.append("report txt is missing")
    if not os.path.exists(report_pdf_path):
        warnings.append("report pdf is missing")
    if not os.path.exists(notes_manifest_path):
        warnings.append("notes manifest is missing")
    if not os.path.exists(dossier_manifest_path):
        warnings.append("dossier manifest is missing")
    if not os.path.exists(index_json_path):
        warnings.append("video_index.json is missing")
    if not os.path.exists(index_csv_path):
        warnings.append("video_index.csv is missing")
    if not os.path.exists(all_tx_path):
        warnings.append("channel_alltranscripts.txt is missing")
    if not os.path.exists(all_titles_path):
        warnings.append("channel_allvideostranscribed.txt is missing")

    expected_titles = [r.get("title", "").strip() for r in records if r.get("title", "").strip()]
    title_file_count = 0
    if os.path.exists(all_titles_path):
        with open(all_titles_path, "r", encoding="utf-8") as f:
            title_lines = [x.strip() for x in f.read().splitlines() if x.strip()]
        title_file_count = len(title_lines)
        if len(title_lines) != len(expected_titles):
            warnings.append(f"channel_allvideostranscribed.txt line count ({len(title_lines)}) does not match transcript record count ({len(expected_titles)})")

    note_manifest_count = 0
    notes_manifest_missing_entries = []
    if os.path.exists(notes_manifest_path):
        try:
            with open(notes_manifest_path, "r", encoding="utf-8") as f:
                notes_manifest = json.load(f)
            manifest_records = notes_manifest.get("records", []) if isinstance(notes_manifest, dict) else []
            note_manifest_count = len(manifest_records)
            manifest_by_file = {x.get("transcript_file"): x for x in manifest_records if isinstance(x, dict)}
            for rec in records:
                item = manifest_by_file.get(rec["transcript_file"])
                if not item:
                    notes_manifest_missing_entries.append(rec["transcript_file"])
                    continue
                if item.get("note_id") != rec.get("note_id"):
                    issues.append({"type": "notes_manifest_note_id_mismatch", "transcript_file": rec["transcript_file"]})
        except Exception as exc:
            issues.append({"type": "notes_manifest_unreadable", "error": str(exc)})

    if notes_manifest_missing_entries:
        issues.append({"type": "notes_manifest_missing_entries", "files": notes_manifest_missing_entries})

    audit = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "channel_name": channel_name,
        "channel_id": make_channel_id(channel_name),
        "channel_folder": channel_folder,
        "transcript_count": len(transcript_files),
        "processed_count": len(processed_files),
        "processed_note_id_count": len(processed_note_ids),
        "source_video_count": len(source_video_files),
        "evidence_quote_count": len(evidence_files),
        "note_manifest_count": note_manifest_count,
        "title_file_count": title_file_count,
        "report_txt_exists": os.path.exists(report_txt_path),
        "report_pdf_exists": os.path.exists(report_pdf_path),
        "issues": issues,
        "warnings": warnings,
        "orphan_note_files": orphan_note_files,
        "orphan_raw_files": orphan_raw_files,
        "missing_raw_files": missing_raw_files,
        "scrape_ready": os.path.exists(index_json_path) and os.path.exists(index_csv_path) and os.path.exists(all_tx_path) and os.path.exists(all_titles_path),
        "dossier_ready": os.path.exists(state_path) and os.path.exists(report_txt_path) and os.path.exists(report_pdf_path) and os.path.exists(notes_manifest_path) and os.path.exists(dossier_manifest_path) and len(missing_note_files) == 0,
        "ok": len(issues) == 0,
    }

    if write_report:
        os.makedirs(analysis_root, exist_ok=True)
        save_json(os.path.join(analysis_root, AUDIT_REPORT_NAME), audit)

    return audit


def section_content_for_state(section: str, state: Dict[str, Any]) -> str:
    if section == "Document header":
        return (
            f"{section}\n\n"
            f"Source name: {state['channel_name']}\n"
            f"Source folder: {state['channel_folder']}\n"
            f"Dossier type: transcript-derived research dossier\n"
            f"Generated from individual transcript files with incremental updates\n"
            f"Videos processed so far: {len(state.get('processed_transcript_files', []))} / {state.get('total_videos', 0)}"
        )

    if section == "Scope and limitations":
        return (
            f"{section}\n\n"
            "This dossier is based only on the transcript material collected for this source. "
            "It does not use outside knowledge, external verification, or factual correction. "
            "Topics that are absent, vague, or weakly evidenced are marked as not clearly stated in transcripts."
        )

    section_map = {
        "Executive overview": "overview_notes",
        "Source identity as presented in transcripts": "overview_notes",
        "Markets, instruments, and assets mentioned": "markets",
        "Timeframes and style": "timeframes",
        "Core beliefs and worldview": "core_beliefs",
        "Concepts and vocabulary map": "concepts",
        "Setups, models, and trade structures": "setups",
        "Entry logic": "entry_logic",
        "Exit logic": "exit_logic",
        "Risk management framework": "risk_management",
        "Psychology and trader behavior": "psychology",
        "Market conditions framework": "market_conditions",
        "Tools, indicators, and data sources mentioned": "tools",
        "Process and routine": "process_routine",
        "Repeated claims and major themes": "repeated_claims",
        "Contradictions, tensions, and ambiguity": "contradictions",
        "What is missing or underexplained": "missing_or_unclear",
        "Verification backlog": "verification_backlog",
    }

    if section == "Evidence appendix":
        items = state.get("evidence_quotes", [])
        if not items:
            return f"{section}\n\nnot clearly stated in transcripts"
        lines = [section, ""]
        for item in items:
            lines.append(
                f"- Topic: {item.get('topic', '')}\n"
                f"  File: {item.get('transcript_file', '')}\n"
                f"  Title: {item.get('title', '')}\n"
                f"  URL: {item.get('url', '') or 'not available'}\n"
                f"  Excerpt: {item.get('excerpt', '')}"
            )
        return "\n".join(lines)

    if section == "Glossary of terms, abbreviations, and mechanisms":
        glossary = state.get("glossary_terms", [])
        if not glossary:
            return f"{section}\n\nnot clearly stated in transcripts"
        lines = [section, ""]
        for item in glossary:
            lines.append(f"TERM: {item.get('term', '')}")
            lines.append(f"Expansion: {item.get('expansion', 'not clearly stated in transcripts')}")
            lines.append(f"Meaning in these transcripts: {item.get('meaning_in_transcripts', 'not clearly stated in transcripts')}")
            lines.append(f"How it works in these transcripts: {item.get('how_it_works_in_transcripts', 'not clearly stated in transcripts')}")
            lines.append(f"Evidence basis: {item.get('evidence_basis', 'not clearly stated in transcripts')}")
            lines.append("")
        return "\n".join(lines).strip()

    key = section_map.get(section)
    if not key:
        return f"{section}\n\nnot clearly stated in transcripts"

    values = state.get(key, [])
    if not values:
        return f"{section}\n\nnot clearly stated in transcripts"

    lines = [section, ""]
    for item in values:
        lines.append(f"- {item}")
    return "\n".join(lines)


def make_bundle_text(channel_name: str, channel_folder: str, state: Dict[str, Any]) -> str:
    parts = []
    parts.append(f"# Transcript-Derived Research Dossier - {channel_name}")
    parts.append("")
    parts.append(f"Source folder: {channel_folder}")
    parts.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    parts.append("Analysis mode: source-bound / transcript-only / no external verification")
    parts.append("")

    for section in DOSSIER_SECTIONS:
        parts.append(section_content_for_state(section, state))
        parts.append("")

    return "\n".join(parts).strip() + "\n"


def save_incremental_outputs(
    channel_name: str,
    channel_folder: str,
    state: Dict[str, Any],
    report_txt_path: str,
    analysis_root: str,
) -> Dict[str, str]:
    state_path = os.path.join(analysis_root, "incremental_state.json")
    save_json(state_path, state)

    section_texts = {section: section_content_for_state(section, state) for section in DOSSIER_SECTIONS}
    bundle_text = make_bundle_text(channel_name, channel_folder, state)

    save_text(os.path.join(analysis_root, f"{safe_filename(channel_name)}_dossier.txt"), bundle_text)
    save_text(report_txt_path, bundle_text)

    return section_texts


def escape_pdf_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_pdf(channel_name: str, channel_folder: str, section_texts: Dict[str, str], out_pdf: str) -> None:
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BodySmall", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.5, leading=13, spaceAfter=6, alignment=TA_LEFT))
    styles.add(ParagraphStyle(name="SectionTitle2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=14, leading=18, spaceAfter=8, textColor=colors.HexColor("#16324F")))
    styles.add(ParagraphStyle(name="Meta", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.5, leading=11, textColor=colors.HexColor("#555555")))
    styles.add(ParagraphStyle(name="TitleCenter", parent=styles["Title"], alignment=TA_CENTER, textColor=colors.HexColor("#16324F"), fontSize=22, leading=28))

    doc = SimpleDocTemplate(
        out_pdf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Transcript-Derived Research Dossier - {channel_name}",
        author="Local LLM pipeline",
    )

    story = []
    story.append(Paragraph(escape_pdf_text(f"Transcript-Derived Research Dossier - {channel_name}"), styles["TitleCenter"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(escape_pdf_text(channel_folder), styles["Meta"]))
    story.append(Paragraph(escape_pdf_text(f"Generated: {datetime.now().isoformat(timespec='seconds')}"), styles["Meta"]))
    story.append(Paragraph("Analysis mode: source-bound / transcript-only / no external verification", styles["Meta"]))
    story.append(Spacer(1, 10))

    for section in DOSSIER_SECTIONS:
        story.append(Paragraph(escape_pdf_text(section), styles["SectionTitle2"]))
        body = section_texts.get(section, "not clearly stated in transcripts")
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        for para in paragraphs:
            if para.startswith(section):
                continue
            if para.lstrip().startswith("- "):
                items = []
                for line in para.splitlines():
                    line = line.strip()
                    if line.startswith("- "):
                        items.append(ListItem(Paragraph(escape_pdf_text(line[2:]), styles["BodySmall"])))
                if items:
                    story.append(ListFlowable(items, bulletType="bullet", leftIndent=14))
            else:
                story.append(Paragraph(escape_pdf_text(para).replace("\n", "<br/>"), styles["BodySmall"]))
        story.append(Spacer(1, 8))

    def _page(canvas, doc_obj):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#666666"))
        canvas.drawRightString(A4[0] - 18 * mm, 10 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()

    doc.build(story, onFirstPage=_page, onLaterPages=_page)


def dossier_mode(channel_folder: str, settings: Dict[str, Any], if_exists: str | None = None, source: str | None = None) -> str:
    channel_folder = os.path.abspath(channel_folder)
    channel_name = os.path.basename(channel_folder.rstrip(os.sep))
    status(f"Building dossier for channel folder: {channel_folder}")

    report_txt_path, report_pdf_path = get_report_paths(channel_name, source=source)
    existence_mode = if_exists or settings.get("DOSSIER_IF_EXISTS", "skip")

    if not should_generate_report(report_txt_path, report_pdf_path, existence_mode):
        return report_pdf_path if os.path.exists(report_pdf_path) else report_txt_path

    records, _index_by_file, all_text = load_channel_data(channel_folder, source=source)
    if not records:
        raise RuntimeError("No transcript records found")

    analysis_root = analysis_root_for_source(channel_folder, source)
    if existence_mode == "replace":
        reset_analysis_outputs(channel_folder)
    per_video_notes_dir = os.path.join(analysis_root, "per_video_notes")
    os.makedirs(per_video_notes_dir, exist_ok=True)

    state = load_or_create_state(channel_name, channel_folder, analysis_root, len(records))
    done_files = set(state.get("processed_transcript_files", []))

    status(f"Transcript files loaded: {len(records)}")
    status(f"Already incorporated into dossier: {len(done_files)}")
    pending = [r for r in records if r["transcript_file"] not in done_files]
    status(f"Pending transcript files: {len(pending)}")
    render_every = max(1, int(settings.get("REPORT_RENDER_EVERY_N_VIDEOS", 1)))

    section_texts = save_incremental_outputs(channel_name, channel_folder, state, report_txt_path, analysis_root)

    progress_bar = tqdm(records, desc=f"Dossier {channel_name}", unit="video")
    processed_since_render = 0

    for rec in progress_bar:
        progress_bar.set_postfix_str(rec["transcript_file"][:60])

        if rec["transcript_file"] in done_files:
            continue

        note = extract_note_for_video(rec, settings, per_video_notes_dir)
        merge_video_note_into_state(state, rec, note)
        done_files.add(rec["transcript_file"])

        processed_since_render += 1
        if processed_since_render >= render_every:
            section_texts = save_incremental_outputs(channel_name, channel_folder, state, report_txt_path, analysis_root)
            processed_since_render = 0

    if processed_since_render > 0:
        section_texts = save_incremental_outputs(channel_name, channel_folder, state, report_txt_path, analysis_root)

    render_pdf(channel_name, channel_folder, section_texts, report_pdf_path)
    write_channel_manifests(channel_name, channel_folder, analysis_root, state, records, report_txt_path, report_pdf_path)
    audit = audit_channel_folder(channel_folder, write_report=True)
    status(f"Audit status: ok={audit.get('ok')} issues={len(audit.get('issues', []))} warnings={len(audit.get('warnings', []))}")
    status(f"TXT dossier created: {report_txt_path}")
    status(f"PDF dossier created: {report_pdf_path}")
    return report_pdf_path


def batch_dossiers(root: str, settings: Dict[str, Any], if_exists: str | None = None) -> None:
    creators_dir = os.path.join(root, "creators")
    if not os.path.exists(creators_dir):
        raise FileNotFoundError(f"Missing creators directory: {creators_dir}")

    folders = [
        os.path.join(creators_dir, d)
        for d in sorted(os.listdir(creators_dir))
        if os.path.isdir(os.path.join(creators_dir, d))
    ]

    status(f"Batch dossier mode: {len(folders)} creator folder(s)")
    for folder in folders:
        try:
            dossier_mode(folder, settings, if_exists=if_exists, source=source)
        except Exception as exc:
            status(f"Batch dossier failed for {folder}: {exc}")


#############################################
# Main
#############################################

def main() -> None:
    ensure_config_templates()
    ensure_prompt_dir()
    settings = load_settings()
    apply_runtime_paths(settings)
    os.makedirs(OUTPUT_ROOT, exist_ok=True)
    ensure_logs()

    parser = argparse.ArgumentParser(description="Trading transcript scraper + local incremental dossier builder")
    parser.add_argument("--mode", choices=["scrape", "dossier", "batch-dossiers", "batch_dossiers", "audit", "repair", "batch_repair", "simplify", "batch_simplify", "all"], default="scrape")
    parser.add_argument("--channel-folder", help="Path to a single channel folder inside dataset/creators")
    parser.add_argument("--root", default=OUTPUT_ROOT, help="Dataset root for batch modes")
    parser.add_argument("--if-exists", choices=["skip", "replace", "ask"], default=settings.get("DOSSIER_IF_EXISTS", "skip"))
    parser.add_argument("--source", choices=["raw", "simple"], default=normalize_source_name(None, settings))
    args = parser.parse_args()

    if args.mode == "scrape":
        scrape_mode(settings)
    elif args.mode == "simplify":
        folder = resolve_channel_folder(args.channel_folder, settings)
        result = simplify_channel_folder(folder, settings, overwrite=False)
        status(f"Simplified transcripts complete: {result['count']} file(s)")
    elif args.mode == "batch_simplify":
        batch_simplify_mode(args.root, settings, overwrite=False)
    elif args.mode == "dossier":
        folder = resolve_channel_folder(args.channel_folder, settings)
        dossier_mode(folder, settings, if_exists=args.if_exists, source=args.source)
    elif args.mode == "audit":
        folder = resolve_channel_folder(args.channel_folder, settings)
        report = audit_channel_folder(folder, write_report=True, source=args.source)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report.get("ok"):
            raise SystemExit(1)
    elif args.mode == "repair":
        folder = resolve_channel_folder(args.channel_folder, settings)
        report = repair_channel_folder(folder, settings, if_exists="replace", source=args.source)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        if not report.get("ok"):
            raise SystemExit(1)
    elif args.mode == "batch_repair":
        batch_repair_mode(args.root, settings, if_exists="replace", source=args.source)
    elif args.mode in {"batch-dossiers", "batch_dossiers"}:
        batch_dossiers_mode(args.root, settings, if_exists=args.if_exists, source=args.source)
    elif args.mode == "all":
        scrape_mode(settings)
        folder = resolve_channel_folder(args.channel_folder, settings)
        dossier_mode(folder, settings, if_exists=args.if_exists, source=args.source)
    else:
        raise SystemExit(f"Unsupported mode: {args.mode}")


if __name__ == "__main__":
    main()
