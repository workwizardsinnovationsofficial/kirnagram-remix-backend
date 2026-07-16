from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import StreamingResponse
from typing import Optional
from datetime import datetime
from io import BytesIO
import base64
import traceback
import requests
from urllib.parse import urlparse
from PIL import Image
from bson import ObjectId
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, InvalidArgument, Unauthenticated

from firebase import verify_firebase_token
from database import db
from r2 import s3, BUCKET_NAME, PUBLIC_BASE
from config import (
    GEMINI_API_KEY,
    GEMINI_IMAGE_MODEL,
    GEMINI_FALLBACK_MODE,
    OPENAI_API_KEY,
    OPENAI_IMAGE_MODEL,
)
from credits import ensure_wallet, record_transaction

router = APIRouter(prefix="/remix", tags=["Remix"])


def normalize_variable_key(value: str) -> str:
    import re

    return re.sub(r"[^a-zA-Z0-9_]", "", re.sub(r"\s+", "_", str(value or "").strip())).lower()

# ==========================================================
# MY REMIX HISTORY
# ==========================================================
@router.get("/my-remixes")
async def get_my_remixes(authorization: str = Header(...)):
    user_id = get_user_id(authorization)

    remixes = await db.ai_creator_remixes.find(
        {"user_id": user_id}
    ).sort("created_at", -1).to_list(length=None)

    result = []

    for remix in remixes:
        result.append({
            "id": str(remix["_id"]),
            "image_url": remix.get("output_image"),
            "prompt_id": remix.get("prompt_id"),
            "ratio": remix.get("ratio"),
            "payout_per_remix": int(remix.get("payout_per_remix", 1) or 1),
            "created_at": remix.get("created_at")
        })

    response_data = {
        "total": len(result),
        "remixes": result
    }
    print(f"📤 [get_my_remixes] RETURNING RESPONSE: total={response_data['total']} remixes for user_id={user_id}")
    return response_data

# ==========================================================
# GET SPECIFIC REMIX
# ==========================================================
@router.get("/{remix_id}")
async def get_remix(remix_id: str, authorization: str = Header(...)):
    user_id = get_user_id(authorization)

    if not ObjectId.is_valid(remix_id):
        raise HTTPException(status_code=400, detail="Invalid remix id")

    remix = await db.ai_creator_remixes.find_one({"_id": ObjectId(remix_id)})
    if not remix:
        raise HTTPException(status_code=404, detail="Remix not found")

    # Determine generation status based on output_image presence
    # - "completed" if output_image exists
    # - "processing" if output_image is None/missing
    has_output = bool(remix.get("output_image"))
    status = "completed" if has_output else "processing"

    # Build response with all public remix data
    response = {
        "id": str(remix["_id"]),
        "image_url": remix.get("output_image"),
        "source_image": remix.get("source_image"),
        "prompt_id": remix.get("prompt_id"),
        "ratio": remix.get("ratio"),
        "model": remix.get("model"),
        "quality": remix.get("quality"),
        "credits_used": remix.get("credits_used"),
        "payout_per_remix": int(remix.get("payout_per_remix", 1) or 1),
        "review_rating": remix.get("review_rating"),
        "review_comment": remix.get("review_comment"),
        "review_improvement": remix.get("review_improvement"),
        "review_submitted_at": remix.get("review_submitted_at"),
        "created_at": remix.get("created_at"),
        "status": status,  # 👈 NEW: Include generation status
        "is_owner": remix.get("user_id") == user_id,  # 👈 NEW: Is current user the owner?
    }

    # Add owner info if available (name, avatar for display)
    owner_id = remix.get("user_id")
    if owner_id:
        response["owner_id"] = owner_id
        # Optionally fetch owner name/info from users collection if needed
        # For now, frontend can fetch this separately via /profile/user/{owner_id}

    print(f"📤 [get_remix] RETURNING RESPONSE: id={response['id']}, status={response['status']}, is_owner={response['is_owner']}, has_image={bool(response.get('image_url'))}")
    return response


@router.post("/{remix_id}/review")
async def submit_remix_review(
    remix_id: str,
    rating: str = Form(...),
    comment: Optional[str] = Form(None),
    improvement: Optional[str] = Form(None),
    authorization: str = Header(...)
):
    user_id = get_user_id(authorization)

    if not ObjectId.is_valid(remix_id):
        raise HTTPException(status_code=400, detail="Invalid remix id")

    normalized_rating = str(rating or "").strip().lower()
    if normalized_rating not in {"good", "bad"}:
        raise HTTPException(status_code=400, detail="Rating must be 'good' or 'bad'")

    remix = await db.ai_creator_remixes.find_one({"_id": ObjectId(remix_id)})
    if not remix:
        raise HTTPException(status_code=404, detail="Remix not found")

    if remix.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if normalized_rating == "bad" and not str(improvement or "").strip():
        raise HTTPException(status_code=400, detail="Improvement feedback is required for bad rating")

    review_doc = {
        "review_rating": normalized_rating,
        "review_comment": str(comment or "").strip() or None,
        "review_improvement": str(improvement or "").strip() or None,
        "review_submitted_at": datetime.utcnow(),
    }

    await db.ai_creator_remixes.update_one(
        {"_id": ObjectId(remix_id), "user_id": user_id},
        {"$set": review_doc}
    )

    response_data = {
        "success": True,
        "id": remix_id,
        **review_doc,
    }
    print(f"📤 [submit_remix_review] RETURNING RESPONSE: success=True, id={remix_id}, rating={normalized_rating}")
    return response_data


# ==========================================================
# GET PROMPT DETAILS
# ==========================================================
@router.get("/prompt/{prompt_id}")
async def get_prompt(prompt_id: str, authorization: str = Header(...)):
    get_user_id(authorization)  # Just validate token

    if not ObjectId.is_valid(prompt_id):
        raise HTTPException(status_code=400, detail="Invalid prompt id")

    prompt = await db.ai_creator_prompts.find_one({"_id": ObjectId(prompt_id)})
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    status = str(prompt.get("status") or "").lower()
    if prompt.get("is_deleted") or status not in {"approved", "delete_requested"}:
        raise HTTPException(status_code=404, detail="Prompt not found or not approved")

    response_data = {
        "_id": str(prompt["_id"]),
        "unit_id": prompt.get("unit_id"),
        "style_name": prompt.get("style_name"),
        "prompt_description": prompt.get("prompt_description"),
        "prompt_template": prompt.get("prompt_template"),
        "prompt_variables": prompt.get("prompt_variables", []),
        "description": prompt.get("description"),
        "prompt": prompt.get("prompt"),
        "prompt_text": prompt.get("prompt_text"),
        "ai_model": prompt.get("ai_model"),
        "image_url": prompt.get("image_url"),
        "sample_image_url": prompt.get("sample_image_url"),
        "sample_image_urls": prompt.get("sample_image_urls", []),
        "reference_correct_image_urls": prompt.get("reference_correct_image_urls", []),
        "reference_wrong_image_urls": prompt.get("reference_wrong_image_urls", []),
        "tags": prompt.get("tags", []),
        "aspect_ratio": prompt.get("aspect_ratio"),
        "burn_credits": prompt.get("burn_credits", 3),
        "payout_per_remix": prompt.get("payout_per_remix", 1),
        "remix_count": prompt.get("remix_count", 0),
        "status": prompt.get("status"),
        "created_at": prompt.get("created_at")
    }
    print(f"📤 [get_prompt] RETURNING RESPONSE: id={response_data['_id']}, aspect_ratio={response_data.get('aspect_ratio')}, burn_credits={response_data.get('burn_credits')}")
    return response_data

# ==========================================================
# GET REMIXES FOR A PROMPT
# ==========================================================
@router.get("/prompt/{prompt_id}/remixes")
async def get_prompt_remixes(prompt_id: str, authorization: str = Header(...)):
    get_user_id(authorization)  # Just validate token

    if not ObjectId.is_valid(prompt_id):
        raise HTTPException(status_code=400, detail="Invalid prompt id")

    prompt = await db.ai_creator_prompts.find_one({"_id": ObjectId(prompt_id)})
    if not prompt:
        raise HTTPException(status_code=404, detail="Prompt not found")

    status = str(prompt.get("status") or "").lower()
    if prompt.get("is_deleted") or status not in {"approved", "delete_requested"}:
        raise HTTPException(status_code=404, detail="Prompt not found or not approved")

    remixes = await db.ai_creator_remixes.find(
        {"prompt_id": prompt_id}
    ).sort("created_at", -1).to_list(length=None)

    result = []
    for remix in remixes:
        result.append({
            "id": str(remix["_id"]),
            "image_url": remix.get("output_image"),
            "user_id": remix.get("user_id"),
            "ratio": remix.get("ratio"),
            "model": remix.get("model"),
            "quality": remix.get("quality"),
            "created_at": remix.get("created_at")
        })

    response_data = {
        "total": len(result),
        "remixes": result
    }
    print(f"📤 [get_prompt_remixes] RETURNING RESPONSE: total={response_data['total']} remixes for prompt_id={prompt_id}")
    return response_data

# ============================================================
# AUTH
# ============================================================
def get_user_id(authorization: str) -> str:
    if not authorization or " " not in authorization:
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.split(" ")[1]
    decoded = verify_firebase_token(token)
    return decoded["uid"]


def add_watermark_pil(image: Image.Image, logo_path: str = None, text: str = "KIRANAGRAM") -> Image.Image:
    from PIL import ImageDraw, ImageFont

    width, height = image.size
    is_landscape = width > height
    
    # Keep landscape watermark smaller while preserving visibility.
    if is_landscape:
        # 16:9 landscape
        font_size = max(16, int(min(width, height) * 0.028))
    else:
        # 9:16 portrait
        font_size = max(24, int(min(width, height) * 0.04))

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except:
        font = ImageFont.load_default()

    # Create transparent layer for watermark
    txt_layer = Image.new("RGBA", image.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt_layer)

    # Measure text
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Slightly tighter corner placement for smaller landscape text.
    if is_landscape:
        padding = max(12, int(min(width, height) * 0.018))
    else:
        padding = max(20, int(min(width, height) * 0.025))

    # Bottom right position
    x = width - text_width - padding
    y = height - text_height - padding

    # Strong shadow + visible text
    shadow_offset = 2
    shadow_opacity = 200  # Much darker (was 60-70)
    text_opacity = 235    # Much brighter (was 65-75)
    
    # Draw dark shadow for contrast
    draw.text(
        (x + shadow_offset, y + shadow_offset),
        text,
        font=font,
        fill=(0, 0, 0, shadow_opacity)
    )

    # Main watermark text - bright white
    draw.text(
        (x, y),
        text,
        font=font,
        fill=(255, 255, 255, text_opacity)
    )

    return Image.alpha_composite(image, txt_layer)

def crop_to_ratio(image: Image.Image, ratio: str) -> Image.Image:
    width, height = image.size

    if ratio == "16:9":
        target_ratio = 16 / 9
    elif ratio == "9:16":
        target_ratio = 9 / 16
    else:
        return image

    current_ratio = width / height

    if current_ratio > target_ratio:
        # Crop width
        new_width = int(height * target_ratio)
        offset = (width - new_width) // 2
        return image.crop((offset, 0, offset + new_width, height))
    else:
        # Crop height
        new_height = int(width / target_ratio)
        offset = (height - new_height) // 2
        return image.crop((0, offset, width, offset + new_height))

# Resize image to match the requested aspect ratio
    # REMOVED resize_to_ratio
# ============================================================
def convert_to_png(upload: UploadFile) -> bytes:
    raw = upload.file.read()
    try:
        img = Image.open(BytesIO(raw)).convert("RGBA")
        out = BytesIO()
        img.save(out, format="PNG")
        out.seek(0)
        return out.read()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file")


def _load_pil_image(upload: UploadFile) -> Image.Image:
    raw = upload.file.read()
    try:
        return Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid image file")


def _load_pil_from_base64(image_base64: str) -> Image.Image:
    if not image_base64:
        raise HTTPException(status_code=400, detail="Missing base64 image data")

    prefix = "base64,"
    if prefix in image_base64:
        image_base64 = image_base64.split(prefix, 1)[1]

    try:
        raw = base64.b64decode(image_base64)
        return Image.open(BytesIO(raw)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data")


def _pil_to_png_bytes(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="PNG")
    out.seek(0)
    return out.read()


def fetch_image_base64(url: str) -> str:
    try:
        res = requests.get(url, timeout=60)
    except Exception:
        raise HTTPException(status_code=400, detail="Failed to fetch source image")

    if res.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch source image")

    return base64.b64encode(res.content).decode("utf-8")


# ============================================================
# PROMPT
# ============================================================
def build_prompt(style: str, description: str, ratio: str) -> str:
    """Build fallback prompt from style and description fields."""
    parts = []
    if style:
        parts.append(f"Style: {style}")
    if description:
        parts.append(f"Description: {description}")
    return " ".join(parts) or "Create a stylized image"


def render_prompt_template(template: str, values: dict) -> str:
    import re

    normalized_values = {}
    for key, value in (values or {}).items():
        normalized_key = normalize_variable_key(key)
        if normalized_key:
            normalized_values[normalized_key] = str(value or "")

    def resolve_value(raw_key: str) -> str:
        direct = values.get(raw_key, "") if isinstance(values, dict) else ""
        if str(direct or "").strip():
            return str(direct).strip()

        normalized_key = normalize_variable_key(raw_key)
        if not normalized_key:
            return ""

        normalized_direct = values.get(normalized_key, "") if isinstance(values, dict) else ""
        if str(normalized_direct or "").strip():
            return str(normalized_direct).strip()

        return str(normalized_values.get(normalized_key, "") or "").strip()

    def replacer(match):
        key = (match.group(1) or "").strip()
        return resolve_value(key)

    # Support legacy {{var}} and current {var} tokens.
    rendered = re.sub(r"{{\s*([^{}]+?)\s*}}", replacer, template or "")
    rendered = re.sub(r"\{\s*([^{}]+?)\s*\}", replacer, rendered)
    return " ".join(rendered.split()).strip()


def build_identity_preserving_prompt(user_prompt: str, ratio: str) -> str:
    safety_prefix = (
        "Use the uploaded image as the primary identity reference. "
        "Preserve face identity, facial structure, skin tone, eye shape, hairstyle direction, and natural expression. "
        "Do not swap gender, do not change age drastically, and do not deform facial features. "
        "Keep realistic facial proportions and clean eyes, nose, lips, jawline, and ears. "
        "Maintain original pose and camera angle as much as possible while applying style. "
        f"Target output aspect ratio: {ratio}."
    )
    return f"{safety_prefix}\n\nStyle instructions: {user_prompt}".strip()


def build_variable_lock_instructions(values: dict) -> str:
    if not isinstance(values, dict):
        return ""

    pairs = []
    color_pairs = []

    for raw_key, raw_value in values.items():
        key = normalize_variable_key(raw_key)
        value = str(raw_value or "").strip()
        if not key or not value:
            continue

        pairs.append((key, value))
        if any(token in key for token in ("color", "colour", "colur")):
            color_pairs.append((key, value))

    if not pairs:
        return ""

    lines = [
        "Variable locks (high priority): Apply these values exactly as provided.",
        "Do not keep placeholders. Do not substitute with defaults.",
    ]

    for key, value in pairs:
        lines.append(f"- {key}: {value}")

    if color_pairs:
        lines.append(
            "Color lock: If a variable specifies a color for an item (e.g., shirt), keep that item in the exact requested color and do not recolor it due to global style grading."
        )

    return "\n".join(lines)


# ============================================================
# DOWNLOAD REMIX
# ============================================================
@router.get("/download/{remix_id}")
async def download_remix(remix_id: str, authorization: str = Header(...)):
    user_id = get_user_id(authorization)

    remix = await db.ai_creator_remixes.find_one({
        "_id": ObjectId(remix_id),
        "user_id": user_id
    })

    if not remix:
        raise HTTPException(status_code=404, detail="Remix not found")

    image_url = remix.get("output_image")

    # 🔥 Count download
    await db.ai_creator_remixes.update_one(
        {"_id": ObjectId(remix_id)},
        {"$inc": {"download_count": 1}}
    )

    # 🔥 Properly fetch full image content
    response = requests.get(image_url)

    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to fetch image")

    return StreamingResponse(
        BytesIO(response.content),
        media_type="image/png",
        headers={
            "Content-Disposition": "attachment; filename=kirnagram-remix.png"
        }
    )


def _public_url_to_r2_key(url: Optional[str]) -> Optional[str]:
    if not url:
        return None

    parsed = urlparse(url)
    path = (parsed.path or "").lstrip("/")
    public_base_path = urlparse(PUBLIC_BASE).path.lstrip("/")

    if public_base_path and path.startswith(f"{public_base_path}/"):
        return path[len(public_base_path) + 1:]

    return path or None


@router.delete("/{remix_id}")
async def delete_remix(remix_id: str, authorization: str = Header(...)):
    user_id = get_user_id(authorization)

    if not ObjectId.is_valid(remix_id):
        raise HTTPException(status_code=400, detail="Invalid remix id")

    remix_object_id = ObjectId(remix_id)
    remix = await db.ai_creator_remixes.find_one({
        "_id": remix_object_id,
        "user_id": user_id
    })

    if not remix:
        raise HTTPException(status_code=404, detail="Remix not found")

    prompt_id = remix.get("prompt_id")

    # Best-effort object cleanup in R2.
    for image_field in ("source_image", "output_image"):
        key = _public_url_to_r2_key(remix.get(image_field))
        if not key:
            continue
        try:
            s3.delete_object(Bucket=BUCKET_NAME, Key=key)
        except Exception as cleanup_error:
            print(f"R2 delete failed for {key}: {cleanup_error}")

    await db.ai_creator_remixes.delete_one({"_id": remix_object_id, "user_id": user_id})

    # Keep prompt remix counters in sync even with legacy prompt_id formats.
    prompt_matchers = [{"remixes": remix_id}]
    prompt_id_str = ""

    if isinstance(prompt_id, ObjectId):
        prompt_matchers.append({"_id": prompt_id})
        prompt_id_str = str(prompt_id)
    elif isinstance(prompt_id, str) and prompt_id:
        prompt_matchers.append({"unit_id": prompt_id})
        prompt_id_str = prompt_id
        if ObjectId.is_valid(prompt_id):
            prompt_matchers.append({"_id": ObjectId(prompt_id)})

    prompt_doc = await db.ai_creator_prompts.find_one({"$or": prompt_matchers})
    if prompt_doc:
        prompt_oid = prompt_doc.get("_id")
        prompt_unit_id = prompt_doc.get("unit_id")

        await db.ai_creator_prompts.update_one(
            {"_id": prompt_oid},
            {"$pull": {"remixes": remix_id}}
        )

        prompt_ids_for_count = {str(prompt_oid)}
        if prompt_id_str:
            prompt_ids_for_count.add(prompt_id_str)
        if isinstance(prompt_unit_id, str) and prompt_unit_id:
            prompt_ids_for_count.add(prompt_unit_id)

        total_remix_count = await db.ai_creator_remixes.count_documents(
            {"prompt_id": {"$in": list(prompt_ids_for_count)}}
        )

        await db.ai_creator_prompts.update_one(
            {"_id": prompt_oid},
            {"$set": {"remix_count": max(0, int(total_remix_count))}}
        )

    return {"success": True, "message": "Remix deleted"}


# ============================================================
# GEMINI IMAGE GENERATION (SAFE)
# ============================================================
def generate_with_gemini(prompt: str, image_url: str) -> bytes:
    # ✅ STEP 1: Validate API key is configured
    if not GEMINI_API_KEY:
        print("❌ [generate_with_gemini] GEMINI_API_KEY not configured")
        raise HTTPException(status_code=500, detail="Gemini API key not configured")

    print("🔷 [generate_with_gemini] Configuring Gemini API")
    # ✅ STEP 2: Configure the API (this doesn't validate, only sets key)
    try:
        genai.configure(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"❌ [generate_with_gemini] Failed to configure Gemini: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to configure Gemini API")

    # ✅ STEP 3: Validate model is configured
    model_name = (GEMINI_IMAGE_MODEL or "").strip()
    if not model_name:
        print("❌ [generate_with_gemini] GEMINI_IMAGE_MODEL not configured")
        raise HTTPException(status_code=500, detail="Gemini model not configured")
    
    resolved_model = model_name if model_name.startswith("models/") else f"models/{model_name}"
    print(f"🔷 [generate_with_gemini] Using model: {resolved_model}")
    
    # ✅ STEP 4: Create model instance
    try:
        model = genai.GenerativeModel(resolved_model)
    except Exception as e:
        print(f"❌ [generate_with_gemini] Failed to create model: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to create Gemini model")

    # ✅ STEP 5: Fetch image
    print("🔷 [generate_with_gemini] Fetching image from URL")
    image_base64 = fetch_image_base64(image_url)

    # ✅ STEP 6: Generate content
    print(f"🔷 [generate_with_gemini] Calling generate_content (prompt length: {len(prompt)})")
    try:
        response = model.generate_content(
            [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_base64
                            }
                        }
                    ]
                }
            ]
        )
    except ResourceExhausted as e:
        # Quota exceeded
        print(f"⚠️  [generate_with_gemini] Quota exceeded: {str(e)}")
        raise HTTPException(
            status_code=429,
            detail="AI quota exceeded. Please try again later."
        )
    except (InvalidArgument, Unauthenticated) as e:
        # Invalid API key or authentication issue
        error_msg = str(e)
        print(f"❌ [generate_with_gemini] API Authentication Error: {error_msg}")
        if "API_KEY_INVALID" in error_msg or "Invalid API key" in error_msg:
            raise HTTPException(
                status_code=500,
                detail="Gemini API key is invalid or has expired. Please check the API key configuration."
            )
        elif "not authorized" in error_msg.lower():
            raise HTTPException(
                status_code=500,
                detail="Gemini API key is not authorized. Please verify the API key has Gemini API enabled."
            )
        else:
            raise HTTPException(
                status_code=500,
                detail=f"Gemini API authentication failed: {error_msg}"
            )
    except Exception as e:
        # Generic error
        print(f"❌ [generate_with_gemini] Unexpected error: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"Gemini image generation failed: {str(e)}"
        )

    # ✅ STEP 7: Extract image from response
    print("🔷 [generate_with_gemini] Extracting image from response")
    for candidate in response.candidates or []:
        for part in candidate.content.parts or []:
            if hasattr(part, "inline_data") and part.inline_data:
                data = part.inline_data.data
                decoded = base64.b64decode(data) if isinstance(data, str) else data
                print(f"✅ [generate_with_gemini] Successfully extracted image ({len(decoded)} bytes)")
                return decoded

    print("❌ [generate_with_gemini] Response did not contain image data")
    raise HTTPException(status_code=500, detail="Gemini did not return image. Response structure unexpected.")


def _map_ratio_to_size(ratio: str) -> str:
    ratio = (ratio or "").strip()

    if ratio == "1:1":
        return "1024x1024"
    elif ratio == "16:9":
        return "1536x1024"
    elif ratio == "9:16":
        return "1024x1536"

    return "1024x1024"


def _resolve_openai_edit_model() -> str:
    configured = (OPENAI_IMAGE_MODEL or "").strip()
    return configured or "gpt-image-1"


def generate_with_openai(prompt: str, ratio: str, source_png: bytes, quality: str = "medium") -> bytes:
    from openai import OpenAI

    if not OPENAI_API_KEY:
        print("❌ [generate_with_openai] OpenAI API key not configured")
        raise HTTPException(status_code=500, detail="OpenAI API key not configured")

    try:
        print("🎬 [generate_with_openai] Starting OpenAI generation")
        
        size = _map_ratio_to_size(ratio)
        print(f"📐 [generate_with_openai] Mapped ratio '{ratio}' to size '{size}'")
        
        model = _resolve_openai_edit_model()
        print(f"🤖 [generate_with_openai] Resolved OpenAI model: {model}")

        # Clamp quality to OpenAI-supported values.
        requested_quality = (quality or "").strip().lower()
        output_quality = requested_quality if requested_quality in {"low", "medium", "high", "auto"} else "medium"
        print(f"📊 [generate_with_openai] Quality: requested='{quality}', output='{output_quality}'")

        print("🔑 [generate_with_openai] Creating OpenAI client")
        client = OpenAI(api_key=OPENAI_API_KEY)

        print(f"📦 [generate_with_openai] Creating BytesIO object from source_png ({len(source_png)} bytes)")
        source_file = BytesIO(source_png)
        source_file.name = "source.png"

        print(f"🚀 [generate_with_openai] Calling client.images.edit with model={model}, size={size}, quality={output_quality}")
        result = client.images.edit(
            model=model,
            image=source_file,  # ✅ FIXED: Was [source_file] (list), should be source_file (file object)
            prompt=prompt,
            size=size,
            quality=output_quality,
            input_fidelity="high",
        )

        print("✅ [generate_with_openai] OpenAI API call succeeded")
        
        b64 = result.data[0].b64_json
        print(f"🖼️ [generate_with_openai] Received base64 image ({len(b64)} chars)")
        
        output_bytes = base64.b64decode(b64)
        print(f"✅ [generate_with_openai] Decoded base64 to {len(output_bytes)} bytes")
        
        return output_bytes

    except Exception as e:
        print(f"❌ [generate_with_openai] Error: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"OpenAI failed: {str(e)}")
# ============================================================
# API: GENERATE REMIX
# ============================================================
# SIMPLIFIED: No complex prompt manipulation
# Just: image + user's raw prompt → OpenAI/Gemini → watermark → store


@router.post("/test-gemini")
async def test_gemini(
    prompt_text: str = Form(...),
    image: UploadFile = File(...),
    authorization: str = Header(...)
):
    # Debug-only endpoint to verify Gemini output.
    user_id = get_user_id(authorization)
    print("GEMINI_TEST_REQUEST:", {
        "user_id": user_id,
        "image_filename": getattr(image, "filename", None),
        "image_content_type": getattr(image, "content_type", None),
    })

    source_png = convert_to_png(image)
    ts = int(datetime.utcnow().timestamp())

    source_key = f"remix/test/{user_id}/{ts}.png"
    s3.upload_fileobj(
        BytesIO(source_png),
        BUCKET_NAME,
        source_key,
        ExtraArgs={"ContentType": "image/png"}
    )
    source_url = f"{PUBLIC_BASE}/{source_key}"

    output_bytes = generate_with_gemini(prompt_text, source_url)
    return StreamingResponse(BytesIO(output_bytes), media_type="image/png")


@router.post("/generate")
async def generate_remix(
    prompt_id: str = Form(...),
    ratio: str = Form("1:1"),
    quality: Optional[str] = Form(None),
    model: Optional[str] = Form(None),
    prompt_text: Optional[str] = Form(None),
    variable_values_json: Optional[str] = Form(None),
    image: UploadFile = File(...),
    authorization: str = Header(...)
):
    # ✅ STEP 1: Get user ID from token
    try:
        print("🔐 [generate_remix] Step 1: Verifying Firebase token")
        user_id = get_user_id(authorization)
        print(f"✅ [generate_remix] Step 1 SUCCESS: user_id={user_id}")
    except Exception as e:
        print(f"❌ [generate_remix] Step 1 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=401, detail="Authentication failed")

    # ✅ Log request details
    print("📥 [generate_remix] Request details:", {
        "user_id": user_id,
        "prompt_id": prompt_id,
        "ratio": ratio,
        "requested_model": (model or "").lower(),
        "image_filename": getattr(image, "filename", None),
        "image_content_type": getattr(image, "content_type", None),
    })

    # ✅ STEP 2: Validate prompt_id format
    try:
        print("🔍 [generate_remix] Step 2: Validating prompt ID format")
        if not ObjectId.is_valid(prompt_id):
            raise HTTPException(status_code=400, detail="Invalid prompt id")
        print(f"✅ [generate_remix] Step 2 SUCCESS: prompt_id is valid")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [generate_remix] Step 2 FAILED: {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid prompt id format")

    # ✅ STEP 3: Fetch prompt from database
    try:
        print("🗄️ [generate_remix] Step 3: Fetching prompt from database")
        prompt = await db.ai_creator_prompts.find_one({"_id": ObjectId(prompt_id)})
        if not prompt:
            print(f"❌ [generate_remix] Step 3 FAILED: Prompt not found")
            raise HTTPException(status_code=404, detail="Prompt not found")
        print(f"✅ [generate_remix] Step 3 SUCCESS: Found prompt: {prompt.get('_id')}")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [generate_remix] Step 3 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to fetch prompt from database")

    # ✅ STEP 4: Validate prompt status
    try:
        print("📋 [generate_remix] Step 4: Validating prompt status")
        prompt_status = str(prompt.get("status") or "").lower()
        if prompt.get("is_deleted") or prompt_status not in {"approved", "delete_requested"}:
            print(f"❌ [generate_remix] Step 4 FAILED: Prompt not approved (status={prompt_status}, deleted={prompt.get('is_deleted')})")
            raise HTTPException(status_code=404, detail="Prompt not found or not approved")
        print(f"✅ [generate_remix] Step 4 SUCCESS: Prompt approved (status={prompt_status})")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [generate_remix] Step 4 FAILED: {type(e).__name__}: {str(e)}")
        raise HTTPException(status_code=404, detail="Prompt validation failed")

    # ✅ STEP 5: Resolve AI model
    try:
        print("🤖 [generate_remix] Step 5: Resolving AI model")
        prompt_ai_model = (prompt.get("ai_model") or "").lower()
        requested_model = (model or "").lower()
        if prompt_ai_model == "gemini":
            resolved_model = "gemini"
        elif prompt_ai_model == "chatgpt":
            resolved_model = "chatgpt"
        elif prompt_ai_model == "both":
            resolved_model = requested_model if requested_model in {"chatgpt", "gemini"} else "chatgpt"
        else:
            resolved_model = requested_model if requested_model in {"chatgpt", "gemini"} else "chatgpt"
        print(f"✅ [generate_remix] Step 5 SUCCESS: prompt_ai_model={prompt_ai_model}, resolved_model={resolved_model}")
    except Exception as e:
        print(f"❌ [generate_remix] Step 5 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to resolve AI model")

    # ✅ STEP 6: Resolve quality mapping
    try:
        print("📊 [generate_remix] Step 6: Resolving quality mapping")
        quality_aliases = {
            "gemini": {"low": "fast", "medium": "standard", "high": "ultra"},
            "chatgpt": {"fast": "low", "standard": "medium", "ultra": "high"},
        }
        normalized_quality = (quality or "").strip().lower()
        mapped_quality = quality_aliases.get(resolved_model, {}).get(normalized_quality, normalized_quality)
        print(f"✅ [generate_remix] Step 6 SUCCESS: normalized_quality={normalized_quality}, mapped_quality={mapped_quality}")
    except Exception as e:
        print(f"❌ [generate_remix] Step 6 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to resolve quality")

    # ✅ STEP 7: Calculate burn cost
    try:
        print("💳 [generate_remix] Step 7: Calculating burn cost")
        burn_cost = int(prompt.get("burn_credits", 3) or 3)
        print(f"✅ [generate_remix] Step 7 SUCCESS: burn_cost={burn_cost}")
    except Exception as e:
        print(f"❌ [generate_remix] Step 7 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to calculate burn cost")

    # ✅ STEP 8: Check user credits
    try:
        print("💰 [generate_remix] Step 8: Checking user credits")
        wallet = await ensure_wallet(user_id)
        current_balance = int(wallet.get("balance", 0) or 0)
        print(f"💳 [generate_remix] Wallet check: balance={current_balance}, required={burn_cost}")
        if current_balance < burn_cost:
            print(f"❌ [generate_remix] Step 8 FAILED: Insufficient credits ({current_balance} < {burn_cost})")
            raise HTTPException(status_code=400, detail=f"Not enough credits. Required: {burn_cost}, Available: {current_balance}")
        print(f"✅ [generate_remix] Step 8 SUCCESS: User has enough credits")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [generate_remix] Step 8 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to check user credits")

    # ✅ STEP 9: Convert image to PNG
    try:
        print("🖼️ [generate_remix] Step 9: Converting image to PNG")
        source_png = convert_to_png(image)
        print(f"✅ [generate_remix] Step 9 SUCCESS: Image converted to PNG ({len(source_png)} bytes)")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [generate_remix] Step 9 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=400, detail="Failed to convert image to PNG")

    # ✅ STEP 10: Upload source image to R2
    try:
        print("☁️ [generate_remix] Step 10: Uploading source image to R2 (Cloudflare)")
        ts = int(datetime.utcnow().timestamp())
        source_key = f"remix/source/{user_id}/{ts}.png"
        s3.upload_fileobj(
            BytesIO(source_png),
            BUCKET_NAME,
            source_key,
            ExtraArgs={"ContentType": "image/png"}
        )
        source_url = f"{PUBLIC_BASE}/{source_key}"
        print(f"✅ [generate_remix] Step 10 SUCCESS: Source image uploaded to R2: {source_url}")
    except Exception as e:
        print(f"❌ [generate_remix] Step 10 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to upload source image to R2")

    # ✅ STEP 11: Get canonical prompt ID
    try:
        print("📝 [generate_remix] Step 11: Getting canonical prompt ID")
        canonical_prompt_id = str(prompt.get("_id"))
        print(f"✅ [generate_remix] Step 11 SUCCESS: canonical_prompt_id={canonical_prompt_id}")
    except Exception as e:
        print(f"❌ [generate_remix] Step 11 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to get canonical prompt ID")

    # ✅ STEP 12: Resolve prompt text
    try:
        print("🔤 [generate_remix] Step 12: Resolving prompt text")
        raw_prompt = (prompt_text or "").strip()
        prompt_template = (prompt.get("prompt_template") or "").strip()
        prompt_variables = prompt.get("prompt_variables") or []

        variable_values = {}
        if variable_values_json:
            try:
                loaded = __import__("json").loads(variable_values_json)
                if isinstance(loaded, dict):
                    variable_values = {str(k): str(v or "") for k, v in loaded.items()}
            except Exception as json_err:
                print(f"⚠️ [generate_remix] Failed to parse variable_values_json: {json_err}")
                raise HTTPException(status_code=400, detail="Invalid variable_values_json")

        normalized_variable_values = {}
        for key, value in variable_values.items():
            normalized_key = normalize_variable_key(key)
            if normalized_key:
                normalized_variable_values[normalized_key] = str(value or "")

        if prompt_template:
            for item in prompt_variables:
                key = str(item.get("key") or "").strip()
                normalized_key = normalize_variable_key(key)
                if not normalized_key:
                    continue

                selected_value = str(variable_values.get(key, "") or "").strip()
                if not selected_value:
                    selected_value = str(normalized_variable_values.get(normalized_key, "") or "").strip()

                if not selected_value:
                    default_value = str(item.get("default_value") or "").strip()
                    if default_value:
                        selected_value = default_value
                        normalized_variable_values[normalized_key] = default_value

                if item.get("required") and not selected_value:
                    print(f"❌ [generate_remix] Step 12 FAILED: Missing required variable: {key}")
                    raise HTTPException(status_code=400, detail=f"Missing required variable: {key}")

            resolved_prompt = render_prompt_template(prompt_template, normalized_variable_values)
            if not resolved_prompt:
                print(f"❌ [generate_remix] Step 12 FAILED: Rendered prompt is empty")
                raise HTTPException(status_code=400, detail="Rendered prompt is empty")
        elif raw_prompt:
            resolved_prompt = raw_prompt
        else:
            resolved_prompt = build_prompt(
                prompt.get("style_name", ""),
                prompt.get("prompt_description", ""),
                ratio
            )

        variable_lock_instructions = build_variable_lock_instructions(normalized_variable_values)
        final_generation_prompt = build_identity_preserving_prompt(resolved_prompt, ratio)
        if variable_lock_instructions:
            final_generation_prompt = f"{final_generation_prompt}\n\n{variable_lock_instructions}".strip()

        print(f"✅ [generate_remix] Step 12 SUCCESS: Prompt resolved (length={len(final_generation_prompt)})")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [generate_remix] Step 12 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to resolve prompt text")

    # ✅ STEP 13: AI Generation
    try:
        print(f"🤖 [generate_remix] Step 13: Starting AI generation (model={resolved_model})")
        if resolved_model == "gemini":
            print("🔷 [generate_remix] Using Gemini API")
            try:
                output_bytes = generate_with_gemini(final_generation_prompt, source_url)
                print(f"✅ [generate_remix] Gemini generation succeeded ({len(output_bytes)} bytes)")
            except HTTPException as exc:
                if exc.status_code == 429 and (GEMINI_FALLBACK_MODE or "").lower() == "openai":
                    print("⚠️ [generate_remix] Gemini quota exceeded, falling back to OpenAI")
                    print(f"📄 [generate_remix] Using OpenAI as fallback with prompt (length={len(final_generation_prompt)})")
                    output_bytes = generate_with_openai(
                        final_generation_prompt,
                        ratio,
                        source_png,
                        mapped_quality,
                    )
                    print(f"✅ [generate_remix] OpenAI fallback succeeded ({len(output_bytes)} bytes)")
                else:
                    raise
        else:
            print("🔵 [generate_remix] Using OpenAI API")
            print(f"📄 [generate_remix] OpenAI prompt (length={len(final_generation_prompt)})")
            output_bytes = generate_with_openai(
                final_generation_prompt,
                ratio,
                source_png,
                mapped_quality,
            )
            print(f"✅ [generate_remix] OpenAI generation succeeded ({len(output_bytes)} bytes)")
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ [generate_remix] Step 13 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"AI generation failed: {str(e)}")

    # ✅ STEP 14: Apply watermark
    try:
        print("🎨 [generate_remix] Step 14: Applying watermark")
        try:
            img = Image.open(BytesIO(output_bytes)).convert("RGBA")
            img = crop_to_ratio(img, ratio)
            img = add_watermark_pil(img, logo_path="kirnagram-logo.png", text="KIRNAGRAM")
            out = BytesIO()
            img.save(out, format="PNG")
            out.seek(0)
            output_bytes = out.read()
            print(f"✅ [generate_remix] Step 14 SUCCESS: Watermark applied ({len(output_bytes)} bytes)")
        except Exception as watermark_err:
            print(f"⚠️ [generate_remix] Step 14 WARNING: Watermark failed ({type(watermark_err).__name__}), using unwatermarked image")
            print(f"    Details: {str(watermark_err)}")
            # Continue without watermark - don't fail the whole generation
    except Exception as e:
        print(f"⚠️ [generate_remix] Step 14 WARNING: {type(e).__name__}: {str(e)}")
        # Don't fail - continue with current output_bytes

    # ✅ STEP 15: Upload output image to R2
    try:
        print("☁️ [generate_remix] Step 15: Uploading output image to R2 (Cloudflare)")
        output_key = f"remix/output/{user_id}/{ts}.png"
        s3.upload_fileobj(
            BytesIO(output_bytes),
            BUCKET_NAME,
            output_key,
            ExtraArgs={"ContentType": "image/png"}
        )
        output_url = f"{PUBLIC_BASE}/{output_key}"
        print(f"✅ [generate_remix] Step 15 SUCCESS: Output image uploaded to R2: {output_url}")
    except Exception as e:
        print(f"❌ [generate_remix] Step 15 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to upload output image to R2")

    # ✅ STEP 16: Insert remix into database
    try:
        print("🗄️ [generate_remix] Step 16: Inserting remix into MongoDB")
        remix_result = await db.ai_creator_remixes.insert_one({
            "user_id": user_id,
            "prompt_id": canonical_prompt_id,
            "source_image": source_url,
            "output_image": output_url,
            "ratio": ratio,
            "model": resolved_model,
            "quality": mapped_quality,
            "credits_used": burn_cost,
            "payout_per_remix": int(prompt.get("payout_per_remix", 1) or 1),
            "created_at": datetime.utcnow()
        })
        print(f"✅ [generate_remix] Step 16 SUCCESS: Remix inserted (id={remix_result.inserted_id})")
    except Exception as e:
        print(f"❌ [generate_remix] Step 16 FAILED: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail="Failed to insert remix into database")

    # ✅ STEP 17: Update prompt with remix reference
    try:
        print("🔗 [generate_remix] Step 17: Updating prompt with remix reference")
        await db.ai_creator_prompts.update_one(
            {"_id": ObjectId(prompt_id)},
            {"$push": {"remixes": str(remix_result.inserted_id)}}
        )
        print(f"✅ [generate_remix] Step 17 SUCCESS: Prompt updated")
    except Exception as e:
        print(f"⚠️ [generate_remix] Step 17 WARNING: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        # Non-critical - continue anyway

    # ✅ STEP 18: Update remix count
    try:
        print("📊 [generate_remix] Step 18: Updating remix count")
        prompt_owner_id = prompt.get("user_id")
        if user_id == prompt_owner_id:
            owner_remix_count = await db.ai_creator_remixes.count_documents({
                "prompt_id": canonical_prompt_id,
                "user_id": user_id
            })
            if owner_remix_count == 1:
                await db.ai_creator_prompts.update_one(
                    {"_id": ObjectId(prompt_id)},
                    {"$inc": {"remix_count": 1}}
                )
        else:
            await db.ai_creator_prompts.update_one(
                {"_id": ObjectId(prompt_id)},
                {"$inc": {"remix_count": 1}}
            )
        print(f"✅ [generate_remix] Step 18 SUCCESS: Remix count updated")
    except Exception as e:
        print(f"⚠️ [generate_remix] Step 18 WARNING: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        # Non-critical - continue anyway

    # ✅ STEP 19: Deduct credits
    try:
        print("💳 [generate_remix] Step 19: Deducting credits from wallet")
        await db.credit_wallets.update_one(
            {"user_id": user_id},
            {"$inc": {"balance": -burn_cost}}
        )
        print(f"✅ [generate_remix] Step 19 SUCCESS: Deducted {burn_cost} credits")
    except Exception as e:
        print(f"⚠️ [generate_remix] Step 19 WARNING: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        # Non-critical but log it

    # ✅ STEP 20: Record transaction
    try:
        print("📋 [generate_remix] Step 20: Recording transaction")
        await record_transaction(
            user_id,
            -burn_cost,
            "burn",
            {"type": "remix", "model": resolved_model, "quality": mapped_quality}
        )
        print(f"✅ [generate_remix] Step 20 SUCCESS: Transaction recorded")
    except Exception as e:
        print(f"⚠️ [generate_remix] Step 20 WARNING: {type(e).__name__}: {str(e)}")
        print(traceback.format_exc())
        # Non-critical - continue anyway

    # ✅ STEP 21: Send notification
    try:
        print("🔔 [generate_remix] Step 21: Sending credit burn notification")
        model_label = (resolved_model or "AI model").capitalize()
        quality_label = (mapped_quality or "").strip().capitalize()
        quality_suffix = f" ({quality_label})" if quality_label else ""
        notification_doc = {
            "user_id": user_id,
            "action": "credits_burned",
            "description": f"You spent {burn_cost} credits for remix with {model_label}{quality_suffix}",
            "timestamp": datetime.utcnow(),
            "read": False,
            "created_at": datetime.utcnow(),
        }
        await db.notifications.insert_one(notification_doc)
        print(f"✅ [generate_remix] Step 21 SUCCESS: Notification sent")
    except Exception as e:
        print(f"⚠️ [generate_remix] Step 21 WARNING: Notification failed ({type(e).__name__}), non-blocking")
        # Non-critical - don't fail

    # ✅ SUCCESS: Return response
    print(f"🎉 [generate_remix] ✅ GENERATION COMPLETE for remix_id={remix_result.inserted_id}")
    response_data = {
        "success": True,
        "image_url": output_url,
        "remix_id": str(remix_result.inserted_id),
        "credits_used": burn_cost
    }
    print(f"📤 [generate_remix] RETURNING RESPONSE: success={response_data['success']}, remix_id={response_data['remix_id']}, image_url_length={len(response_data['image_url'])}")
    return response_data


@router.post("/gemini-edit")
async def gemini_image_edit(
    prompt_text: str = Form(...),
    image_base64: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    authorization: str = Header(...)
):
    user_id = get_user_id(authorization)
    print("GEMINI_EDIT_REQUEST:", {
        "user_id": user_id,
        "has_image_file": image is not None,
        "has_base64": bool(image_base64),
    })

    if image is None and not image_base64:
        raise HTTPException(status_code=400, detail="Provide image file or base64 image")

    if image is not None:
        source_png = convert_to_png(image)
    else:
        source_png = _pil_to_png_bytes(_load_pil_from_base64(image_base64 or ""))

    ts = int(datetime.utcnow().timestamp())
    source_key = f"remix/gemini-edit/{user_id}/{ts}.png"
    s3.upload_fileobj(
        BytesIO(source_png),
        BUCKET_NAME,
        source_key,
        ExtraArgs={"ContentType": "image/png"}
    )
    source_url = f"{PUBLIC_BASE}/{source_key}"

    output_bytes = generate_with_gemini(prompt_text, source_url)
    return StreamingResponse(BytesIO(output_bytes), media_type="image/png")