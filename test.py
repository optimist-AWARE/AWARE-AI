import os
import io
import base64
import re
from typing import List, Literal, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from pydantic import BaseModel, Field
from PIL import Image

from google import genai
from google.genai import types


# -------------------------
# ENV / Client
# -------------------------
load_dotenv()
load_dotenv("AI.env")

API_KEY = os.getenv("GEMINI_API_KEY")
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3-flash-preview")
# "나노바나나" 계열은 계정/프로젝트에 따라 모델명이 다를 수 있어 env로 바꿀 수 있게 둠
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
# 예: Gemini 3 Pro Image preview를 쓰고 싶으면 아래처럼 env에 설정:
# GEMINI_IMAGE_MODEL=gemini-3-pro-image-preview  (Gemini 3 문서에 표기된 이미지 모델) :contentReference[oaicite:1]{index=1}

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY가 없습니다. .env 또는 AI.env에 GEMINI_API_KEY를 설정하세요.")

client = genai.Client(api_key=API_KEY)

app = FastAPI(title="AWARE AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 배포 땐 도메인으로 좁히기
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------------------------
# Utils
# -------------------------
def _load_image(file: UploadFile) -> Image.Image:
    try:
        raw = file.file.read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return img
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이미지 읽기 실패: {e}")


def _pil_to_jpeg_part(img: Image.Image, quality: int = 95) -> types.Part:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")


def _get_finish_reason(res) -> str:
    try:
        if getattr(res, "candidates", None) and res.candidates:
            fr = getattr(res.candidates[0], "finish_reason", None)
            return str(fr) if fr is not None else "UNKNOWN"
    except Exception:
        pass
    return "UNKNOWN"


def _extract_text_from_response(res) -> str:
    # res.text 우선
    if getattr(res, "text", None):
        return res.text

    # candidates/parts에서 텍스트만 모으기
    parts = None
    if getattr(res, "parts", None):
        parts = res.parts
    elif getattr(res, "candidates", None) and res.candidates:
        c0 = res.candidates[0]
        if getattr(c0, "content", None) and getattr(c0.content, "parts", None):
            parts = c0.content.parts

    if not parts:
        return ""

    texts = []
    for p in parts:
        t = getattr(p, "text", None)
        if t:
            texts.append(t)
    return "\n".join(texts)


def _find_first_json_object(s: str) -> str:
    """
    문자열에서 첫 JSON 객체({...})를 안전하게 추출.
    - 따옴표("...") 안의 { } 는 카운팅하지 않음
    - ```json ... ``` 코드펜스 제거
    """
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


def _thinking_config_for(model_name: str) -> types.ThinkingConfig:
    # Gemini 3는 thinking_level 권장. Flash는 minimal 지원 :contentReference[oaicite:2]{index=2}
    if (model_name or "").startswith("gemini-3"):
        return types.ThinkingConfig(thinking_level="minimal")
    # Gemini 2.5 계열이면 thinking_budget=0도 가능
    return types.ThinkingConfig(thinking_budget=0)


def _extract_inline_image(response) -> Tuple[bytes, str]:
    """
    google-genai 응답에서 이미지 바이트(inline_data.data)와 mime_type 추출
    """
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
StyleKey = Literal["clean", "street", "formal"]

class StyleOption(BaseModel):
    key: StyleKey
    title: str
    summary: str
    edit_prompt: str


class AnalyzeResult(BaseModel):
    name: str = Field(description="사용자 이름")
    style_score: float = Field(ge=0, le=10, description="스타일/프로필 완성도 점수 (0~10, 소수 1자리)")
    vibe: str
    vibe_reason: str
    styling_tips: List[str]
    style_options: List[StyleOption]
    default_style_key: StyleKey


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
):
    """
    이미지 + 이름을 받아
    - 스타일/프로필 완성도 점수
    - vibe(상) + 이유
    - 스타일링 팁
    - 3가지 스타일 옵션(선택지) + 각각의 edit_prompt
    JSON 반환
    """
    img = _load_image(image)
    safe_name = (name or "000").strip() or "000"

    # ✅ (중요) 외모/매력 평가가 아니라 "스타일/프로필 완성도" 중심으로만
    system_instruction = (
        "You are a friendly style assistant for teens. "
        "Do NOT insult. Do NOT judge attractiveness/beauty. "
        "Return a 'style/profile readiness' score based on grooming, styling, lighting, sharpness, and composition. "
        "Always be positive/neutral."
    )

    # 출력 길이 짧게(잘림 방지), JSON 한 덩어리로
    prompt = (
        f"사용자 이름은 '{safe_name}'이다.\n"
        "아래 사진을 보고 JSON만 출력해. 설명/마크다운/코드펜스 금지.\n"
        "반드시 아래 스키마를 정확히 지켜:\n"
        "{\n"
        '  "name": string,\n'
        '  "style_score": number (0~10, 소수1자리),\n'
        '  "vibe": string (예: 강아지상/고양이상/사슴상/여우상/곰상/토끼상/공룡상 중 하나),\n'
        '  "vibe_reason": string (긍정적/중립적으로 1~2문장),\n'
        '  "styling_tips": string[] (4~6개, 선택형 팁: 헤어/안경/의상/각도/조명 등),\n'
        '  "style_options": [\n'
        '    {"key":"clean","title":string,"summary":string,"edit_prompt":string},\n'
        '    {"key":"street","title":string,"summary":string,"edit_prompt":string},\n'
        '    {"key":"formal","title":string,"summary":string,"edit_prompt":string}\n'
        '  ],\n'
        '  "default_style_key": "clean"|"street"|"formal"\n'
        "}\n"
        "규칙:\n"
        "- 외모(매력/못생김/예쁨) 평가 금지.\n"
        "- name 필드는 반드시 입력 이름 그대로 넣어.\n"
        "- edit_prompt는 '원본 인물 동일성 유지(얼굴/표정/신원 유지)'를 전제로 스타일만 적용하도록 한국어로 써.\n"
        "- JSON은 한 번에 끝까지 완성해(중간에 끊기지 않게 간결하게).\n"
    )

    image_part = _pil_to_jpeg_part(img)

    def call_once(max_tokens: int):
        return client.models.generate_content(
            model=TEXT_MODEL,
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(
                thinking_config=_thinking_config_for(TEXT_MODEL),  # ✅ 잘림 방지 핵심 :contentReference[oaicite:3]{index=3}
                system_instruction=system_instruction,
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=max_tokens,
            ),
        )

    try:
        # 1차 호출
        res = call_once(1200)

        fr = _get_finish_reason(res)
        raw_text = _extract_text_from_response(res)
        print("\n[ANALYZE FINISH_REASON]", fr)
        print("[ANALYZE TEXT LEN]", len(raw_text or ""))
        print("[ANALYZE RAW HEAD]\n", (raw_text or "")[:200])

        # 파싱 시도
        json_text = _find_first_json_object(raw_text)
        return AnalyzeResult.model_validate_json(json_text).model_dump()

    except Exception as e1:
        # 2차 재시도(토큰 더 주기)
        try:
            print("\n[ANALYZE RETRY] reason =", repr(e1))
            res2 = call_once(2200)

            fr2 = _get_finish_reason(res2)
            raw2 = _extract_text_from_response(res2)
            print("[ANALYZE RETRY FINISH_REASON]", fr2)
            print("[ANALYZE RETRY TEXT LEN]", len(raw2 or ""))
            print("[ANALYZE RETRY RAW HEAD]\n", (raw2 or "")[:200])

            json_text2 = _find_first_json_object(raw2)
            return AnalyzeResult.model_validate_json(json_text2).model_dump()

        except Exception as e2:
            raw_preview = (_extract_text_from_response(res2) if 'res2' in locals() else "")
            raw_preview = (raw_preview or "")[:800]
            raise HTTPException(
                status_code=500,
                detail=f"응답 JSON 파싱 실패: {repr(e2)} / raw_preview={raw_preview}",
            )


@app.post("/api/apply")
def apply_style(
    image: UploadFile = File(...),
    edit_prompt: Optional[str] = Form(None),
    preset: Optional[Literal["clean", "street", "formal"]] = Form("clean"),
):
    """
    원본 이미지를 받아, 선택한 스타일 프롬프트(또는 preset)로 스타일링 적용 이미지를 생성해 반환(png/jpg)
    """
    img = _load_image(image)

    preset_map = {
        "clean": "깔끔하고 밝은 스튜디오 톤. 헤어는 단정하게 정리. 피부/조명은 자연스럽게 보정. 배경은 심플.",
        "street": "스트릿 캐주얼 무드. 색감은 세련되게. 오버핏/힙한 룩 느낌. 배경은 도시 감성으로 은은하게.",
        "formal": "포멀한 프로필/증명사진 느낌. 조명은 균형 있게. 톤은 차분하고 단정하게.",
    }

    final_prompt = (edit_prompt or "").strip()
    if not final_prompt:
        final_prompt = preset_map.get(preset or "clean", preset_map["clean"])

    # ✅ “다른 사람처럼” 바뀌는 걸 최대한 막는 가드
    safe_edit_prompt = (
        "IMPORTANT: Keep the same person. Preserve identity, face shape, facial features, expression, and age. "
        "Do NOT change the person into someone else. "
        "Only apply styling, grooming, clothing, lighting, and color grading adjustments.\n\n"
        f"요청(스타일만 적용): {final_prompt}"
    )

    image_part = _pil_to_jpeg_part(img)

    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=[image_part, safe_edit_prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"이미지 생성 호출 실패: {repr(e)}")

    try:
        img_bytes, mime = _extract_inline_image(response)
        return StreamingResponse(io.BytesIO(img_bytes), media_type=mime)
    except Exception as e:
        debug_text = getattr(response, "text", None)
        raise HTTPException(status_code=500, detail=f"이미지 추출 실패: {repr(e)} / response.text={debug_text}")
