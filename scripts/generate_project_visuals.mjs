/*
 * Deterministically composes the project posters from the checked-in stack
 * inventory and locally stored SVG brand marks.  The PNGs are convenience
 * exports; SVG is the canonical, editable source.
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const sharp = require("sharp");
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const visualDir = path.join(root, "docs", "visuals");
const logoDir = path.join(visualDir, "logos");
const backgroundPath = path.join(visualDir, "poster-background.png");
const W = 1920;
const H = 1080;
const C = {
  ink: "#292724",
  muted: "#716d66",
  line: "#c8c0b1",
  softLine: "#ded7c9",
  card: "#fbf8f1",
  cream: "#f6f1e7",
  green: "#03a66a",
  blue: "#2187d6",
  purple: "#6c5ce7",
  orange: "#e47c2c",
  red: "#c95656",
  teal: "#138d8a",
};
const font = "'Malgun Gothic','Noto Sans KR','Apple SD Gothic Neo',Arial,sans-serif";
const mono = "'Cascadia Mono','Consolas','Malgun Gothic',monospace";

function esc(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function enc(file) {
  const raw = fs.readFileSync(file);
  return `data:image/${path.extname(file).slice(1)};base64,${raw.toString("base64")}`;
}

const logos = Object.fromEntries(
  fs.readdirSync(logoDir)
    .filter((name) => name.endsWith(".svg"))
    .map((name) => [path.basename(name, ".svg"), enc(path.join(logoDir, name))]),
);
const logoRefs = Object.fromEntries(
  fs.readdirSync(logoDir)
    .filter((name) => name.endsWith(".svg"))
    .map((name) => [path.basename(name, ".svg"), `logos/${name}`]),
);
const background = fs.existsSync(backgroundPath) ? enc(backgroundPath) : null;
let embedAssets = true;

function text(x, y, value, size, opts = {}) {
  const anchor = opts.anchor || "start";
  const weight = opts.weight || 400;
  const fill = opts.fill || C.ink;
  const family = opts.family || font;
  const letter = opts.letter || 0;
  return `<text x="${x}" y="${y}" text-anchor="${anchor}" font-family="${family}" font-size="${size}" font-weight="${weight}" letter-spacing="${letter}" fill="${fill}">${esc(value)}</text>`;
}

function multiline(x, y, lines, size, opts = {}) {
  const lineHeight = opts.lineHeight || Math.round(size * 1.42);
  return lines.map((line, index) => text(x, y + index * lineHeight, line, size, opts)).join("");
}

function poster(title, subtitle, content, footerLeft, footerRight = "FINANCIAL PRODUCT AGENT · MAIN") {
  const bg = background
    ? `<image href="${embedAssets ? background : "poster-background.png"}" x="0" y="0" width="${W}" height="${H}" preserveAspectRatio="none" opacity="0.58"/>`
    : `<rect width="${W}" height="${H}" fill="${C.cream}"/>`;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <rect width="${W}" height="${H}" fill="${C.cream}"/>
  ${bg}
  <rect width="${W}" height="${H}" fill="#fffdf8" opacity="0.15"/>
  ${text(68, 80, title, 60, { weight: 800, letter: -2 })}
  ${text(70, 127, subtitle, 21, { fill: C.muted, weight: 450 })}
  ${text(1850, 80, "MIRAE ASSET AI FESTIVAL", 16, { anchor: "end", fill: C.muted, weight: 650, letter: 0.5 })}
  ${text(1850, 108, "FINANCIAL PRODUCT AGENT", 16, { anchor: "end", fill: C.muted, weight: 650, letter: 0.5 })}
  <line x1="0" y1="168" x2="1920" y2="168" stroke="${C.ink}" stroke-width="2" opacity="0.85"/>
  ${content}
  <line x1="68" y1="1000" x2="1852" y2="1000" stroke="${C.line}" stroke-width="1.4"/>
  ${text(68, 1035, footerLeft, 15, { fill: C.muted, weight: 500 })}
  ${text(1852, 1035, `${footerRight} · VISUAL KIT`, 15, { anchor: "end", fill: C.muted, weight: 500 })}
</svg>`;
}

function panel(x, y, w, h, title, subtitle = "") {
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="18" fill="${C.card}" fill-opacity="0.82" stroke="${C.softLine}" stroke-width="1.5"/>
  ${text(x + 28, y + 42, title, 26, { weight: 760, letter: -0.5 })}
  ${subtitle ? text(x + 28, y + 70, subtitle, 14, { fill: C.muted }) : ""}`;
}

function chip(x, y, label, color = C.ink, style = {}) {
  const width = style.width || Math.max(88, label.length * (style.factor || 9.2) + 34);
  const height = style.height || 28;
  return `<rect x="${x}" y="${y}" width="${width}" height="${height}" rx="${height / 2}" fill="${color}" fill-opacity="0.10" stroke="${color}" stroke-opacity="0.48"/>
  ${text(x + width / 2, y + 19, label, style.size || 12, { anchor: "middle", weight: 720, fill: color })}`;
}

function actualLogo(name, x, y, size) {
  const source = (embedAssets ? logos : logoRefs)[name];
  if (!source) return customMark("dot", x, y, size, C.ink);
  return `<image href="${source}" x="${x}" y="${y}" width="${size}" height="${size}" preserveAspectRatio="xMidYMid meet"/>`;
}

function customMark(kind, x, y, size, color = C.ink) {
  const c = color;
  const s = size;
  if (kind === "ledger") {
    return `<rect x="${x + s * 0.18}" y="${y + s * 0.08}" width="${s * 0.64}" height="${s * 0.82}" rx="${s * 0.08}" fill="none" stroke="${c}" stroke-width="${s * 0.07}"/>
      <path d="M${x + s * 0.32} ${y + s * 0.34}l${s * 0.08} ${s * 0.08} ${s * 0.18} -${s * 0.18} M${x + s * 0.32} ${y + s * 0.58}l${s * 0.08} ${s * 0.08} ${s * 0.18} -${s * 0.18} M${x + s * 0.32} ${y + s * 0.80}h${s * 0.34}" fill="none" stroke="${c}" stroke-width="${s * 0.065}" stroke-linecap="round" stroke-linejoin="round"/>`;
  }
  if (kind === "graph") {
    return `<path d="M${x + s * 0.22} ${y + s * 0.74}L${x + s * 0.48} ${y + s * 0.28}L${x + s * 0.78} ${y + s * 0.62} M${x + s * 0.48} ${y + s * 0.28}L${x + s * 0.73} ${y + s * 0.20}" stroke="${c}" stroke-width="${s * 0.075}" fill="none" stroke-linecap="round"/>
    <circle cx="${x + s * 0.22}" cy="${y + s * 0.74}" r="${s * 0.12}" fill="${c}"/><circle cx="${x + s * 0.48}" cy="${y + s * 0.28}" r="${s * 0.12}" fill="${c}"/><circle cx="${x + s * 0.78}" cy="${y + s * 0.62}" r="${s * 0.12}" fill="${c}"/><circle cx="${x + s * 0.73}" cy="${y + s * 0.20}" r="${s * 0.12}" fill="${c}"/>`;
  }
  if (kind === "search") {
    return `<circle cx="${x + s * 0.43}" cy="${y + s * 0.42}" r="${s * 0.23}" fill="none" stroke="${c}" stroke-width="${s * 0.09}"/><path d="M${x + s * 0.61} ${y + s * 0.61}L${x + s * 0.84} ${y + s * 0.84}" stroke="${c}" stroke-width="${s * 0.10}" stroke-linecap="round"/>`;
  }
  if (kind === "vector") {
    return `<circle cx="${x + s * 0.25}" cy="${y + s * 0.27}" r="${s * 0.10}" fill="${c}"/><circle cx="${x + s * 0.72}" cy="${y + s * 0.24}" r="${s * 0.10}" fill="${c}"/><circle cx="${x + s * 0.50}" cy="${y + s * 0.70}" r="${s * 0.10}" fill="${c}"/><path d="M${x + s * 0.31} ${y + s * 0.31}L${x + s * 0.65} ${y + s * 0.28}L${x + s * 0.52} ${y + s * 0.62}Z" fill="none" stroke="${c}" stroke-width="${s * 0.06}"/>`;
  }
  if (kind === "lock") {
    return `<rect x="${x + s * 0.20}" y="${y + s * 0.43}" width="${s * 0.60}" height="${s * 0.42}" rx="${s * 0.06}" fill="none" stroke="${c}" stroke-width="${s * 0.075}"/><path d="M${x + s * 0.32} ${y + s * 0.43}v-${s * 0.14}a${s * 0.18} ${s * 0.18} 0 0 1 ${s * 0.36} 0v${s * 0.14}" fill="none" stroke="${c}" stroke-width="${s * 0.075}" stroke-linecap="round"/><circle cx="${x + s * 0.50}" cy="${y + s * 0.62}" r="${s * 0.055}" fill="${c}"/>`;
  }
  if (kind === "database") {
    return `<ellipse cx="${x + s * 0.50}" cy="${y + s * 0.22}" rx="${s * 0.30}" ry="${s * 0.13}" fill="none" stroke="${c}" stroke-width="${s * 0.07}"/><path d="M${x + s * 0.20} ${y + s * 0.22}v${s * 0.47}c0 ${s * 0.17} ${s * 0.60} ${s * 0.17} ${s * 0.60} 0V${y + s * 0.22}M${x + s * 0.20} ${y + s * 0.46}c0 ${s * 0.17} ${s * 0.60} ${s * 0.17} ${s * 0.60} 0" fill="none" stroke="${c}" stroke-width="${s * 0.07}"/>`;
  }
  if (kind === "person") {
    return `<circle cx="${x + s * 0.50}" cy="${y + s * 0.32}" r="${s * 0.15}" fill="none" stroke="${c}" stroke-width="${s * 0.08}"/><path d="M${x + s * 0.22} ${y + s * 0.83}c0-${s * 0.21} ${s * 0.56}-${s * 0.21} ${s * 0.56} 0" fill="none" stroke="${c}" stroke-width="${s * 0.08}" stroke-linecap="round"/>`;
  }
  if (kind === "hcx") {
    return `<path d="M${x + s * 0.22} ${y + s * 0.20}h${s * 0.18}v${s * 0.23}h${s * 0.20}V${y + s * 0.20}h${s * 0.18}v${s * 0.60}h-${s * 0.18}V${y + s * 0.59}h-${s * 0.20}v${s * 0.21}h-${s * 0.18}z" fill="${c}"/><path d="M${x + s * 0.72} ${y + s * 0.27}l${s * 0.09} ${s * 0.10} ${s * 0.14}-${s * 0.16}" stroke="${C.orange}" stroke-width="${s * 0.09}" fill="none" stroke-linecap="round" stroke-linejoin="round"/>`;
  }
  return `<circle cx="${x + s / 2}" cy="${y + s / 2}" r="${s * 0.18}" fill="${c}"/>`;
}

function stackTile(x, y, item) {
  const size = 58;
  const mark = item.logo ? actualLogo(item.logo, x + 42, y + 2, size) : customMark(item.mark, x + 42, y + 2, size, item.color || C.ink);
  return `${mark}${text(x + 71, y + 86, item.name, 16, { anchor: "middle", weight: 760 })}${text(x + 71, y + 108, item.note, 12, { anchor: "middle", fill: C.muted, weight: 480 })}`;
}

function stackPanel(x, y, title, subtitle, items) {
  let body = panel(x, y, 556, 315, title, subtitle);
  // Three-item rows deliberately have more breathing room for names such as
  // HyperCLOVA X and ConditionLedger; four-item rows remain compact.
  const step = items.length === 3 ? 160 : 124;
  const start = items.length === 3 ? 26 : 18;
  items.forEach((item, index) => { body += stackTile(x + start + index * step, y + 108, item); });
  return body;
}

function techStack() {
  const topY = 212;
  const bottomY = 588;
  const a = 68;
  const b = 682;
  const d = 1296;
  const content = `${stackPanel(a, topY, "사용자 경험", "테스터용 React 채팅과 대회 응답 표면", [
    { logo: "react", name: "React 18", note: "채팅 UI" },
    { logo: "typescript", name: "TypeScript", note: "화면 계약" },
    { logo: "vite", name: "Vite", note: "정적 빌드" },
  ])}
  ${stackPanel(b, topY, "Agent API", "명시적 계약과 구조화된 실행 경로", [
    { logo: "python", name: "Python 3.12", note: "엔진 언어" },
    { logo: "fastapi", name: "FastAPI", note: "/answer · QA" },
    { logo: "pydantic", name: "Pydantic", note: "엄격한 계약" },
    { mark: "ledger", name: "Uvicorn", note: "ASGI 실행", color: C.teal },
  ])}
  ${stackPanel(d, topY, "AI 계획 · 안전", "HyperCLOVA X만 런타임 LLM으로 사용", [
    { logo: "naver", name: "HyperCLOVA X", note: "구조화 계획" },
    { mark: "ledger", name: "ConditionLedger", note: "조건 누락 차단", color: C.green },
    { mark: "lock", name: "AES-256-GCM", note: "QA 상태 암호화", color: C.orange },
  ])}
  ${stackPanel(a, bottomY, "공식 데이터", "원본 XLSX → 재현 가능한 serving data", [
    { logo: "pandas", name: "pandas", note: "ETL 변환" },
    { mark: "database", name: "openpyxl", note: "XLSX 원본", color: C.blue },
    { logo: "apachearrow", name: "Apache Arrow", note: "열 지향 데이터" },
    { logo: "duckdb", name: "DuckDB", note: "SQL 권위 원천" },
  ])}
  ${stackPanel(b, bottomY, "Federated Retrieval", "질문 성격에 따라 채널을 조합하고 SQL로 재검증", [
    { mark: "search", name: "Exact / Alias", note: "코드·상품명", color: C.blue },
    { mark: "graph", name: "Graph 1–2 hop", note: "관계 후보", color: C.purple },
    { mark: "search", name: "BM25", note: "전략·퍼지명", color: C.orange },
    { mark: "vector", name: "Vector · RRF", note: "선택적 1024D", color: C.teal },
  ])}
  ${stackPanel(d, bottomY, "검증 · 제공", "품질 검증과 안전한 컨테이너 실행", [
    { logo: "pytest", name: "pytest", note: "회귀 · oracle" },
    { logo: "ruff", name: "Ruff", note: "정적 품질" },
    { logo: "docker", name: "Docker", note: "재현 배포" },
    { logo: "caddy", name: "Caddy", note: "TLS 예시" },
  ])}`;
  return poster(
    "개발 환경 및 기술 스택",
    "공식 데이터 기반 · 증거 중심 · HyperCLOVA X 전용 금융상품 Agent",
    content,
    "개발·협업 도구, 음성 입력, 다른 프로젝트의 로컬망 구성은 의도적으로 제외 · Vector는 유효한 1,024차원 cache가 있을 때만 활성화",
  );
}

function arrow(x1, y1, x2, y2, color = C.ink, width = 3) {
  const head = 12;
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const a1 = angle + Math.PI * 0.83;
  const a2 = angle - Math.PI * 0.83;
  return `<path d="M${x1} ${y1} L${x2} ${y2}" stroke="${color}" stroke-width="${width}" fill="none" stroke-linecap="round"/>
    <path d="M${x2} ${y2} L${x2 + head * Math.cos(a1)} ${y2 + head * Math.sin(a1)} M${x2} ${y2} L${x2 + head * Math.cos(a2)} ${y2 + head * Math.sin(a2)}" stroke="${color}" stroke-width="${width}" fill="none" stroke-linecap="round"/>`;
}

function node(x, y, w, h, title, lines, opts = {}) {
  const color = opts.color || C.ink;
  const mark = opts.logo ? actualLogo(opts.logo, x + 24, y + 28, 46) : opts.mark ? customMark(opts.mark, x + 24, y + 27, 48, color) : "";
  const tx = mark ? x + 86 : x + 24;
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="16" fill="${C.card}" fill-opacity="0.92" stroke="${color}" stroke-opacity="0.55" stroke-width="1.8"/>
    ${mark}${text(tx, y + 52, title, 20, { weight: 800, fill: color })}
    ${multiline(tx, y + 82, lines, 13, { fill: C.muted, weight: 480, lineHeight: 20 })}`;
}

function architecture() {
  const content = `
  ${text(72, 211, "01  요청 표면", 18, { fill: C.muted, weight: 760, letter: 1 })}
  ${node(72, 250, 300, 145, "대회 평가자", ["GET /answer", "다섯 문자열 계약"], { mark: "person", color: C.blue })}
  ${node(72, 442, 300, 145, "팀 테스터", ["내부 인간검증 챗", "대화 · 피드백 · 내보내기"], { logo: "react", color: C.teal })}
  ${node(72, 634, 300, 145, "QA Gateway", ["상태 · 인증 · 암호화", "LLM/답변 생성 없음"], { mark: "lock", color: C.orange })}
  ${arrow(222, 395, 222, 442, C.blue)}
  ${arrow(372, 514, 470, 514, C.teal)}
  ${text(460, 211, "02  증거 중심 Agent 엔진", 18, { fill: C.muted, weight: 760, letter: 1 })}
  ${node(470, 250, 320, 145, "FastAPI 엔트리", ["입력 정규화 · no-store", "정확 코드/상품명 fast path"], { logo: "fastapi", color: C.teal })}
  ${node(870, 250, 320, 145, "HCX 1단계 계획", ["의도·스코프·조건만", "다른 LLM·자동 폴백 없음"], { logo: "naver", color: C.green })}
  ${node(1270, 250, 300, 145, "Grounding", ["허용 필드·온톨로지", "ConditionLedger 생성"], { mark: "ledger", color: C.purple })}
  ${arrow(790, 322, 870, 322, C.ink)}${arrow(1190, 322, 1270, 322, C.ink)}
  ${text(460, 455, "03  RetrievalPlan — 후보 탐색은 역할별, 계산·근거는 SQL이 최종 권위", 18, { fill: C.muted, weight: 760, letter: 0 })}
  ${node(470, 500, 238, 142, "Exact / Alias", ["코드·정확 상품명", "결정적 후보"], { mark: "search", color: C.blue })}
  ${node(735, 500, 238, 142, "Graph 1–2 hop", ["운용사·지역·벤치마크", "role/scope 격리"], { mark: "graph", color: C.purple })}
  ${node(1000, 500, 238, 142, "BM25", ["전략 설명·퍼지명", "lexical 후보"], { mark: "search", color: C.orange })}
  ${node(1265, 500, 305, 142, "Vector + RRF", ["유효 1,024D cache 때만", "없으면 BM25 정상 동작"], { mark: "vector", color: C.teal })}
  ${arrow(1420, 395, 1420, 486, C.purple)}
  ${arrow(589, 642, 767, 720, C.blue, 2)}${arrow(854, 642, 848, 720, C.purple, 2)}${arrow(1119, 642, 928, 720, C.orange, 2)}${arrow(1417, 642, 1005, 720, C.teal, 2)}
  ${node(710, 735, 330, 145, "DuckDB · SQL 재검증", ["필터 · 정렬 · 집계 · 계산", "공식 원본 행과 최종 근거"], { logo: "duckdb", color: C.ink })}
  ${node(1120, 735, 350, 145, "EvidenceBundle · 답변", ["file/sheet/Excel row/field/hash", "질문 · context · trace · answer"], { mark: "ledger", color: C.green })}
  ${arrow(1040, 807, 1120, 807, C.ink)}
  ${chip(484, 908, "공식 데이터만", C.green, { width: 132 })}
  ${chip(636, 908, "교차질의 유지", C.purple, { width: 135 })}
  ${chip(791, 908, "근거 없는 FULL 금지", C.red, { width: 160 })}
  ${chip(971, 908, "HCX-only", C.green, { width: 102 })}
  ${chip(1093, 908, "Vector: 선택적", C.teal, { width: 128 })}
  ${chip(1241, 908, "외부 live gate 대기", C.orange, { width: 155 })}`;
  return poster(
    "전체 시스템 아키텍처",
    "한 번의 답변도 ‘AI가 아는 것’이 아니라 공식 데이터와 검증 가능한 근거로 완성한다",
    content,
    "QA Gateway는 인간검증 전용 보조 서비스이며 답변을 만들거나 다른 LLM을 호출하지 않음 · 실제 HCX/Embedding 공개 운영은 PENDING_EXTERNAL",
  );
}

function flowStep(x, y, n, title, lines, color, mark) {
  return `<circle cx="${x + 26}" cy="${y + 26}" r="26" fill="${color}"/>${text(x + 26, y + 33, n, 17, { anchor: "middle", weight: 800, fill: "#fff" })}
    ${node(x, y + 56, 262, 130, title, lines, { mark, color })}`;
}

function questionFlow() {
  const content = `
  ${text(72, 220, "질문은 바로 문장으로 답하지 않습니다. 먼저 조건이 사라지지 않았는지 확인합니다.", 24, { weight: 680, fill: C.ink })}
  ${flowStep(72, 287, "1", "사용자 질문", ["자연어 · 코드 · 상품명", "대회/챗봇 입력"], C.blue, "person")}
  ${flowStep(390, 287, "2", "의도 판별", ["결정적 코드·정확명", "→ fast path 유지"], C.teal, "search")}
  ${flowStep(708, 287, "3", "HCX 계획", ["의도·스코프·조건", "구조화 출력만 사용"], C.green, "hcx")}
  ${flowStep(1026, 287, "4", "Grounding", ["공식 온톨로지·허용필드", "ConditionLedger로 추적"], C.purple, "ledger")}
  ${flowStep(1344, 287, "5", "RetrievalPlan", ["Exact · Graph · BM25", "Vector는 준비된 경우만"], C.orange, "graph")}
  ${arrow(334, 408, 390, 408, C.ink)}${arrow(652, 408, 708, 408, C.ink)}${arrow(970, 408, 1026, 408, C.ink)}${arrow(1288, 408, 1344, 408, C.ink)}
  ${text(72, 590, "ConditionLedger의 네 가지 결론", 21, { weight: 800 })}
  ${node(72, 630, 380, 140, "grounded", ["공식 필드에 안전하게 연결", "계획을 계속 실행"], { mark: "ledger", color: C.green })}
  ${node(500, 630, 380, 140, "clarification_required", ["결과를 바꾸는 조건이 모호함", "가장 판별력 높은 질문 하나"], { mark: "person", color: C.orange })}
  ${node(928, 630, 380, 140, "unavailable", ["공식 데이터에 값·근거 없음", "추측하지 않고 사유를 설명"], { mark: "search", color: C.red })}
  ${node(1356, 630, 380, 140, "not_comparable", ["단위·기준일·통화가 다름", "차이를 명시해 비교"], { mark: "ledger", color: C.purple })}
  ${arrow(1262, 473, 1262, 578, C.orange, 2)}
  ${arrow(1262, 578, 262, 630, C.green, 2)}${arrow(1262, 578, 690, 630, C.orange, 2)}${arrow(1262, 578, 1118, 630, C.red, 2)}${arrow(1262, 578, 1546, 630, C.purple, 2)}
  ${node(360, 810, 360, 100, "추가 질문 1개", ["사용자 보충 후 활성 조건에 누적", "이 turn은 엔진을 다시 호출하지 않음"], { mark: "person", color: C.orange })}
  ${node(810, 810, 360, 100, "SQL + EvidenceBundle", ["숫자·필터·집계와 최종 근거 행을 재검증"], { logo: "duckdb", color: C.ink })}
  ${node(1260, 810, 430, 100, "안전한 공개 응답", ["FULL 또는 SAFE_LIMITED · 검증 가능한 trace만 노출"], { mark: "lock", color: C.green })}
  ${arrow(262, 770, 990, 810, C.green, 2)}${arrow(1118, 770, 1475, 810, C.red, 2)}${arrow(690, 770, 540, 810, C.orange, 2)}${arrow(1546, 770, 1475, 810, C.purple, 2)}${arrow(1170, 860, 1260, 860, C.ink)}
  <path d="M360 860 L38 860 L38 485" stroke="${C.orange}" stroke-width="2" fill="none" stroke-linecap="round"/>
  ${arrow(38, 485, 72, 485, C.orange, 2)}
  ${text(70, 957, "원칙: 중요한 조건 하나라도 설명 없이 사라지면 FULL 답변을 금지 · 교차 스코프 질의는 거부하지 않고 계획을 끝까지 유지", 17, { fill: C.muted, weight: 620 })}`;
  return poster(
    "질문 하나가 답변이 되기까지",
    "조건 보존 → 채널별 후보 탐색 → SQL 재검증 → 근거가 연결된 안전한 답변",
    content,
    "비공개 추론·원문 프롬프트·비밀키는 공개 trace·응답·export에 포함하지 않음",
  );
}

function contractField(x, y, field, description, color, mark) {
  return `<rect x="${x}" y="${y}" width="156" height="102" rx="16" fill="${C.card}" fill-opacity="0.92" stroke="${color}" stroke-opacity="0.55" stroke-width="1.7"/>
    ${customMark(mark, x + 54, y + 20, 35, color)}
    ${text(x + 78, y + 70, field, field.length > 14 ? 11.5 : 13.5, { anchor: "middle", family: mono, weight: 750, fill: color })}
    ${text(x + 78, y + 89, description, 11, { anchor: "middle", fill: C.muted, weight: 500 })}`;
}

function repoMap() {
  const tree = [
    ["app/", "API · planner · execution · retrieval"],
    ["etl/", "공식 XLSX 정제 · provenance 생성"],
    ["data/", "재현 가능한 serving artifacts"],
    ["qa_chat/", "인증 · 암호화 · 대화 어댑터"],
    ["qa_web/", "React 테스터 채팅 · 검사 패널"],
    ["tests/", "계약 · 회귀 · 공격 시나리오"],
    ["eval/", "640 oracle · holdout · fixtures"],
    ["docs/", "공식 요구 · 결정 · 인수인계"],
    ["deploy/", "compose · healthcheck · TLS 예시"],
  ];
  let left = panel(72, 224, 790, 695, "저장소 구조", "변경 위치가 곧 제품 경계가 되도록 분리");
  left += `<path d="M118 330v530 M118 350h25 M118 410h25 M118 470h25 M118 530h25 M118 590h25 M118 650h25 M118 710h25 M118 770h25 M118 830h25" stroke="${C.line}" stroke-width="2" fill="none"/>`;
  tree.forEach(([name, note], index) => {
    const y = 355 + index * 60;
    left += `${text(164, y, name, 19, { family: mono, weight: 750, fill: index === 0 ? C.teal : C.ink })}${text(355, y, note, 16, { fill: C.muted, weight: 500 })}`;
  });
  const content = `${left}
  ${panel(938, 224, 914, 230, "바뀌지 않는 공개 계약", "대회 엔진의 GET /answer는 다섯 문자열 필드를 유지")}
  ${contractField(968, 305, "question_id", "문항 식별", C.blue, "ledger")}
  ${contractField(1140, 305, "question", "사용자 질문", C.teal, "person")}
  ${contractField(1312, 305, "retrieved_context", "근거 요약", C.orange, "search")}
  ${contractField(1484, 305, "think_trace", "실행 메타", C.purple, "ledger")}
  ${contractField(1656, 305, "answer", "최종 답변", C.green, "lock")}
  ${panel(938, 500, 914, 419, "코드가 지키는 제품 규칙", "기능 추가도 아래 안전 경계를 통과해야 함")}
  ${chip(980, 580, "공식 데이터 우선", C.green, { width: 162, height: 34, size: 14 })}
  ${chip(1160, 580, "HCX 외 LLM 없음", C.green, { width: 165, height: 34, size: 14 })}
  ${chip(1343, 580, "SQL = 최종 권위", C.blue, { width: 154, height: 34, size: 14 })}
  ${chip(1515, 580, "교차질의 유지", C.purple, { width: 154, height: 34, size: 14 })}
  ${chip(980, 640, "근거 없는 FULL 금지", C.red, { width: 182, height: 34, size: 14 })}
  ${chip(1180, 640, "비밀·프롬프트 비노출", C.orange, { width: 185, height: 34, size: 14 })}
  ${chip(1383, 640, "상태 표기 정직성", C.teal, { width: 164, height: 34, size: 14 })}
  ${chip(1565, 640, "read-only 검증", C.ink, { width: 154, height: 34, size: 14 })}
  ${multiline(980, 750, ["문서는 과거 수치와 현재 검증 결과를 분리하고, 새 세션은 00_START_HERE.md와", "docs/20_MAINLINE_HANDOFF_AND_REPOSITORY_MAP.md부터 읽어 코드·데이터·운영 경계를 복원한다."], 17, { fill: C.muted, weight: 520, lineHeight: 30 })}`;
  return poster(
    "코드와 문서가 연결되는 지도",
    "새로운 개발자·Codex 세션도 ‘무엇을 어디에서 왜 바꾸는지’를 먼저 파악할 수 있는 구조",
    content,
    "원본 공식 자료와 데이터는 보존 · generated artifact와 실행 코드·검증·배포 문서를 분리 · 시작점: 00_START_HERE.md",
  );
}

function assurance() {
  const content = `
  ${text(72, 220, "완성 여부는 한 단어가 아니라 증거 상태로 판정합니다.", 25, { weight: 720 })}
  ${panel(72, 278, 515, 562, "공식 기준", "우리가 임의로 바꿀 수 없는 입력")}
  ${node(112, 365, 435, 112, "공식 과제 PDF", ["제출 계약 · 금지 사항 · 평가 경계"], { mark: "ledger", color: C.ink })}
  ${node(112, 505, 435, 112, "공식 데이터 XLSX", ["네 상품 스코프 · 수치·근거의 우선 원천"], { mark: "database", color: C.green })}
  ${node(112, 645, 435, 112, "설명회 녹취", ["운영 해석과 Q&A를 교차 확인"], { mark: "person", color: C.blue })}
  ${chip(112, 790, "OFFICIAL SOURCE", C.ink, { width: 162, height: 32, size: 13 })}
  ${panel(702, 278, 515, 562, "VERIFIED_LOCAL", "현재 코드와 fixture에서 재현한 범위")}
  ${node(742, 365, 435, 112, "데이터 재생성", ["ETL · KG · BM25 index · source hash"], { logo: "pandas", color: C.green })}
  ${node(742, 505, 435, 112, "회귀·안전 검증", ["Python tests · 640 oracle · 공격 시나리오"], { logo: "pytest", color: C.orange })}
  ${node(742, 645, 435, 112, "컨테이너 실행", ["healthcheck · restart · schema smoke"], { logo: "docker", color: C.blue })}
  ${chip(742, 790, "LOCAL EVIDENCE", C.green, { width: 162, height: 32, size: 13 })}
  ${panel(1332, 278, 515, 562, "PENDING_EXTERNAL", "사람의 자격증명·인프라가 필요한 범위")}
  ${node(1372, 365, 435, 112, "실제 HCX gate", ["승인 model · endpoint · 20→100→전체"], { logo: "naver", color: C.green })}
  ${node(1372, 505, 435, 112, "Embedding live smoke", ["1,024차원 cache · 실제 provider 검증"], { mark: "vector", color: C.teal })}
  ${node(1372, 645, 435, 112, "NCP 공개 배포 · 승인", ["TLS · 외부 smoke · 사람 freeze"], { logo: "caddy", color: C.orange })}
  ${chip(1372, 790, "GATE CLOSED", C.red, { width: 142, height: 32, size: 13 })}
  ${arrow(587, 560, 702, 560, C.green, 3)}${arrow(1217, 560, 1332, 560, C.orange, 3)}
  ${text(960, 922, "로컬 검증 통과 ≠ 실제 AI 호출·공개 운영 완료", 26, { anchor: "middle", weight: 800, fill: C.red })}`;
  return poster(
    "검증 완료 범위와 Release Gate",
    "무엇이 확인됐고, 무엇이 아직 사람·외부 환경을 기다리는지 한 화면에 분리한다",
    content,
    "내부 20·100·640·1,200·5,000 gate는 공식 평가 문항 수가 아닌 release 관리 기준 · 실제 외부 gate 전에는 ‘완료’로 표시하지 않음",
  );
}

async function emit(name, createSvg) {
  const svgPath = path.join(visualDir, `${name}.svg`);
  const pngPath = path.join(visualDir, `${name}.png`);
  embedAssets = false;
  fs.writeFileSync(svgPath, createSvg().replace(/[ \t]+(?=\r?\n)/g, ""), "utf8");
  embedAssets = true;
  await sharp(Buffer.from(createSvg())).png({ compressionLevel: 9 }).toFile(pngPath);
  process.stdout.write(`${path.relative(root, svgPath)}\n${path.relative(root, pngPath)}\n`);
}

fs.mkdirSync(visualDir, { recursive: true });
await emit("mirae-tech-stack-1920x1080", techStack);
await emit("mirae-system-architecture-1920x1080", architecture);
await emit("mirae-question-flow-1920x1080", questionFlow);
await emit("mirae-repository-map-1920x1080", repoMap);
await emit("mirae-assurance-release-gates-1920x1080", assurance);
