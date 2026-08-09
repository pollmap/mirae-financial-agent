import { Check, MessageSquareWarning } from "lucide-react";
import { useEffect, useId, useState } from "react";

import type { FeedbackRecord, FeedbackTag, FeedbackVerdict } from "../types";

const VERDICTS: Array<{ value: FeedbackVerdict; label: string }> = [
  { value: "accurate", label: "정확함" },
  { value: "partly_accurate", label: "일부만 정확함" },
  { value: "incorrect", label: "잘못됨" },
  { value: "uncertain", label: "판단하기 어려움" },
];

const TAGS: Array<{ value: FeedbackTag; label: string }> = [
  { value: "WRONG_PRODUCT", label: "상품 오류" },
  { value: "WRONG_VALUE", label: "수치 오류" },
  { value: "MISSING_CONDITION", label: "조건 누락" },
  { value: "BAD_CLARIFICATION", label: "추가 질문 오류" },
  { value: "WRONG_COMPARISON", label: "비교 오류" },
  { value: "EVIDENCE_MISMATCH", label: "근거 불일치" },
  { value: "UNSAFE_LANGUAGE", label: "부적절한 표현" },
  { value: "SLOW", label: "응답 지연" },
  { value: "OTHER", label: "기타" },
];

interface FeedbackControlProps {
  initial?: FeedbackRecord | null;
  disabled?: boolean;
  onSave: (feedback: FeedbackRecord) => Promise<void>;
}

export function FeedbackControl({ initial, disabled, onSave }: FeedbackControlProps) {
  const [verdict, setVerdict] = useState<FeedbackVerdict | "">(initial?.verdict || "");
  const [tags, setTags] = useState<FeedbackTag[]>(initial?.tags || []);
  const [note, setNote] = useState(initial?.note || "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(Boolean(initial));
  const groupId = useId();
  const problemTagRequired = verdict === "partly_accurate" || verdict === "incorrect";

  useEffect(() => {
    setVerdict(initial?.verdict || "");
    setTags(initial?.tags || []);
    setNote(initial?.note || "");
    setSaved(Boolean(initial));
  }, [initial]);

  const toggleTag = (tag: FeedbackTag) => {
    setSaved(false);
    setTags((current) => current.includes(tag) ? current.filter((item) => item !== tag) : [...current, tag]);
  };

  const submit = async () => {
    if (!verdict || (problemTagRequired && tags.length === 0)) return;
    setSaving(true);
    try {
      await onSave({ verdict, tags: verdict === "accurate" ? [] : tags, note: note.trim() || undefined });
      setSaved(true);
    } catch {
      setSaved(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <details className="feedback-control">
      <summary><MessageSquareWarning aria-hidden="true" /> 이 답변 평가</summary>
      <div className="feedback-body">
        <fieldset disabled={disabled || saving}>
          <legend>정확도 판단</legend>
          <div className="segmented-options">
            {VERDICTS.map((item) => (
              <label key={item.value} className={verdict === item.value ? "is-checked" : ""}>
                <input
                  type="radio"
                  name={`verdict-${groupId}`}
                  value={item.value}
                  checked={verdict === item.value}
                  onChange={() => { setVerdict(item.value); setSaved(false); }}
                />
                {item.label}
              </label>
            ))}
          </div>
        </fieldset>

        {verdict && verdict !== "accurate" && (
          <fieldset disabled={disabled || saving}>
            <legend>문제 유형</legend>
            <div className="tag-options">
              {TAGS.map((tag) => (
                <label key={tag.value} className={tags.includes(tag.value) ? "is-checked" : ""}>
                  <input
                    type="checkbox"
                    checked={tags.includes(tag.value)}
                    onChange={() => toggleTag(tag.value)}
                  />
                  {tag.label}
                </label>
              ))}
            </div>
            {problemTagRequired && tags.length === 0 && (
              <small className="feedback-requirement">일부만 정확함 또는 잘못됨은 문제 유형을 하나 이상 선택해 주세요.</small>
            )}
          </fieldset>
        )}

        {verdict && (
          <label className="feedback-note">
            검토 메모 <span>선택</span>
            <textarea
              value={note}
              maxLength={500}
              disabled={disabled || saving}
              onChange={(event) => { setNote(event.target.value); setSaved(false); }}
              placeholder="재현 조건이나 기대 결과를 적어 주세요. 개인정보는 입력하지 마세요."
            />
            <small className="field-counter">{note.length} / 500</small>
          </label>
        )}

        <button
          className="secondary-button"
          type="button"
          disabled={!verdict || (problemTagRequired && tags.length === 0) || disabled || saving || saved}
          onClick={submit}
        >
          {saved ? <><Check aria-hidden="true" /> 저장됨</> : saving ? "저장 중" : "평가 저장"}
        </button>
      </div>
    </details>
  );
}
