const API_BASE = "http://127.0.0.1:8000";

const el = (id) => document.getElementById(id);
const screens = {
  upload: el("screen-upload"),
  result: el("screen-result"),
  styled: el("screen-styled"),
};
const loading = el("loading");

// camera elements
const video = el("camera");
const canvas = el("camera-canvas");
const cameraActions = el("camera-actions");
const btnCameraStart = el("btn-camera-start");
const btnCameraCapture = el("btn-camera-capture");
const btnCameraStop = el("btn-camera-stop");

let cameraStream = null;

const state = {
  name: "000",
  age: null,           // ✅ 추가
  gender: null,        // ✅ 추가: "male" | "female" (둘 중 하나 선택)
  originalFile: null,
  originalUrl: null,
  analysis: null,
  selectedStyleKey: null,

  personKey: null,
  generated: [],
  selectedGeneratedId: null,
};

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
  // ✅ name은 없어도 되지만 사진은 필수, gender/age는 권장이라 필수로 잠그진 않음
  btn.disabled = !state.originalFile;
}

function clearGeneratedForCurrentPerson() {
  state.generated.forEach(g => {
    try { URL.revokeObjectURL(g.url); } catch {}
  });
  state.generated = [];
  state.selectedGeneratedId = null;
}

function resetToUploadView() {
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
  ];
  const used = options.length ? options : fallback;

  const defaultKey = a.default_style_key || used[0].key;
  state.selectedStyleKey = state.selectedStyleKey || defaultKey;

  used.forEach(opt => {
    const div = document.createElement("div");
    div.className = "opt" + (opt.key === state.selectedStyleKey ? " active" : "");
    div.dataset.key = opt.key;

    div.innerHTML = `
      <div class="opt-title">${opt.title}</div>
      <div class="opt-desc">${opt.summary || ""}</div>
    `;

    div.addEventListener("click", () => {
      state.selectedStyleKey = opt.key;
      renderResult();
    });

    box.appendChild(div);
  });
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

    div.appendChild(img);
    div.addEventListener("click", () => {
      state.selectedGeneratedId = g.id;
      setPreview("preview-styled", g.url);
      renderStyledThumbs();
    });

    thumbs.appendChild(div);
  });
}

async function postAnalyze() {
  const fd = new FormData();
  fd.append("image", state.originalFile);
  fd.append("name", state.name);

  // ✅ 추가: age/gender 전달
  if (state.age !== null) fd.append("age", String(state.age));
  if (state.gender) fd.append("gender", state.gender); // "male" | "female"

  const res = await fetch(`${API_BASE}/api/analyze`, {
    method: "POST",
    body: fd,
  });

  if (!res.ok) {
    let msg = await res.text();
    throw new Error(msg);
  }
  return await res.json();
}

async function postApply(editPrompt) {
  const fd = new FormData();
  fd.append("image", state.originalFile);
  fd.append("edit_prompt", editPrompt);

  // (선택) 이미지 생성에도 age/gender를 같이 보내고 싶다면 백엔드에 추가한 뒤 아래 주석 해제
  // if (state.age !== null) fd.append("age", String(state.age));
  // if (state.gender) fd.append("gender", state.gender);

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

function getSelectedOption() {
  const a = state.analysis;

  const fallback = [
    { key: "clean", title: "클린", summary: "단정하고 밝은 프로필 톤", edit_prompt: "깔끔한 조명과 단정한 헤어로 정리. 배경은 심플." },
    { key: "street", title: "스트릿", summary: "힙한 캐주얼 무드", edit_prompt: "스트릿 캐주얼 무드. 오버핏 느낌, 색감 세련되게." },
    { key: "formal", title: "포멀", summary: "정돈된 포멀 프로필", edit_prompt: "포멀한 프로필 톤. 차분하고 단정하게." },
  ];

  const used = (a && Array.isArray(a.style_options) && a.style_options.length)
    ? a.style_options
    : fallback;

  return used.find(o => o.key === state.selectedStyleKey) || used[0];
}

function makePersonKey() {
  return `p_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function setNewOriginalFile(file) {
  clearGeneratedForCurrentPerson();

  state.personKey = makePersonKey();
  state.originalFile = file;

  state.analysis = null;
  state.selectedStyleKey = null;
  state.selectedGeneratedId = null;

  if (state.originalUrl) {
    try { URL.revokeObjectURL(state.originalUrl); } catch {}
  }
  state.originalUrl = URL.createObjectURL(file);

  el("placeholder").classList.add("hidden");

  video.classList.add("hidden");
  el("preview-upload").classList.remove("hidden");
  setPreview("preview-upload", state.originalUrl);

  enableAnalyzeButton();
}

function showCameraUI(on) {
  cameraActions.classList.toggle("hidden", !on);
  btnCameraStart.classList.toggle("hidden", on);
}

async function startCamera() {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "user" },
    audio: false,
  });
  cameraStream = stream;
  video.srcObject = stream;
  await video.play();

  el("placeholder").classList.add("hidden");
  el("preview-upload").classList.add("hidden");
  video.classList.remove("hidden");
  showCameraUI(true);
}

function stopCamera() {
  if (cameraStream) {
    cameraStream.getTracks().forEach(t => t.stop());
    cameraStream = null;
  }
  video.srcObject = null;
  showCameraUI(false);

  if (state.originalUrl) {
    el("placeholder").classList.add("hidden");
    el("preview-upload").classList.remove("hidden");
    video.classList.add("hidden");
    setPreview("preview-upload", state.originalUrl);
  } else {
    video.classList.add("hidden");
    el("preview-upload").classList.add("hidden");
    el("placeholder").classList.remove("hidden");
  }
}

async function captureFromCamera() {
  if (!video || video.classList.contains("hidden")) return;

  const w = video.videoWidth;
  const h = video.videoHeight;
  if (!w || !h) {
    alert("카메라 준비 중이에요. 잠시 후 다시 촬영해줘.");
    return;
  }

  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(video, 0, 0, w, h);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) {
    alert("촬영 실패: blob 생성에 실패했어.");
    return;
  }

  const file = new File([blob], `capture_${Date.now()}.jpg`, { type: "image/jpeg", lastModified: Date.now() });
  setNewOriginalFile(file);
  stopCamera();
}

// ------------------
// Gender buttons
// ------------------
function setGender(g) {
  state.gender = g; // "male" | "female"
  el("gender-m").classList.toggle("active", g === "male");
  el("gender-f").classList.toggle("active", g === "female");
}
el("gender-m").addEventListener("click", () => setGender("male"));
el("gender-f").addEventListener("click", () => setGender("female"));

// ------------------
// Events
// ------------------
el("file").addEventListener("change", (e) => {
  const file = e.target.files?.[0] || null;
  if (!file) return;
  stopCamera();
  setNewOriginalFile(file);
});

el("name").addEventListener("input", (e) => {
  state.name = (e.target.value || "000").trim() || "000";
});

el("age").addEventListener("input", (e) => {
  const raw = (e.target.value || "").replace(/[^\d]/g, "");
  e.target.value = raw;
  if (!raw) {
    state.age = null;
    return;
  }
  const n = Number(raw);
  // 너무 비정상 값은 입력만 받고 서버에 안 보내도 됨(여기선 간단히 클램프)
  state.age = Math.max(1, Math.min(120, n));
});

el("btn-analyze").addEventListener("click", async () => {
  if (!state.originalFile) return;

  showLoading(true);
  try {
    state.analysis = await postAnalyze();
    setPreview("preview-result", state.originalUrl);

    state.selectedStyleKey = state.analysis.default_style_key || "clean";
    renderResult();
    showScreen("result");
  } catch (err) {
    console.error(err);
    alert("분석 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

el("btn-back-upload").addEventListener("click", () => {
  resetToUploadView();
  if (state.originalUrl) {
    el("placeholder").classList.add("hidden");
    setPreview("preview-upload", state.originalUrl);
    enableAnalyzeButton();
  }
});

el("btn-view-generated").addEventListener("click", () => {
  if (!state.generated.length) {
    alert("아직 생성된 이미지가 없어요. '스타일링 적용 이미지 생성하기'로 먼저 생성해줘!");
    return;
  }
  const selected = state.generated.find(g => g.id === state.selectedGeneratedId) || state.generated[0];
  state.selectedGeneratedId = selected.id;
  setPreview("preview-styled", selected.url);
  renderStyledThumbs();
  showScreen("styled");
});

el("btn-go-styled").addEventListener("click", async () => {
  const opt = getSelectedOption();
  if (!opt) {
    alert("스타일 옵션이 없습니다. 먼저 분석을 완료해줘.");
    return;
  }

  const existing = state.generated.find(g => g.key === opt.key);
  if (existing) {
    state.selectedGeneratedId = existing.id;
    setPreview("preview-styled", existing.url);
    renderStyledThumbs();
    showScreen("styled");
    return;
  }

  showLoading(true);
  try {
    const url = await postApply(opt.edit_prompt);

    const id = `${opt.key}-${Date.now()}`;
    state.generated.push({
      id,
      key: opt.key,
      title: opt.title,
      url,
      prompt: opt.edit_prompt,
    });

    state.selectedGeneratedId = id;
    setPreview("preview-styled", url);
    renderStyledThumbs();
    showScreen("styled");
  } catch (err) {
    console.error(err);
    alert("스타일링 생성 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

el("btn-custom").addEventListener("click", async () => {
  const p = (el("custom-prompt").value || "").trim();
  if (!p) {
    alert("원하는 스타일을 입력해줘.");
    return;
  }

  showLoading(true);
  try {
    const url = await postApply(p);

    const id = `custom-${Date.now()}`;
    state.generated.push({
      id,
      key: "custom",
      title: "커스텀",
      url,
      prompt: p,
    });

    state.selectedGeneratedId = id;
    setPreview("preview-styled", url);
    renderStyledThumbs();
  } catch (err) {
    console.error(err);
    alert("커스텀 생성 실패: " + String(err.message || err));
  } finally {
    showLoading(false);
  }
});

el("btn-back-result").addEventListener("click", () => {
  setPreview("preview-result", state.originalUrl);
  renderResult();
  showScreen("result");
});

// Camera
btnCameraStart.addEventListener("click", async () => {
  try {
    await startCamera();
  } catch (e) {
    console.error(e);
    alert("카메라를 켤 수 없어요. 브라우저 권한(카메라 허용)과 https/localhost 환경을 확인해줘.");
  }
});

btnCameraCapture.addEventListener("click", async () => {
  try {
    await captureFromCamera();
  } catch (e) {
    console.error(e);
    alert("촬영 실패: " + String(e.message || e));
  }
});

btnCameraStop.addEventListener("click", () => stopCamera());

window.addEventListener("beforeunload", () => {
  stopCamera();
  if (state.originalUrl) {
    try { URL.revokeObjectURL(state.originalUrl); } catch {}
  }
  clearGeneratedForCurrentPerson();
});
