"use client";

import { useState } from "react";
import { Button, EmptyState, ErrorBanner } from "./ui";
import { apiPost, toApiError, type ApiError } from "@/lib/api";
import { CRMSyncSchema } from "@/lib/schemas";

export default function ImportLeads({ onImported }: { onImported: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function importLeads() {
    setBusy(true);
    setError(null);
    try {
      await apiPost("/crm/sync", CRMSyncSchema, {});
      onImported();
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      {error ? <ErrorBanner error={error} /> : null}
      <EmptyState
        title="No leads yet"
        action={<Button variant="primary" loading={busy} onClick={importLeads}>Import synthetic leads</Button>}
      >
        <p>
          Imports <code className="rounded bg-white/10 px-1.5 py-0.5">data/customers.json</code> into PostgreSQL. If that
          file is missing, run <code className="rounded bg-white/10 px-1.5 py-0.5">python generate_data.py</code> in the backend folder first.
        </p>
      </EmptyState>
    </div>
  );
}