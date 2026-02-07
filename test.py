import os
import io
import base64
from typing import List, Literal, Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from pydantic import BaseModel, Field
from PIL import Image

from google import genai
from google.genai import types


# .env 또는 AI.env 둘 중 하나라도 읽히게
load_dotenv()
load_dotenv("AI.env")

API_KEY = os.getenv("GEMINI_API_KEY")
TEXT_MODEL = os.getenv("GEMINI_TEXT_MODEL", "gemini-3-flash-preview")
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

if not API_KEY:
    raise RuntimeError("GEMINI_API_KEY가 없습니다. .env 또는 AI.env에 GEMINI_API_KEY를 설정하세요.")

client = genai.Client(api_key=API_KEY)

app = FastAPI(title="AWARE AI Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # 배포 땐 도메인으로 좁히기 추천
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_image(file: UploadFile) -> Image.Image:
    try:
        raw = file.file.read()
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        return img
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"이미지 읽기 실패: {e}")


class AnalyzeResult(BaseModel):
    # 외모 점수 대신 "사진 품질 점수"만
    photo_quality_score: float = Field(ge=0, le=10, description="사진 품질 점수 (0~10, 소수 1자리)")
    vibe: str = Field(description="재미용 분위기/상 키워드 (예: 강아지상/고양이상 등)")
    vibe_reason: str = Field(description="왜 그렇게 판단했는지(긍정적/중립적으로)")
    styling_tips: List[str] = Field(description="스타일링/촬영 팁 (부정적 표현 금지)")
    suggested_edit_prompt: str = Field(description="이미지 생성(편집)용 추천 프롬프트")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/api/analyze", response_model=AnalyzeResult)
def analyze_face(image: UploadFile = File(...)):
    img = _load_image(image)

    system_instruction = (
        "You are a friendly style assistant for teens. "
        "DO NOT rate attractiveness, DO NOT judge beauty, DO NOT insult. "
        "Only give a photo quality score (lighting/sharpness/composition) and positive/neutral style suggestions."
    )

    prompt = (
        "다음 얼굴 사진을 보고 JSON으로만 답해.\n"
        "- photo_quality_score: 0~10 (소수 첫째까지), '사진 품질'만 평가(조명/선명도/구도)\n"
        "- vibe: 재미용 '상' 키워드 하나(예: 강아지상, 고양이상, 토끼상, 여우상, 사슴상, 곰상, 공룡상)\n"
        "- vibe_reason: 긍정적/중립적으로 짧게\n"
        "- styling_tips: 4~6개 (헤어/안경/의상/촬영각도/조명 같은 '선택' 중심, 부정적 표현 금지)\n"
        "- suggested_edit_prompt: 원본 얼굴/표정/피부톤은 최대한 유지하고, 추천한 스타일을 '자연스럽게' 적용하도록 하는 한글 프롬프트\n"
        "주의: 외모 점수(매력 점수) 같은 건 절대 만들지 마."
    )

    # 이미지 -> bytes 파트로
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    image_part = types.Part.from_bytes(data=buf.getvalue(), mime_type="image/jpeg")

    try:
        res = client.models.generate_content(
            model=TEXT_MODEL,
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                response_schema=AnalyzeResult,
                temperature=0.2,
                max_output_tokens=800,
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Gemini 호출 실패: {repr(e)}")

    try:
        return AnalyzeResult.model_validate_json(res.text).model_dump()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"응답 JSON 파싱 실패: {e} / raw={getattr(res, 'text', None)}")


def _extract_inline_image(response) -> Tuple[bytes, str]:
    """
    google-genai 응답에서 이미지 바이트(inline_data.data)와 mime_type을 뽑아오는 안전한 함수
    (SDK/버전마다 response.parts / response.candidates 구조가 달라서 둘 다 대응)
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


@app.post("/api/apply")
def apply_style(
    image: UploadFile = File(...),
    edit_prompt: Optional[str] = Form(None),
    preset: Optional[Literal["natural", "clean", "street", "formal"]] = Form("natural"),
):
    img = _load_image(image)

    if not edit_prompt:
        preset_map = {
            "natural": "원본 얼굴 특징은 유지하면서 자연스럽게 피부 톤/조명만 부드럽게 보정하고, 전체적인 분위기를 깔끔하게 정리해줘.",
            "clean": "원본 얼굴 특징은 유지하면서 헤어를 단정하게 정리한 느낌으로, 조명은 밝고 깨끗한 스튜디오 톤으로 보정해줘.",
            "street": "원본 얼굴 특징은 유지하면서 스트릿 캐주얼 무드(조명/색감)를 추가하고 배경은 은은하게 더 세련되게 정리해줘.",
            "formal": "원본 얼굴 특징은 유지하면서 포멀한 프로필 사진 느낌(깔끔한 조명/톤, 단정한 분위기)으로 보정해줘.",
        }
        edit_prompt = preset_map.get(preset, preset_map["natural"])

    safe_edit_prompt = (
        "Using the provided image, keep the person's identity, face shape, facial features, and expression unchanged. "
        "Only apply styling and photo adjustments as requested. "
        "Do not make them look like a different person.\n\n"
        f"요청: {edit_prompt}"
    )

    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=[img, safe_edit_prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"나노바나나 호출 실패: {repr(e)}")

    try:
        img_bytes, mime = _extract_inline_image(response)
        return StreamingResponse(io.BytesIO(img_bytes), media_type=mime)
    except Exception as e:
        debug_text = getattr(response, "text", None)
        raise HTTPException(status_code=500, detail=f"이미지 추출 실패: {repr(e)} / response.text={debug_text}")
