import os
import io
import base64
import re
from typing import List, Optional, Tuple, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from pydantic import BaseModel, Field
from PIL import Image

from google import genai
from google.genai import types

from openai import OpenAI


# -------------------------
# ENV / Client
# -------------------------
load_dotenv()
load_dotenv("AI.env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5-mini")

DEBUG = os.getenv("DEBUG", "1") == "1"

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY가 없습니다. .env 또는 AI.env에 GEMINI_API_KEY를 설정하세요.")
if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY가 없습니다. .env 또는 AI.env에 OPENAI_API_KEY를 설정하세요.")

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
oa = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="AWARE AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------
# Utils
# -------------------------
ALLOWED_VIBES = ["강아지상", "고양이상", "토끼상", "여우상", "사슴상", "곰상", "공룡상"]


def _load_image(file: UploadFile) -> Image.Image:
    try:
        raw = file.file.read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return img
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이미지 읽기 실패: {e}")


def _pil_to_jpeg_bytes(img: Image.Image, quality: int = 95) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _pil_to_jpeg_part(img: Image.Image, quality: int = 95) -> types.Part:
    return types.Part.from_bytes(data=_pil_to_jpeg_bytes(img, quality=quality), mime_type="image/jpeg")


def _oa_output_text(resp) -> str:
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt

    out = getattr(resp, "output", None) or []
    chunks = []
    for item in out:
        content = getattr(item, "content", None) or []
        for c in content:
            t = None
            if isinstance(c, dict):
                t = c.get("text")
            else:
                t = getattr(c, "text", None)
            if t:
                chunks.append(t)
    return "\n".join(chunks)


def _find_first_json_object(s: str) -> str:
    s = (s or "").strip()

    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()

    start = s.find("{")
    if start == -1:
        raise ValueError("JSON 시작 '{'를 찾지 못했습니다.")

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(s)):
        ch = s[i]

        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]

    raise ValueError("JSON 끝 '}'를 찾지 못했습니다.")


def _sanitize_analyze_dict(d: dict) -> dict:
    """
    모델이 실수로:
    - style_score를 0~100으로 주거나
    - vibe를 동물상이 아닌 문장으로 주거나
    - default_style_key 누락/불일치
    같은 경우를 최대한 안전하게 보정
    """
    # name
    if "name" in d and isinstance(d["name"], str):
        d["name"] = d["name"].strip() or "000"
    else:
        d["name"] = "000"

    # style_score (0~10으로 강제)
    sc = d.get("style_score")
    try:
        scf = float(sc)
        # 11~100 사이면 0~100로 보고 /10
        if 10 < scf <= 100:
            scf = scf / 10.0
        # 범위 클램프 + 소수 1자리
        scf = max(0.0, min(10.0, scf))
        d["style_score"] = round(scf, 1)
    except Exception:
        d["style_score"] = 0.0

    # vibe (동물상으로 강제)
    vibe = d.get("vibe")
    if isinstance(vibe, str):
        v = vibe.strip()
        if v not in ALLOWED_VIBES:
            # 문장에 '사슴' 같은 단어가 들어가면 매칭
            matched = None
            for vv in ALLOWED_VIBES:
                base = vv.replace("상", "")
                if base in v or vv in v:
                    matched = vv
                    break
            d["vibe"] = matched or "강아지상"
        else:
            d["vibe"] = v
    else:
        d["vibe"] = "강아지상"

    # vibe_reason
    if not isinstance(d.get("vibe_reason"), str):
        d["vibe_reason"] = ""

    # styling_tips
    tips = d.get("styling_tips")
    if not isinstance(tips, list):
        d["styling_tips"] = []
    else:
        d["styling_tips"] = [str(x) for x in tips][:6]

    # style_options
    opts = d.get("style_options")
    fixed_opts = []
    if isinstance(opts, list):
        for o in opts:
            if not isinstance(o, dict):
                continue
            key = str(o.get("key") or "").strip()
            title = str(o.get("title") or "").strip()
            summary = str(o.get("summary") or "").strip()
            edit_prompt = str(o.get("edit_prompt") or "").strip()
            if key and title and edit_prompt:
                fixed_opts.append(
                    {"key": key, "title": title, "summary": summary, "edit_prompt": edit_prompt}
                )

    # 3개 미만이면 fallback 채움(프론트가 undefined 안 뜨게)
    fallback = [
        {"key": "clean_casual", "title": "클린 캐주얼", "summary": "단정하고 깔끔한 기본 스타일.", "edit_prompt": "얼굴·표정 동일성 유지, 헤어는 단정하게 정리, 깔끔한 상의와 심플 배경, 자연광 톤으로 보정."},
        {"key": "street_modern", "title": "스트릿 모던", "summary": "힙하지만 과하지 않은 무드.", "edit_prompt": "얼굴·표정 동일성 유지, 오버핏 상의 느낌, 색감은 쿨톤으로 정리, 배경은 심플하게."},
        {"key": "smart_minimal", "title": "스마트 미니멀", "summary": "프로필용 정돈된 분위기.", "edit_prompt": "얼굴·표정 동일성 유지, 셔츠 또는 니트 계열로 단정하게, 조명은 밝고 균일하게, 과한 보정 금지."},
    ]
    while len(fixed_opts) < 3:
        fixed_opts.append(fallback[len(fixed_opts)])
    fixed_opts = fixed_opts[:3]
    d["style_options"] = fixed_opts

    # default_style_key
    dkey = d.get("default_style_key")
    keys = [o["key"] for o in fixed_opts]
    if not isinstance(dkey, str) or dkey not in keys:
        d["default_style_key"] = keys[0]

    return d


def _extract_inline_image(response) -> Tuple[bytes, str]:
    parts = None
    if getattr(response, "parts", None):
        parts = response.parts
    elif getattr(response, "candidates", None) and response.candidates:
        cand0 = response.candidates[0]
        if getattr(cand0, "content", None) and getattr(cand0.content, "parts", None):
            parts = cand0.content.parts

    if not parts:
        raise ValueError("response에 parts가 없습니다.")

    for part in parts:
        inline = getattr(part, "inline_data", None)
        if inline and getattr(inline, "data", None):
            data = inline.data
            mime = getattr(inline, "mime_type", None) or "image/png"

            if isinstance(data, str):
                data = base64.b64decode(data)

            if not isinstance(data, (bytes, bytearray)):
                raise ValueError(f"inline_data.data 타입이 bytes가 아닙니다: {type(data)}")

            return bytes(data), mime

    raise ValueError("inline_data 이미지가 없습니다.")


# -------------------------
# Response Models
# -------------------------
class StyleOption(BaseModel):
    key: str
    title: str
    summary: str
    edit_prompt: str


class AnalyzeResult(BaseModel):
    name: str
    style_score: float = Field(ge=0, le=10)   # ✅ 0~10 고정
    vibe: str
    vibe_reason: str
    styling_tips: List[str]
    style_options: List[StyleOption]
    default_style_key: str


# -------------------------
# Routes
# -------------------------
@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/analyze", response_model=AnalyzeResult)
def analyze_face(
    image: UploadFile = File(...),
    name: str = Form("000"),
    gender: Optional[Literal["male", "female"]] = Form(None),
    age: Optional[int] = Form(None),
):
    img = _load_image(image)
    safe_name = (name or "000").strip() or "000"

    # ✅ 나이/성별은 참고만(결과가 크게 흔들리지 않게)
    gender_text = "미입력" if not gender else ("남" if gender == "male" else "여")
    age_text = "미입력" if age is None else str(age)

    system_instruction = (
        "You are a professional image consultant and stylist. "
        "Be positive and constructive. "
        "Do NOT insult or rate attractiveness. "
        "Focus on styling/presentation quality. "
        "Return valid JSON only."
    )

    prompt = (
        f"사용자 이름은 '{safe_name}'이다.\n"
        f"사용자가 입력한 성별(참고): {gender_text}\n"
        f"사용자가 입력한 나이(참고): {age_text}\n"
        "주의: 성별/나이는 참고 정보일 뿐이며, 사진 기반 분석을 우선하되 추천이 해당 나이대에 자연스럽게 어울리도록만 살짝 조정해.\n"
        "아래 사진을 보고 JSON만 출력해. 설명/마크다운/코드펜스 금지.\n"
        "스키마:\n"
        "{"
        "\"name\":string,"
        "\"style_score\":number(0~10,소수1자리),"
        "\"vibe\":string(반드시 동물상 1개: 강아지상/고양이상/토끼상/여우상/사슴상/곰상/공룡상 중 하나),"
        "\"vibe_reason\":string,"
        "\"styling_tips\":string[](4~6개),"
        "\"style_options\":[{\"key\":string,\"title\":string,\"summary\":string,\"edit_prompt\":string},{...},{...}],"
        "\"default_style_key\":string"
        "}\n"
        "규칙:\n"
        "- 외모(매력/못생김/예쁨) 평가 금지.\n"
        "- style_score는 절대 0~100으로 쓰지 말고, 반드시 0~10으로만 출력.\n"
        "- style_options는 정확히 3개.\n"
        "- summary는 1문장.\n"
        "- edit_prompt는 '원본 인물 동일성 유지(얼굴/표정/신원 유지)' 전제로 스타일만 적용하도록 한국어로 작성.\n"
        "- JSON을 한 번에 끝까지 완성.\n"
    )

    img_bytes = _pil_to_jpeg_bytes(img, quality=95)
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{img_b64}"

    def call_once(max_out: int):
        return oa.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_instruction}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": data_url, "detail": "high"},
                    ],
                },
            ],
            text={"format": {"type": "json_object"}},
            max_output_tokens=max_out,
        )

    # ✅ 1차부터 넉넉하게(카메라 캡처는 설명이 길게 나오는 경향이 있어)
    resp = call_once(2400)
    raw = _oa_output_text(resp)

    if DEBUG:
        print("\n[ANALYZE STATUS]", getattr(resp, "status", None))
        print("[ANALYZE OUT LEN]", len(raw))
        print("[ANALYZE HEAD]\n", raw[:220])
        print("[ANALYZE TAIL]\n", raw[-220:])

    # incomplete면 한 번 더
    if getattr(resp, "status", None) != "completed":
        resp2 = call_once(3200)
        raw2 = _oa_output_text(resp2)

        if DEBUG:
            print("\n[ANALYZE RETRY STATUS]", getattr(resp2, "status", None))
            print("[ANALYZE RETRY OUT LEN]", len(raw2))
            print("[ANALYZE RETRY HEAD]\n", raw2[:220])
            print("[ANALYZE RETRY TAIL]\n", raw2[-220:])

        raw = raw2

    try:
        json_text = _find_first_json_object(raw)
        data = __import__("json").loads(json_text)

        # ✅ 핵심: 모델이 0~100으로 줘도 여기서 0~10으로 보정
        data = _sanitize_analyze_dict(data)

        return AnalyzeResult.model_validate(data).model_dump()

    except Exception as e:
        preview = (raw or "")[:900]
        raise HTTPException(status_code=500, detail=f"응답 JSON 파싱/검증 실패: {repr(e)} / raw_preview={preview}")


@app.post("/api/apply")
def apply_style(
    image: UploadFile = File(...),
    edit_prompt: str = Form(...),
):
    img = _load_image(image)

    final_prompt = (edit_prompt or "").strip()
    if not final_prompt:
        raise HTTPException(status_code=400, detail="edit_prompt가 비어 있습니다.")

    safe_edit_prompt = (
        "IMPORTANT: Keep the same person. Preserve identity, face shape, facial features, expression, and age. "
        "Do NOT change the person into someone else. "
        "Only apply styling, grooming, clothing, lighting, and color grading adjustments.\n\n"
        f"요청(스타일만 적용): {final_prompt}"
    )

    image_part = _pil_to_jpeg_part(img)

    try:
        response = gemini_client.models.generate_content(
            model=IMAGE_MODEL,
            contents=[image_part, safe_edit_prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이미지 생성 호출 실패: {repr(e)}")

    try:
        img_bytes, mime = _extract_inline_image(response)
        return StreamingResponse(io.BytesIO(img_bytes), media_type=mime)
    except Exception as e:
        debug_text = getattr(response, "text", None)
        raise HTTPException(status_code=500, detail=f"이미지 추출 실패: {repr(e)} / response.text={debug_text}")
