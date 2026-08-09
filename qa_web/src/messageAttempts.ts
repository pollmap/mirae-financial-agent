export interface MessageAttemptFingerprint {
  sessionId: string;
  text: string;
  replyToMessageId?: string;
  clarificationId?: string;
  clarificationOptionValue?: string;
}

function defaultMessageId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function fingerprintKey(value: MessageAttemptFingerprint): string {
  return JSON.stringify([
    value.sessionId,
    value.text,
    value.replyToMessageId || "",
    value.clarificationId || "",
    value.clarificationOptionValue || "",
  ]);
}

export class MessageAttemptLedger {
  private pending: { key: string; id: string } | null = null;

  constructor(private readonly generateId: () => string = defaultMessageId) {}

  acquire(fingerprint: MessageAttemptFingerprint): string {
    const key = fingerprintKey(fingerprint);
    if (this.pending?.key === key) return this.pending.id;
    const id = this.generateId();
    this.pending = { key, id };
    return id;
  }

  complete(id: string): void {
    if (this.pending?.id === id) this.pending = null;
  }

  reset(): void {
    this.pending = null;
  }
}
