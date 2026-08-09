import { ArrowRight, Database, GitBranch, ListChecks, PanelRightClose, SearchX } from "lucide-react";
import {
  KeyboardEvent as ReactKeyboardEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { AssistantMessage, EvidenceItem, EvidenceSource } from "../types";
import { shortHash } from "../ui";

type InspectorTab = "conditions" | "evidence" | "retrieval";

interface InspectorPanelProps {
  assistant: AssistantMessage | null;
  open: boolean;
  modal: boolean;
  onClose: () => void;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.map((item) => displayValue(item)).join(", ") : "없음";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function conditionKindLabel(kind: string): string {
  const labels: Record<string, string> = {
    scope: "상품군·스코프",
    metric: "지표",
    period: "기간",
    product: "상품",
    filter: "필터",
    sort: "정렬",
  };
  return labels[kind] || kind;
}

interface EvidenceGroup {
  item: EvidenceItem;
  sources: EvidenceSource[];
}

function evidenceGroups(assistant: AssistantMessage | null): EvidenceGroup[] {
  return (assistant?.evidence?.items || []).map((rawItem) => {
    const item = { ...rawItem, scope: rawItem.scope || assistant?.evidence?.scope || undefined };
    const sources: EvidenceSource[] = [];
    const seen = new Set<string>();
    for (const source of [...(item.fields || []), ...(item.sources || [])]) {
      const key = [source.source_file, source.source_sheet, source.source_excel_row, source.source_field || source.field].join("|");
      if (!seen.has(key)) {
        seen.add(key);
        sources.push(source);
      }
    }
    return { item, sources };
  });
}

const FOCUSABLE_SELECTOR = [
  "button:not([disabled]):not([tabindex='-1'])",
  "[href]:not([tabindex='-1'])",
  "input:not([disabled]):not([tabindex='-1'])",
  "textarea:not([disabled]):not([tabindex='-1'])",
  "select:not([disabled]):not([tabindex='-1'])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

export function InspectorPanel({ assistant, open, modal, onClose }: InspectorPanelProps) {
  const [tab, setTab] = useState<InspectorTab>("conditions");
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const sourceGroups = useMemo(() => evidenceGroups(assistant), [assistant]);
  const sourceCount = sourceGroups.reduce((count, group) => count + group.sources.length, 0);
  const modalOpen = open && modal;

  useEffect(() => setTab("conditions"), [assistant?.id]);
  useEffect(() => { onCloseRef.current = onClose; }, [onClose]);
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    if (modal && !open) panel.setAttribute("inert", "");
    else panel.removeAttribute("inert");
  }, [modal, open]);
  useEffect(() => {
    if (!modalOpen) return;
    restoreFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const focusTimer = window.setTimeout(() => closeRef.current?.focus(), 0);
    const escapeListener = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onCloseRef.current();
    };
    window.addEventListener("keydown", escapeListener);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", escapeListener);
      const target = restoreFocusRef.current;
      window.setTimeout(() => {
        if (target?.isConnected) target.focus();
      }, 0);
    };
  }, [modalOpen]);

  const evidence = assistant?.evidence;
  const handleTabKey = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const tabs = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
    const current = tabs.indexOf(document.activeElement as HTMLButtonElement);
    let next = current;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    else if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    else next = (current + 1) % tabs.length;
    event.preventDefault();
    tabs[next]?.focus();
    tabs[next]?.click();
  };
  const trapModalFocus = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (!modalOpen || event.key !== "Tab") return;
    const focusable = Array.from(
      panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) || [],
    ).filter((element) => !element.hasAttribute("hidden"));
    if (focusable.length === 0) {
      event.preventDefault();
      closeRef.current?.focus();
      return;
    }
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <>
      {modalOpen && <div className="sheet-scrim" aria-hidden="true" onClick={onClose} />}
      <aside
        ref={panelRef}
        className={`inspector-panel ${open ? "is-open" : ""}`}
        aria-label={modalOpen ? undefined : "답변 검사 패널"}
        aria-labelledby={modalOpen ? "inspector-title" : undefined}
        aria-hidden={modal && !open ? true : undefined}
        role={modalOpen ? "dialog" : undefined}
        aria-modal={modalOpen || undefined}
        onKeyDown={trapModalFocus}
      >
        <div className="inspector-heading">
          <div>
            <span className="section-kicker">검증 가능한 메타데이터</span>
            <h2 id="inspector-title">답변 검사</h2>
          </div>
          <button ref={closeRef} className="icon-button inspector-close" type="button" onClick={onClose} aria-label="검사 패널 닫기">
            <PanelRightClose aria-hidden="true" />
          </button>
        </div>

        {assistant ? (
          <>
            <div className="inspector-summary">
              <div><span>상태</span><strong>{assistant.status}</strong></div>
              <div><span>결과</span><strong>{evidence?.result_count ?? "-"}건</strong></div>
              <div><span>기준일</span><strong>{evidence?.snapshot_date || "-"}</strong></div>
            </div>

            <div className="tab-list" role="tablist" aria-label="검사 항목" onKeyDown={handleTabKey}>
              <button id="tab-conditions" aria-controls="panel-inspector" tabIndex={tab === "conditions" ? 0 : -1} type="button" role="tab" aria-selected={tab === "conditions"} onClick={() => setTab("conditions")}>
                <ListChecks aria-hidden="true" /> 조건
              </button>
              <button id="tab-evidence" aria-controls="panel-inspector" tabIndex={tab === "evidence" ? 0 : -1} type="button" role="tab" aria-selected={tab === "evidence"} onClick={() => setTab("evidence")}>
                <Database aria-hidden="true" /> 근거
              </button>
              <button id="tab-retrieval" aria-controls="panel-inspector" tabIndex={tab === "retrieval" ? 0 : -1} type="button" role="tab" aria-selected={tab === "retrieval"} onClick={() => setTab("retrieval")}>
                <GitBranch aria-hidden="true" /> 검색 경로
              </button>
            </div>

            <div className="tab-panel" id="panel-inspector" role="tabpanel" aria-labelledby={`tab-${tab}`}>
              {tab === "conditions" && (
                <div className="ledger-list">
                  {(evidence?.condition_changes || []).length > 0 && (
                    <section className="condition-changes" aria-label="조건 변경 내역">
                      <h3>이번 턴에서 변경된 조건</h3>
                      {evidence!.condition_changes!.map((change, index) => (
                        <article key={`${change.kind}-${index}`}>
                          <div className="condition-change-heading">
                            <strong>{conditionKindLabel(change.kind)}</strong>
                            <span>{change.reason === "explicit_user_correction" ? "사용자 정정" : "조건 변경"}</span>
                          </div>
                          <div className="condition-change-values">
                            <span className="previous-value">{displayValue(change.previous)}</span>
                            <ArrowRight aria-hidden="true" />
                            <strong className="current-value">{change.current}</strong>
                          </div>
                        </article>
                      ))}
                    </section>
                  )}
                  {(evidence?.condition_ledger || []).length === 0 ? (
                    <EmptyInspector text="표시할 조건 원장이 없습니다." />
                  ) : evidence!.condition_ledger!.map((entry, index) => (
                    <article key={entry.condition_id || `${entry.kind}-${index}`}>
                      <div className="ledger-title">
                        <strong>{entry.requested_text || entry.condition || entry.field || `조건 ${index + 1}`}</strong>
                        <span className={`ledger-state state-${(entry.status || "unknown").toLowerCase()}`}>{entry.status || "확인 대기"}</span>
                      </div>
                      <dl>
                        <dt>유형</dt><dd>{entry.kind || "-"}</dd>
                        {entry.operator && <><dt>연산자</dt><dd>{entry.operator}</dd></>}
                        <dt>Grounding 필드</dt><dd>{displayValue(entry.grounded_fields || entry.grounded_value)}</dd>
                      </dl>
                      {(entry.note || entry.explanation) && <p>{entry.note || entry.explanation}</p>}
                    </article>
                  ))}
                </div>
              )}

              {tab === "evidence" && (
                <div className="evidence-list">
                  {(evidence?.aggregates || []).map((aggregate, index) => (
                    <article className="aggregate-evidence" key={aggregate.aggregate_id || `${aggregate.group_key}-${index}`}>
                      <strong>집계 {aggregate.group_key || `#${index + 1}`}</strong>
                      <p className="evidence-value">{displayValue(aggregate.value)} {aggregate.unit || ""}</p>
                      <dl>
                        <dt>원본 테이블</dt><dd>{displayValue(aggregate.source_table_ids)}</dd>
                        <dt>원본 필드</dt><dd>{displayValue(aggregate.source_fields)}</dd>
                        <dt>근거 행 수</dt><dd>{aggregate.source_row_count ?? "-"}</dd>
                        <dt>기준일</dt><dd>{aggregate.as_of_date || "데이터셋 스냅샷"}</dd>
                        {aggregate.query_hash && <><dt>질의 해시</dt><dd title={aggregate.query_hash}>{shortHash(aggregate.query_hash)}</dd></>}
                      </dl>
                    </article>
                  ))}
                  {sourceCount === 0 && (evidence?.aggregates || []).length === 0 ? (
                    <EmptyInspector text="표시할 필드 근거가 없습니다." />
                  ) : sourceGroups.map(({ item, sources }, groupIndex) => (
                    <section
                      className="evidence-product-group"
                      key={item.product_uid || `${item.name || item.product_name}-${groupIndex}`}
                      aria-label={`${item.name || item.product_name || "상품"} 근거`}
                    >
                      <header className="evidence-product-heading">
                        <div>
                          <strong>{item.rank ? `${item.rank}위 · ` : ""}{item.name || item.product_name || "상품명 확인 대기"}</strong>
                          <span>{item.scope || "스코프 확인 대기"}</span>
                        </div>
                        <code title={item.product_uid}>{item.product_uid || "UID 확인 대기"}</code>
                      </header>
                      {sources.length === 0 ? (
                        <EmptyInspector text="이 상품에 연결된 필드 근거가 없습니다." />
                      ) : sources.map((source, index) => (
                        <article key={`${item.product_uid}-${source.source_file}-${source.source_excel_row}-${index}`}>
                          <strong>{source.source_field || source.metric_id || source.field || "공식 원본 필드"}</strong>
                          <p className="evidence-value">{displayValue(source.normalized_value ?? source.raw_value ?? source.value)} {source.unit || ""}</p>
                          <dl>
                            <dt>파일</dt><dd>{source.source_file || "-"}</dd>
                            <dt>시트</dt><dd>{source.source_sheet || "-"}</dd>
                            <dt>행</dt><dd>{source.source_excel_row ?? "-"}</dd>
                            <dt>기준일</dt><dd>{source.as_of_date || "데이터셋 스냅샷"}</dd>
                            <dt>품질</dt><dd>{displayValue(source.quality_flags || source.quality_status)}</dd>
                            {(source.source_row_hash || source.row_hash) && <><dt>행 해시</dt><dd title={source.source_row_hash || source.row_hash}>{shortHash(source.source_row_hash || source.row_hash)}</dd></>}
                          </dl>
                        </article>
                      ))}
                    </section>
                  ))}
                </div>
              )}

              {tab === "retrieval" && (
                <div className="retrieval-list">
                  {(evidence?.retrieval_channels || []).length === 0 ? (
                    <EmptyInspector text="표시할 검색 경로가 없습니다." />
                  ) : evidence!.retrieval_channels!.map((channel, index) => (
                    <article key={`${channel.channel}-${index}`}>
                      <div className="route-name">
                        <strong>{channel.channel || `채널 ${index + 1}`}</strong>
                        <span>{channel.status || "사용"}</span>
                      </div>
                      {channel.reason && <p>{channel.reason}</p>}
                      <dl>
                        <dt>스코프</dt><dd>{channel.scope || "-"}</dd>
                        <dt>후보</dt><dd>{channel.candidate_count ?? "-"}</dd>
                        <dt>SQL 재검증</dt><dd>{channel.verified_count ?? "-"}</dd>
                        <dt>지연시간</dt><dd>{channel.latency_ms !== undefined ? `${channel.latency_ms}ms` : "-"}</dd>
                        <dt>근거 참조</dt><dd>{displayValue(channel.evidence_refs)}</dd>
                        {(channel.fallback_reason || channel.fallback) && <><dt>폴백</dt><dd>{channel.fallback_reason || channel.fallback}</dd></>}
                      </dl>
                    </article>
                  ))}
                  {(evidence?.limitations || []).map((limitation, index) => (
                    <p className="limitation" key={`${limitation}-${index}`}>{limitation}</p>
                  ))}
                </div>
              )}
            </div>

            <dl className="response-manifest">
              <dt>엔진 SHA</dt><dd>{shortHash(assistant.environment?.engine_git_sha)}</dd>
              <dt>데이터 해시</dt><dd>{shortHash(assistant.environment?.data_hash)}</dd>
              <dt>HCX 모델</dt><dd>{assistant.environment?.model_id || "-"}</dd>
            </dl>
          </>
        ) : (
          <EmptyInspector text="대화의 ‘근거와 실행 경로 검사’를 선택하면 조건과 출처를 확인할 수 있습니다." />
        )}
      </aside>
    </>
  );
}

function EmptyInspector({ text }: { text: string }) {
  return <div className="inspector-empty"><SearchX aria-hidden="true" /><p>{text}</p></div>;
}
