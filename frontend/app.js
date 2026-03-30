const API_BASE = "http://127.0.0.1:8000";

const el = (id) => document.getElementById(id);
const screens = {
  upload: el("screen-upload"),
  result: el("screen-result"),
  styled: el("screen-styled"),
};
const loading = el("loading");

// camera
const cameraVideo = el("camera");
const cameraCanvas = el("camera-canvas");
const btnCamStart = el("btn-camera-start");
const btnCamShot = el("btn-camera-capture");
const btnCamStop = el("btn-camera-stop");

let camStream = null;

const state = {
  name: "000",
  age: null,
  gender: null,
  email: "",

  originalFile: null,
  originalUrl: null,
  analysis: null,
  selectedStyleKey: null,

  personKey: null,
  generatedByPerson: new Map(),
  generated: [],
  selectedGeneratedId: null,

  // [신규] 쇼핑 추천 세트
  shoppingSets: null,
  selectedShoppingSetKey: null,
  shoppingLoading: false,

  // [신규] 커스텀 생성 옵션
  selectedBase: "original",       // "original" | "current"
  selectedCategories: [],
};

/* =========================
   RESET DEBUG (no UI change)
   - "처음부터 다시하기" 눌렀는데 값이 남는 원인을 콘솔 로그로 추적하기 위한 코드만 추가됨
   ========================= */
const RESET_DEBUG = true; // 필요 없으면 false로

function _safeFileInfo(f) {
  if (!f) return null;
  try {
    return { name: f.name, size: f.size, type: f.type, lastModified: f.lastModified };
  } catch {
    return String(f);
  }
}

function _isHidden(node) {
  if (!node) return null;
  return node.classList.contains("hidden");
}

function dumpResetDebug(label) {
  if (!RESET_DEBUG) return;

  const fileEl = el("file");
  const nameEl = el("name");
  const ageEl = el("age");
  const genderEl = el("gender");

  // 성별 UI가 라디오일 때 대비 (없으면 null)
  const checkedGender = document.querySelector('input[name="gender"]:checked')?.value || null;

  const snap = {
    label,
    time: new Date().toISOString(),
    state: {
      name: state.name,
      age: state.age,
      gender: state.gender,
      personKey: state.personKey,
      originalFile: _safeFileInfo(state.originalFile),
      originalUrl: state.originalUrl,
      analysisExists: !!state.analysis,
      generatedCount: Array.isArray(state.generated) ? state.generated.length : null,
      generatedByPersonKeys: state.generatedByPerson ? Array.from(state.generatedByPerson.keys()) : null,
      selectedGeneratedId: state.selectedGeneratedId,
    },
    dom: {
      fileValue: fileEl ? fileEl.value : null,
      fileSelected: fileEl?.files?.[0] ? _safeFileInfo(fileEl.files[0]) : null,
      nameValue: nameEl ? nameEl.value : null,
      ageValue: ageEl ? ageEl.value : null,
      genderValue: genderEl ? genderEl.value : null,
      checkedGender,
      screen: {
        uploadHidden: _isHidden(screens.upload),
        resultHidden: _isHidden(screens.result),
        styledHidden: _isHidden(screens.styled),
      },
      preview: {
        uploadSrc: el("preview-upload")?.getAttribute("src") || null,
        resultSrc: el("preview-result")?.getAttribute("src") || null,
        styledSrc: el("preview-styled")?.getAttribute("src") || null,
      },
    },
  };

  console.groupCollapsed(`[RESET DEBUG] ${label}`);
  console.log(snap);
  console.log(new Error("[RESET DEBUG stack]").stack);
  console.groupEnd();
}

// 새로고침/복원(bfcache) 되는지 확인
window.addEventListener("pageshow", (e) => {
  if (!RESET_DEBUG) return;
  console.log("[RESET DEBUG] pageshow", { persisted: e.persisted, url: location.href });
});

window.addEventListener("beforeunload", () => {
  if (!RESET_DEBUG) return;
  console.log("[RESET DEBUG] beforeunload fired");
});

document.addEventListener("DOMContentLoaded", () => {
  if (!RESET_DEBUG) return;
  console.log("[RESET DEBUG] app.js loaded OK", { href: location.href });
  console.log("[RESET DEBUG] scripts:", Array.from(document.scripts).map(s => s.src || "(inline)"));
  try {
    const entries = performance.getEntriesByType("resource");
    const jsEntries = entries.filter(e => String(e.name).includes("app.js"));
    console.log("[RESET DEBUG] resource(app.js) entries:", jsEntries);
  } catch {}
  // id 중복/누락 체크
  ["btn-back-upload","btn-analyze","file","name","preview-upload","preview-result","screen-upload","screen-result","screen-styled"].forEach(id => {
    const nodes = document.querySelectorAll(`#${CSS.escape(id)}`);
    if (nodes.length !== 1) console.warn("[RESET DEBUG] element count != 1:", id, nodes.length, nodes);
  });
  dumpResetDebug("DOMContentLoaded (initial)");
});

function showLoading(on) {
  loading.classList.toggle("hidden", !on);
  loading.setAttribute("aria-hidden", String(!on));
}

function showScreen(which) {
  Object.values(screens).forEach(s => s.classList.add("hidden"));
  screens[which].classList.remove("hidden");
}

function setPreview(selectorId, url) {
  const img = el(selectorId);
  img.src = url;
  img.classList.remove("hidden");
}

function enableAnalyzeButton() {
  const btn = el("btn-analyze");
  btn.disabled = !state.originalFile;
}

function updateTipApplyButton() {}

function resetAll() {
  // 카메라 중단
  stopCamera();

  // URL 해제
  if (state.originalUrl) {
    URL.revokeObjectURL(state.originalUrl);
  }
  state.generated.forEach(g => {
    if (g.url && g.url.startsWith("blob:")) URL.revokeObjectURL(g.url);
  });

  // state 초기화
  state.name = "000";
  state.age = null;
  state.gender = null;
  state.originalFile = null;
  state.originalUrl = null;
  state.analysis = null;
  state.selectedStyleKey = null;
  state.personKey = null;
  state.generatedByPerson = new Map();
  state.generated = [];
  state.selectedGeneratedId = null;
  state.shoppingSets = null;
  state.selectedShoppingSetKey = null;
  state.shoppingLoading = false;
  state.selectedBase = "original";
  state.selectedCategories = [];
  state.email = "";

  // DOM 초기화
  el("name").value = "";
  el("age").value = "";
  el("email").value = "";
  el("file").value = "";
  el("gender-m").classList.remove("active");
  el("gender-f").classList.remove("active");

  const previewUpload = el("preview-upload");
  previewUpload.src = "";
  previewUpload.classList.add("hidden");
  el("placeholder").classList.remove("hidden");

  el("result-title").textContent = "OOO님의 결과";
  el("result-score").textContent = "- / 10 점";
  el("result-vibe").textContent = "-";
  el("result-reason").textContent = "";
  el("tips").innerHTML = "";
  el("options").innerHTML = "";
  el("thumbs").innerHTML = "";
  el("custom-prompt").value = "";

  el("btn-analyze").disabled = true;

  // Screen 3 커스텀 UI 초기화
  el("base-original").classList.add("active");
  el("base-current").classList.remove("active");
  document.querySelectorAll(".cat-tag").forEach(b => b.classList.remove("active"));

  showScreen("upload");
}

function renderResult() {
  const a = state.analysis;
  if (!a) return;

  el("result-title").textContent = `${a.name}님의 외모는`;
  el("result-score").textContent = `${a.style_score} / 10 점`;
  el("result-vibe").textContent = a.vibe;
  el("result-reason").textContent = a.vibe_reason || "";

  const tips = el("tips");
  tips.innerHTML = "";
  (a.styling_tips || []).forEach(t => {
    const li = document.createElement("li");
    li.textContent = t;
    tips.appendChild(li);
  });

  const options = Array.isArray(a.style_options) ? a.style_options : [];
  const box = el("options");
  box.innerHTML = "";

  const fallback = [
    { key: "clean", title: "클린", summary: "단정하고 밝은 프로필 톤", edit_prompt: "깔끔한 조명과 단정한 헤어로 정리. 배경은 심플." },
    { key: "street", title: "스트릿", summary: "힙한 캐주얼 무드", edit_prompt: "스트릿 캐주얼 무드. 오버핏 느낌, 색감 세련되게." },
    { key: "formal", title: "포멀", summary: "정돈된 포멀 프로필", edit_prompt: "포멀한 프로필 톤. 차분하고 단정하게." },
    { key: "dark_chic", title: "다크시크", summary: "세련된 다크 무드", edit_prompt: "다크 컬러 톤 의상. 블랙 위주 세련된 스타일링." },
    { key: "soft_natural", title: "소프트내추럴", summary: "부드럽고 자연스러운 룩", edit_prompt: "내추럴한 소프트 톤. 베이지/화이트 계열 편안한 스타일." },
  ];
  const used = options.length ? options : fallback;

  const defaultKey = a.default_style_key || used[0].key;
  state.selectedStyleKey = state.selectedStyleKey || defaultKey;

  used.forEach(opt => {
    const div = document.createElement("div");
    div.className = "opt" + (opt.key === state.selectedStyleKey ? " active" : "");
    div.dataset.key = opt.key;

    // 쇼핑 아이템 HTML (카드 내부에 직접 렌더)
    const shopItems = state.shoppingSets?.[opt.key]?.items || [];
    let shopHtml = "";
    if (state.shoppingLoading) {
      shopHtml = `<div class="opt-shop-loading"><div class="spinner" style="width:18px;height:18px;border-width:2px;"></div><span>아이템 검색 중...</span></div>`;
    } else if (shopItems.length > 0) {
      const itemsHtml = shopItems.map(item => `
        <a class="opt-shop-item" href="${item.link}" target="_blank" rel="noopener" onclick="event.stopPropagation()">
          <img src="${item.image}" alt="${item.name}" class="opt-shop-img" onerror="this.style.display='none'">
          <div class="opt-shop-cat">${item.category}</div>
          <div class="opt-shop-name">${item.name}</div>
          <div class="opt-shop-price">${Number(item.price).toLocaleString("ko-KR")}원</div>
          <div class="opt-shop-mall">${item.mall}</div>
        </a>
      `).join("");
      shopHtml = `
        <div class="opt-shop-row">
          <div class="opt-shop-label">추천 아이템 <span class="opt-shop-hint">클릭하면 구매 페이지</span></div>
          <div class="opt-shop-cards">${itemsHtml}</div>
          <button class="btn primary opt-apply-btn" data-key="${opt.key}" type="button" onclick="event.stopPropagation()">이 스타일로 이미지 생성</button>
        </div>`;
    }

    div.innerHTML = `
      <div class="opt-title">${opt.title}</div>
      <div class="opt-desc">${opt.summary || ""}</div>
      ${shopHtml}
    `;

    div.addEventListener("click", () => {
      state.selectedStyleKey = opt.key;
      state.selectedShoppingSetKey = opt.key;
      renderResult();
    });

    box.appendChild(div);
  });

  // 각 "이 스타일로 이미지 생성" 버튼 이벤트
  box.querySelectorAll(".opt-apply-btn").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      const key = btn.dataset.key;
      const items = state.shoppingSets?.[key]?.items || [];
      applyShoppingStyle(items, key);
    });
  });
}

function renderShoppingSection() {
  // 쇼핑 아이템이 각 opt 카드 안으로 이동했으므로 이 섹션은 비움
  const container = el("shopping-section");
  if (container) container.innerHTML = "";
}

async function fetchShoppingSets(analysisResult) {
  if (!analysisResult?.style_options?.length) return;

  state.shoppingLoading = true;
  renderShoppingSection();

  try {
    const res = await fetch(`${API_BASE}/api/shopping`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        style_options: analysisResult.style_options,
        gender: state.gender,
      }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.shoppingSets = data.sets;
  } catch (e) {
    console.warn("[fetchShoppingSets] 쇼핑 API 실패 (기존 기능 영향 없음):", e);
    state.shoppingSets = null;
  } finally {
    state.shoppingLoading = false;
    renderResult(); // 쇼핑 아이템을 각 opt 카드에 반영
  }
}

async function applyShoppingStyle(items, key) {
  const styleOption = state.analysis?.style_options?.find(
    o => o.key === (key || state.selectedShoppingSetKey || state.selectedStyleKey)
  );
  if (!styleOption) return;

  const fd = new FormData();
  fd.append("image", state.originalFile);
  fd.append("edit_prompt", styleOption.edit_prompt);
  fd.append("shopping_items", JSON.stringify(items));

  showLoading(true);
  try {
    const res = await fetch(`${API_BASE}/api/apply`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const id = `shopping-${Date.now()}`;

    state.generated.push({
      id,
      key: "shopping",
      title: "쇼핑 세트",
      url,
      prompt: styleOption.edit_prompt,
    });

    state.selectedGeneratedId = id;
    setPreview("preview-styled", url);
    renderStyledThumbs();
    showScreen("styled");
  } catch (e) {
    alert("이미지 생성 중 오류가 발생했습니다: " + e.message);
  } finally {
    showLoading(false);
  }
}

function updateStyledDesc(g) {
  const desc = el("styled-desc");
  if (!desc || !g) return;
  const parts = [];
  if (g.title) parts.push(g.title);
  if (g.prompt && g.prompt !== g.title) parts.push(g.prompt);
  desc.textContent = parts.join(" — ");
}

function renderStyledThumbs() {
  const thumbs = el("thumbs");
  thumbs.innerHTML = "";

  state.generated.forEach(g => {
    const div = document.createElement("div");
    div.className = "thumb" + (g.id === state.selectedGeneratedId ? " active" : "");
    div.title = g.title;

    const img = document.createElement("img");
    img.src = g.url;
    img.alt = g.title;

    const label = document.createElement("div");
    label.className = "thumb-label";
    label.textContent = g.title;

    div.appendChild(img);
    div.appendChild(label);
    div.addEventListener("click", () => {
      state.selectedGeneratedId = g.id;
      setPreview("preview-styled", g.url);
      updateStyledDesc(g);
      renderStyledThumbs();
    });

    thumbs.appendChild(div);
  });

  // 현재 선택된 항목의 설명 업데이트
  const current = state.generated.find(g => g.id === state.selectedGeneratedId);
  if (current) updateStyledDesc(current);
}

async function fileToBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]); // data:...;base64, 제거
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function blobUrlToBase64(url) {
  const res = await fetch(url);
  const blob = await res.blob();
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

async function sendResultEmail(result, email) {
  // 원본 사진 base64
  let originalB64 = null;
  if (state.originalFile) {
    originalB64 = await fileToBase64(state.originalFile).catch(() => null);
  }

  // 모든 생성 이미지 base64 배열
  const generatedImages = [];
  for (const g of state.generated) {
    if (g.url) {
      const b64 = await blobUrlToBase64(g.url).catch(() => null);
      if (b64) {
        generatedImages.push({ image: b64, label: g.title || "", prompt: g.prompt || "" });
      }
    }
  }

  const res = await fetch(`${API_BASE}/api/send-result`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email,
      result,
      original_image: originalB64,
      generated_images: generatedImages,
      shopping_sets: state.shoppingSets || null,
    }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return true;
}

async function postAnalyze() {
  const fd = new FormData();
  fd.append("image", state.originalFile);
  fd.append("name", state.name);
  if (state.gender) fd.append("gender", state.gender);
  if (state.age !== null) fd.append("age", state.age);

  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    body: fd,
  });

  if (!res.ok) {
    let body = await res.text();
    const err = new Error(body);
    try {
      const parsed = JSON.parse(body);
      err.code = parsed.detail;
    } catch {}
    throw err;
  }
  return await res.json();
}

async function postApply(editPrompt, file = null) {
  const fd = new FormData();
  fd.append("image", file || state.originalFile);
  fd.append("edit_prompt", editPrompt);

  const res = await fetch(`${API_BASE}/api/apply`, {
    method: "POST",
    body: fd,
  });

  if (!res.ok) {
    let msg = await res.text();
    throw new Error(msg);
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  return url;
}

// 선택된 기준 이미지 파일 반환 (원본 또는 현재 생성 이미지)
async function getBaseFile() {
  if (state.selectedBase === "current" && state.selectedGeneratedId) {
    const gen = state.generated.find(g => g.id === state.selectedGeneratedId);
    if (gen?.url) {
      try {
        const res = await fetch(gen.url);
        const blob = await res.blob();
        return new File([blob], "base.jpg", { type: "image/jpeg" });
      } catch {}
    }
  }
  return state.originalFile;
}

// 카테고리 선택 + 텍스트 입력을 조합해 최종 프롬프트 생성
function buildCustomPrompt() {
  const text = (el("custom-prompt").value || "").trim();
  const cats = state.selectedCategories;
  if (cats.length && text) return `변경 대상: ${cats.join(", ")}. ${text}`;
  if (cats.length) return `${cats.join(", ")} 부분을 스타일링해줘. 나머지는 그대로 유지.`;
  return text;
}

function getSelectedOption() {
  const a = state.analysis;
  if (!a || !Array.isArray(a.style_options)) return null;
  return a.style_options.find(o => o.key === state.selectedStyleKey) || a.style_options[0] || null;
}

function makePersonKey(file) {
  return `${file.name}_${file.size}_${file.lastModified}`;
}

// Camera (mirror like selfie)
async function startCamera() {
  try {
    camStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user" },
      audio: false,
    });
    cameraVideo.srcObject = camStream;
    cameraVideo.classList.remove("hidden");
    cameraVideo.style.transform = "scaleX(-1)";

    el("preview-upload").classList.add("hidden");
    el("placeholder").classList.add("hidden");

    el("camera-actions").classList.remove("hidden");
  } catch (e) {
    alert("카메라 권한/장치를 확인해줘: " + e);
  }
}

function stopCamera() {
  if (camStream) {
    camStream.getTracks().forEach(t => t.stop());
    camStream = null;
  }
  cameraVideo.classList.add("hidden");
  el("camera-actions").classList.add("hidden");
}

function takePhotoFromCamera() {
  if (!cameraVideo || !camStream) return;

  const w = cameraVideo.videoWidth;
  const h = cameraVideo.videoHeight;

  cameraCanvas.width = w;
  cameraCanvas.height = h;

  const ctx = cameraCanvas.getContext("2d");
  // 카메라가 mirror로 보이므로, 캔버스도 mirror로 그려서
  // "찍은 순간 반대로 보이는 현상" 없애기
  ctx.save();
  ctx.translate(w, 0);
  ctx.scale(-1, 1);
  ctx.drawImage(cameraVideo, 0, 0, w, h);
  ctx.restore();

  cameraCanvas.toBlob((blob) => {
    if (!blob) return;

    const file = new File([blob], `camera_${Date.now()}.jpg`, { type: "image/jpeg" });

    // file input과 동일한 흐름으로 처리
    state.originalFile = file;

    state.personKey = makePersonKey(file);
    if (!state.generatedByPerson.has(state.personKey)) state.generatedByPerson.set(state.personKey, []);
    state.generated = state.generatedByPerson.get(state.personKey);

    state.analysis = null;
    state.selectedStyleKey = null;
    state.selectedGeneratedId = null;

    if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
    state.originalUrl = URL.createObjectURL(file);

    setPreview("preview-upload", state.originalUrl);
    el("preview-upload").classList.remove("hidden");
    cameraVideo.classList.add("hidden");

    enableAnalyzeButton();
  }, "image/jpeg", 0.95);
}

btnCamStart?.addEventListener("click", startCamera);
btnCamStop?.addEventListener("click", stopCamera);
btnCamShot?.addEventListener("click", takePhotoFromCamera);

el("file").addEventListener("change", (e) => {
  const file = e.target.files?.[0] || null;
  if (!file) return;

  state.originalFile = file;
  state.personKey = makePersonKey(file);

  if (!state.generatedByPerson.has(state.personKey)) {
    state.generatedByPerson.set(state.personKey, []);
  }
  state.generated = state.generatedByPerson.get(state.personKey);

  state.analysis = null;
  state.selectedStyleKey = null;
  state.selectedGeneratedId = null;

  if (state.originalUrl) URL.revokeObjectURL(state.originalUrl);
  state.originalUrl = URL.createObjectURL(file);

  el("placeholder").classList.add("hidden");
  setPreview("preview-upload", state.originalUrl);
  enableAnalyzeButton();
});

el("gender-m").addEventListener("click", () => {
  state.gender = "m";
  el("gender-m").classList.add("active");
  el("gender-f").classList.remove("active");
});

el("gender-f").addEventListener("click", () => {
  state.gender = "f";
  el("gender-f").classList.add("active");
  el("gender-m").classList.remove("active");
});

el("name").addEventListener("input", (e) => {
  state.name = (e.target.value || "000").trim() || "000";
});

el("email").addEventListener("input", (e) => {
  state.email = (e.target.value || "").trim();
});


el("btn-analyze").addEventListener("click", async () => {
  if (!state.originalFile) return;

  showLoading(true);
  try {
    state.analysis = await postAnalyze();
    setPreview("preview-result", state.originalUrl);

    state.selectedStyleKey = state.analysis.default_style_key || "clean";
    state.selectedShoppingSetKey = state.selectedStyleKey;
    renderResult();

    showScreen("result");
    fetchShoppingSets(state.analysis); // await 없이 병렬 실행
  } catch (err) {
    console.error(err);
    if (err.code === "NO_FACE_DETECTED") {
      showLoading(false);
      alert("얼굴이 정확하게 나오게 다시 촬영해주세요.");
      resetAll();
      return;
    }
    alert("분석 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

el("btn-send-email").addEventListener("click", async () => {
  if (!state.analysis) {
    alert("분석 결과가 없습니다. 먼저 진단을 완료해주세요.");
    return;
  }

  let email = state.email || el("email").value.trim();
  if (!email) {
    alert("이메일 주소를 위 입력란에 먼저 입력해주세요.");
    el("email").focus();
    return;
  }

  const btn = el("btn-send-email");
  btn.disabled = true;
  btn.textContent = "전송중...";

  try {
    await sendResultEmail(state.analysis, email);
    btn.textContent = "✓ 전송 완료";
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = "이메일로 결과 받기";
    }, 3000);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "이메일로 결과 받기";
    alert("이메일 발송 실패: " + e.message);
  }
});

el("btn-back-upload").addEventListener("click", () => {
  resetAll();
});


el("btn-custom").addEventListener("click", async () => {
  const p = buildCustomPrompt();
  if (!p) {
    alert("변경 대상을 선택하거나 스타일을 입력해줘.");
    return;
  }

  const baseFile = await getBaseFile();

  showLoading(true);
  try {
    const url = await postApply(p, baseFile);
    const id = `custom-${Date.now()}`;
    const baseLabel = state.selectedBase === "current" ? "현재이미지 기준" : "원본 기준";
    const catLabel = state.selectedCategories.length ? state.selectedCategories.join("+") : "커스텀";

    state.generated.push({
      id,
      key: "custom",
      title: `${catLabel} (${baseLabel})`,
      url,
      prompt: p,
    });

    state.selectedGeneratedId = id;
    setPreview("preview-styled", url);
    renderStyledThumbs();
    updateTipApplyButton();
  } catch (err) {
    console.error(err);
    alert("커스텀 생성 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

el("btn-tip-apply").addEventListener("click", async () => {
  const p = (el("tip-prompt").value || "").trim();
  if (!p) {
    alert("원하는 스타일을 입력해줘.");
    return;
  }

  showLoading(true);
  try {
    const url = await postApply(p);
    const id = `tip-${Date.now()}`;

    state.generated.push({
      id,
      key: "tip",
      title: "팁 적용",
      url,
      prompt: p,
    });

    state.selectedGeneratedId = id;
    el("tip-prompt").value = "";
    setPreview("preview-styled", url);
    renderStyledThumbs();
    updateTipApplyButton();
    showScreen("styled");
  } catch (err) {
    console.error(err);
    alert("스타일링 생성 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

// 기준 이미지 선택
el("base-original").addEventListener("click", () => {
  state.selectedBase = "original";
  el("base-original").classList.add("active");
  el("base-current").classList.remove("active");
});

el("base-current").addEventListener("click", () => {
  state.selectedBase = "current";
  el("base-current").classList.add("active");
  el("base-original").classList.remove("active");
});

// 변경 대상 카테고리 태그
document.querySelectorAll(".cat-tag").forEach(btn => {
  btn.addEventListener("click", () => {
    const cat = btn.dataset.cat;
    if (state.selectedCategories.includes(cat)) {
      state.selectedCategories = state.selectedCategories.filter(c => c !== cat);
      btn.classList.remove("active");
    } else {
      state.selectedCategories.push(cat);
      btn.classList.add("active");
    }
  });
});

el("btn-back-result").addEventListener("click", () => {
  setPreview("preview-result", state.originalUrl);
  renderResult();
  updateTipApplyButton();
  showScreen("result");
});

el("btn-back-upload-2")?.addEventListener("click", () => {
  resetAll();
});

el("btn-view-generated")?.addEventListener("click", () => {
  if (!state.generated || state.generated.length === 0) {
    alert("아직 생성된 이미지가 없습니다.");
    return;
  }
  const latest = state.generated[state.generated.length - 1];
  state.selectedGeneratedId = latest.id;
  setPreview("preview-styled", latest.url);
  renderStyledThumbs();
  updateStyledDesc(latest);
  showScreen("styled");
});