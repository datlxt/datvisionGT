import { useState } from "react";

import type { Job } from "../types";

/** Drives the delete-confirmation modal for a job list: pending target, busy, and errors. */
export function useJobDeletion(onDelete: (job: Job) => Promise<void>) {
  const [pending, setPending] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  return {
    pending,
    busy,
    error,
    request(job: Job) {
      setError("");
      setPending(job);
    },
    cancel() {
      if (!busy) setPending(null);
    },
    async confirm() {
      if (!pending) return;
      setBusy(true);
      setError("");
      try {
        await onDelete(pending);
        setPending(null);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "Không xóa được job.");
      } finally {
        setBusy(false);
      }
    },
  };
}
