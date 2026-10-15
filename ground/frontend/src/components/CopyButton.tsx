import { useState } from "react";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };
  return <button type="button" className="copy-button" onClick={copy} aria-label={`${label}: ${value}`} title={label}>{copied ? "COPIED" : "COPY"}</button>;
}
