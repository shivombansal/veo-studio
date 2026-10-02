"""
Veo 3.1 Lite Studio — generate video with Google's Veo 3.1 Lite through
the OpenRouter Video Generation API, right from a Streamlit UI.

Run with:
    streamlit run veo_studio.py

Docs this app is built against:
    https://openrouter.ai/docs/guides/overview/multimodal/video-generation
"""

import base64
import time
import json
from datetime import datetime

import requests
import streamlit as st

# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

API_BASE = "https://openrouter.ai/api/v1"
VIDEOS_ENDPOINT = f"{API_BASE}/videos"

# Model choices and each one's supported values (per OpenRouter's
# /videos/models listing). Lite is cheaper/faster and is the default;
# full Veo 3.1 adds 1:1 aspect ratio support and generally higher quality
# at a higher cost.
MODEL_OPTIONS = {
    "Veo 3.1 Lite (cheaper, faster — default)": {
        "id": "google/veo-3.1-lite",
        "durations": [4, 5, 6, 7, 8],
        "resolutions": ["720p", "1080p"],
        "aspect_ratios": ["16:9", "9:16"],
        "price_note": "from $0.05/second — an 8s 1080p clip is roughly $0.40-$0.80.",
    },
    "Veo 3.1 (full quality)": {
        "id": "google/veo-3.1",
        "durations": [4, 6, 8],
        "resolutions": ["720p", "1080p"],
        "aspect_ratios": ["16:9", "9:16", "1:1"],
        "price_note": "from $0.50/second at 720p (higher at 1080p) — roughly 10x the Lite price.",
    },
}
DEFAULT_MODEL_LABEL = "Veo 3.1 Lite (cheaper, faster — default)"

DEFAULTS = {
    "duration": 8,
    "resolution": "1080p",
    "aspect_ratio": "9:16",
    "generate_audio": True,
    "person_generation": "allow",
    "poll_interval": 10,
}

st.set_page_config(page_title="Veo 3.1 Lite Studio", page_icon="🎬", layout="wide")

# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

if "job_id" not in st.session_state:
    st.session_state.job_id = None
if "video_bytes" not in st.session_state:
    st.session_state.video_bytes = None
if "last_status" not in st.session_state:
    st.session_state.last_status = None
if "last_error" not in st.session_state:
    st.session_state.last_error = None
if "last_cost" not in st.session_state:
    st.session_state.last_cost = None


def image_file_to_data_url(uploaded_file) -> str:
    """Convert a Streamlit UploadedFile (image) into a base64 data: URL,
    since OpenRouter's frame_images / input_references expect a URL string
    and most users won't have their reference images hosted anywhere."""
    raw = uploaded_file.getvalue()
    b64 = base64.b64encode(raw).decode("utf-8")
    mime = uploaded_file.type or "image/png"
    return f"data:{mime};base64,{b64}"


# --------------------------------------------------------------------------
# Sidebar — API key & generation defaults
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("🔑 API access")
    api_key = st.text_input(
        "OpenRouter API key",
        type="password",
        help=(
            "Paste your own OpenRouter API key (starts with sk-or-...). "
            "It is kept only in this browser session's memory — it is never "
            "saved to disk or sent anywhere except OpenRouter's API. "
            "Get one at https://openrouter.ai/settings/keys"
        ),
    )
    st.caption(
        "Veo 3.1 Lite costs from **$0.05/second** on OpenRouter — an 8s "
        "1080p clip is roughly $0.40-$0.80 depending on audio. Cost isn't "
        "shown until a job completes."
    )

    st.divider()
    st.header("🎛️ Model")
    model_label = st.selectbox(
        "Veo model",
        list(MODEL_OPTIONS.keys()),
        index=list(MODEL_OPTIONS.keys()).index(DEFAULT_MODEL_LABEL),
        help=(
            "Veo 3.1 Lite: Google's most cost-effective video model, built "
            "for high-volume iteration — fast and cheap, slightly lower "
            "ceiling on quality. Good default for testing prompts.\n\n"
            "Veo 3.1 (full): the full-quality tier — better detail, motion "
            "coherence and prompt adherence, also supports square (1:1) "
            "output, at a noticeably higher cost per second."
        ),
    )
    model_info = MODEL_OPTIONS[model_label]
    st.caption(f"Pricing: {model_info['price_note']}")

    st.divider()
    st.header("⚙️ Defaults")
    st.caption(
        "These pre-fill the generation form below. Change them here if you "
        "want a new baseline every time you open the app; you can still "
        "override any of them per-generation in the main panel."
    )

    default_duration = st.selectbox(
        "Default duration (seconds)",
        model_info["durations"],
        index=(
            model_info["durations"].index(DEFAULTS["duration"])
            if DEFAULTS["duration"] in model_info["durations"]
            else len(model_info["durations"]) - 1
        ),
        help="How long the generated clip should be, in seconds. Options depend on the model selected above.",
    )
    default_resolution = st.selectbox(
        "Default resolution",
        model_info["resolutions"],
        index=model_info["resolutions"].index(DEFAULTS["resolution"]),
        help="Output pixel resolution. 1080p looks sharper but costs more and takes slightly longer to render than 720p.",
    )
    default_aspect_ratio = st.selectbox(
        "Default aspect ratio",
        model_info["aspect_ratios"],
        index=(
            model_info["aspect_ratios"].index(DEFAULTS["aspect_ratio"])
            if DEFAULTS["aspect_ratio"] in model_info["aspect_ratios"]
            else 0
        ),
        help="16:9 = landscape/widescreen (YouTube, desktop). 9:16 = vertical/portrait (Reels, TikTok, Shorts). 1:1 = square (Veo 3.1 full only).",
    )
    default_audio = st.checkbox(
        "Generate audio by default",
        value=DEFAULTS["generate_audio"],
        help="Veo 3.1 Lite can generate synchronized sound effects, ambience, and dialogue alongside the video. Turn off for silent output (e.g. if you'll add your own music/voiceover in editing).",
    )
    default_poll_interval = st.slider(
        "Status poll interval (seconds)",
        min_value=5,
        max_value=60,
        value=DEFAULTS["poll_interval"],
        help="How often the app checks OpenRouter for job progress while a video is generating. Lower = more responsive UI, higher = fewer API calls. Generation typically takes 30 seconds to a few minutes.",
    )

    st.divider()
    st.caption(
        f"Selected model: **{model_info['id']}** — supports text-to-video, "
        "image-to-video, and reference-guided video."
    )

# --------------------------------------------------------------------------
# Main panel — generation form
# --------------------------------------------------------------------------

st.title("🎬 Veo Studio")
st.write(
    f"Generate a video with Google's **{model_label}** through the "
    "OpenRouter API, then preview and download the result — all from "
    "this page. Switch models any time from the sidebar."
)

st.subheader("1. Prompt")
prompt = st.text_area(
    "Prompt",
    height=110,
    placeholder=(
        "e.g. A slow cinematic push-in on a phone screen showing a rising "
        "sales chart, warm studio lighting, shallow depth of field, "
        "confident and energetic mood"
    ),
    help=(
        "A detailed text description of the video you want. Veo responds "
        "best to specifics: describe the subject, action, camera movement "
        "(e.g. 'slow pan', 'push-in', 'handheld'), lighting, and mood — "
        "not just the subject alone."
    ),
)

negative_prompt = st.text_input(
    "Negative prompt (optional)",
    value="",
    help=(
        "Things you explicitly do NOT want in the video, e.g. 'blurry, "
        "low quality, text artifacts, distorted hands'. This is passed "
        "through as a Google Vertex provider-specific option, so it only "
        "applies to Veo models."
    ),
)

st.subheader("2. Reference / seed images (optional)")
mode = st.radio(
    "Generation mode",
    ["Text-to-video (no image)", "Image-to-video (animate an image)", "Reference-to-video (style/character guidance)"],
    help=(
        "Text-to-video: Veo generates entirely from your prompt.\n\n"
        "Image-to-video: upload a first frame (and optionally a last "
        "frame) that the video will animate from/to.\n\n"
        "Reference-to-video: upload up to 3 images the model uses as "
        "loose visual guidance (style, character, product look) rather "
        "than exact frames."
    ),
)

frame_first_upload = None
frame_last_upload = None
reference_uploads = []

if mode == "Image-to-video (animate an image)":
    col1, col2 = st.columns(2)
    with col1:
        frame_first_upload = st.file_uploader(
            "First frame image (required for this mode)",
            type=["png", "jpg", "jpeg", "webp"],
            help="The video will start from this image and animate outward based on your prompt.",
        )
    with col2:
        frame_last_upload = st.file_uploader(
            "Last frame image (optional)",
            type=["png", "jpg", "jpeg", "webp"],
            help="If provided, Veo will animate a transition ending on this frame. Leave empty to let the model decide the ending freely.",
        )
elif mode == "Reference-to-video (style/character guidance)":
    reference_uploads = st.file_uploader(
        "Reference images (up to 3)",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        help=(
            "Upload 1-3 images (e.g. your product photo, a brand style "
            "board, or a character) for the model to use as visual "
            "guidance. These are not exact frames — the model reinterprets "
            "them according to your prompt."
        ),
    )
    if reference_uploads and len(reference_uploads) > 3:
        st.warning("Only the first 3 reference images will be used.")
        reference_uploads = reference_uploads[:3]

st.subheader("3. Output settings")
col1, col2, col3 = st.columns(3)
with col1:
    duration = st.selectbox(
        "Duration (seconds)",
        model_info["durations"],
        index=model_info["durations"].index(default_duration),
        help=f"Clip length in seconds. {model_label} supports: {model_info['durations']}.",
    )
with col2:
    resolution = st.selectbox(
        "Resolution",
        model_info["resolutions"],
        index=model_info["resolutions"].index(default_resolution),
        help="1080p is sharper and costs more per second than 720p.",
    )
with col3:
    aspect_ratio = st.selectbox(
        "Aspect ratio",
        model_info["aspect_ratios"],
        index=model_info["aspect_ratios"].index(default_aspect_ratio),
        help="9:16 for Reels/Shorts/TikTok (vertical). 16:9 for YouTube/landscape. 1:1 square (Veo 3.1 full only).",
    )

col4, col5 = st.columns(2)
with col4:
    generate_audio = st.checkbox(
        "Generate synchronized audio",
        value=default_audio,
        help="Adds AI-generated sound effects, ambience, and dialogue synced to the video. Turn off if you plan to add your own audio track in post-production.",
    )
with col5:
    person_generation = st.selectbox(
        "Person generation policy",
        ["allow", "allow_adult", "disallow"],
        index=["allow", "allow_adult", "disallow"].index(DEFAULTS["person_generation"]),
        help=(
            "Google Vertex-specific control over whether people can appear "
            "in the output. 'allow' permits people of any age, "
            "'allow_adult' restricts to adults only, 'disallow' excludes "
            "people entirely. Passed through as a provider-specific option."
        ),
    )

with st.expander("Advanced settings"):
    seed = st.text_input(
        "Seed (optional)",
        value="",
        help=(
            "An integer seed for more reproducible generations. Not all "
            "providers guarantee identical output for the same seed, but "
            "it can help when iterating on a prompt. Leave blank for a "
            "random seed each time."
        ),
    )
    size_override = st.text_input(
        "Exact pixel size override (optional, e.g. 1920x1080)",
        value="",
        help=(
            "If set, this takes priority over resolution + aspect ratio "
            "and requests an exact WIDTHxHEIGHT pixel size instead. Leave "
            "blank to use the resolution/aspect ratio fields above."
        ),
    )
    poll_interval = st.slider(
        "Poll interval for this run (seconds)",
        min_value=5,
        max_value=60,
        value=default_poll_interval,
        help="Overrides the sidebar default just for this generation.",
    )
    show_raw_response = st.checkbox(
        "Show raw API responses (debugging)",
        value=False,
        help="Displays the raw JSON OpenRouter returns at each step — useful if a generation fails and you want to see the exact error.",
    )

st.divider()

# --------------------------------------------------------------------------
# Build the request payload
# --------------------------------------------------------------------------


def build_payload():
    payload = {
        "model": model_info["id"],
        "prompt": prompt.strip(),
        "duration": duration,
        "generate_audio": generate_audio,
    }

    if size_override.strip():
        payload["size"] = size_override.strip()
    else:
        payload["resolution"] = resolution
        payload["aspect_ratio"] = aspect_ratio

    if seed.strip():
        try:
            payload["seed"] = int(seed.strip())
        except ValueError:
            st.warning("Seed must be an integer — ignoring it.")

    # frame / reference images
    if mode == "Image-to-video (animate an image)" and frame_first_upload is not None:
        frame_images = [
            {
                "type": "image_url",
                "image_url": {"url": image_file_to_data_url(frame_first_upload)},
                "frame_type": "first_frame",
            }
        ]
        if frame_last_upload is not None:
            frame_images.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image_file_to_data_url(frame_last_upload)},
                    "frame_type": "last_frame",
                }
            )
        payload["frame_images"] = frame_images

    elif mode == "Reference-to-video (style/character guidance)" and reference_uploads:
        payload["input_references"] = [
            {
                "type": "image_url",
                "image_url": {"url": image_file_to_data_url(f)},
            }
            for f in reference_uploads
        ]

    # Google Vertex provider-specific passthrough
    vertex_params = {"personGeneration": person_generation}
    if negative_prompt.strip():
        vertex_params["negativePrompt"] = negative_prompt.strip()
    payload["provider"] = {"options": {"google-vertex": {"parameters": vertex_params}}}

    return payload


# --------------------------------------------------------------------------
# Submit + poll + download logic
# --------------------------------------------------------------------------


def submit_job(payload, headers):
    resp = requests.post(VIDEOS_ENDPOINT, headers=headers, json=payload, timeout=60)
    if resp.status_code not in (200, 202):
        raise RuntimeError(f"Submit failed ({resp.status_code}): {resp.text}")
    return resp.json()


def poll_job(polling_url, headers):
    resp = requests.get(polling_url, headers=headers, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"Poll failed ({resp.status_code}): {resp.text}")
    return resp.json()


def download_video(content_url, headers):
    resp = requests.get(content_url, headers=headers, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"Download failed ({resp.status_code}): {resp.text}")
    return resp.content


generate_clicked = st.button("🎥 Generate video", type="primary", use_container_width=True)

if generate_clicked:
    st.session_state.video_bytes = None
    st.session_state.last_error = None
    st.session_state.last_cost = None

    if not api_key.strip():
        st.error("Add your OpenRouter API key in the sidebar first.")
    elif not prompt.strip():
        st.error("Write a prompt before generating.")
    elif mode == "Image-to-video (animate an image)" and frame_first_upload is None:
        st.error("Image-to-video mode needs at least a first frame image.")
    else:
        headers = {
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        }
        payload = build_payload()

        if show_raw_response:
            with st.expander("Request payload sent to OpenRouter", expanded=False):
                # don't dump full base64 data URLs into the UI
                debug_payload = json.loads(json.dumps(payload))
                for key in ("frame_images", "input_references"):
                    if key in debug_payload:
                        for item in debug_payload[key]:
                            item["image_url"]["url"] = item["image_url"]["url"][:60] + "...[truncated]"
                st.json(debug_payload)

        status_box = st.status("Submitting job to OpenRouter...", expanded=True)
        try:
            submit_result = submit_job(payload, headers)
            job_id = submit_result["id"]
            polling_url = submit_result["polling_url"]
            st.session_state.job_id = job_id
            status_box.write(f"Job submitted: `{job_id}`")

            if show_raw_response:
                status_box.json(submit_result)

            status = submit_result.get("status", "pending")
            attempt = 0
            while status in ("pending", "in_progress"):
                attempt += 1
                status_box.update(label=f"Generating video... (status: {status}, check #{attempt})")
                time.sleep(poll_interval)
                poll_result = poll_job(polling_url, headers)
                status = poll_result.get("status")
                status_box.write(f"[{datetime.now().strftime('%H:%M:%S')}] status: {status}")
                if show_raw_response:
                    status_box.json(poll_result)

            if status == "completed":
                content_url = poll_result["unsigned_urls"][0]
                status_box.update(label="Downloading video...")
                video_bytes = download_video(content_url, headers)
                st.session_state.video_bytes = video_bytes
                st.session_state.last_cost = poll_result.get("usage", {}).get("cost")
                status_box.update(label="Done!", state="complete")
            else:
                err = poll_result.get("error", "Unknown error")
                st.session_state.last_error = err
                status_box.update(label=f"Generation failed: {err}", state="error")

        except Exception as e:
            st.session_state.last_error = str(e)
            status_box.update(label=f"Error: {e}", state="error")

# --------------------------------------------------------------------------
# Result panel
# --------------------------------------------------------------------------

if st.session_state.last_error:
    st.error(f"Something went wrong: {st.session_state.last_error}")

if st.session_state.video_bytes:
    st.subheader("4. Result")
    st.video(st.session_state.video_bytes)

    model_tag = model_info["id"].replace("google/", "").replace(".", "").replace("-", "")
    filename = f"{model_tag}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    st.download_button(
        "⬇️ Download video (.mp4)",
        data=st.session_state.video_bytes,
        file_name=filename,
        mime="video/mp4",
        use_container_width=True,
    )

    if st.session_state.last_cost is not None:
        st.caption(f"This generation cost **${st.session_state.last_cost:.2f}** on your OpenRouter account.")

st.divider()
with st.expander("ℹ️ About the parameters used in this app"):
    st.markdown(
        """
- **Prompt**: the core text instruction. Be specific about subject, action, camera work, lighting, and mood.
- **Negative prompt**: things to steer away from (Veo/Vertex-specific, passed via `provider.options`).
- **Duration**: 4-8 seconds, in whole seconds.
- **Resolution**: `720p` or `1080p`.
- **Aspect ratio**: `16:9` (landscape) or `9:16` (portrait) for Veo 3.1 Lite.
- **Size override**: an exact `WIDTHxHEIGHT` value that replaces resolution + aspect ratio if set.
- **Generate audio**: toggles Veo's native synchronized audio generation.
- **Person generation**: Google Vertex passthrough parameter controlling whether people can appear (`allow`, `allow_adult`, `disallow`).
- **Seed**: optional integer for more reproducible runs (not guaranteed identical).
- **Frame images**: first/last frame for image-to-video generation.
- **Reference images**: up to 3 images used as loose style/subject guidance rather than exact frames.
- **Poll interval**: how often this app checks OpenRouter for job status while waiting.

Full reference: https://openrouter.ai/docs/guides/overview/multimodal/video-generation
        """
    )
