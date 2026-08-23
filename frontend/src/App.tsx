import { useCallback, useEffect, useState } from "react";

import { AppLayout } from "./components/AppLayout";
import { ErrorState, LoadingState } from "./components/ui";
import { api } from "./lib/api";
import { isReadyForReview } from "./lib/format";
import { CondensePage } from "./pages/CondensePage";
import { CreateJobPage } from "./pages/CreateJobPage";
import { ExportsPage } from "./pages/ExportsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ProcessingPage } from "./pages/ProcessingPage";
import { ReviewPage } from "./pages/ReviewPage";
import type { CondenseStatus, Health, Job, View } from "./types";

const KNOWN_VIEWS = ["overview", "create", "processing", "review", "exports", "condense"];

export default function App() {
  // Persist the current screen + selected job so a reload / navigation lands back where you were
  // (an in-progress cut or processing screen is restored, not reset to "Tạo job").
  const [view, setView] = useState<View>(() => {
    const stored = localStorage.getItem("dvgt.view");
    return stored && KNOWN_VIEWS.includes(stored) ? (stored as View) : "create";
  });
  const [createVehicleType, setCreateVehicleType] = useState<"motorcycle" | "car">("motorcycle");
  // A condensed video the user chose to turn into GT — pre-selected in the Create Job import flow
  // (so they draw the ROI / set the lane there) instead of jumping straight to processing.
  const [condensedForCreate, setCondensedForCreate] = useState<CondenseStatus | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [loadingJobs, setLoadingJobs] = useState(true);
  const [jobsError, setJobsError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    api<Job[]>("/api/v1/jobs")
      .then((payload) => {
        setJobs(payload);
        setSelectedJob((current) => {
          if (current) return payload.find((job) => job.id === current.id) ?? payload[0] ?? null;
          // Restore the job the user was on before the reload, if it still exists.
          const storedId = localStorage.getItem("dvgt.jobId");
          if (storedId) return payload.find((job) => job.id === storedId) ?? payload[0] ?? null;
          return payload[0] ?? null;
        });
      })
      .catch((reason: unknown) =>
        setJobsError(reason instanceof Error ? reason.message : "Không thể tải danh sách job."),
      )
      .finally(() => setLoadingJobs(false));
  }, [reloadToken]);

  useEffect(() => {
    api<Health>("/api/v1/health").then(setHealth).catch(() => setHealth(null));
  }, []);

  // Remember where the user is so a reload restores it.
  useEffect(() => {
    localStorage.setItem("dvgt.view", view);
  }, [view]);
  useEffect(() => {
    if (selectedJob) localStorage.setItem("dvgt.jobId", selectedJob.id);
  }, [selectedJob]);
  // If a restored "processing"/"review" view has no job (e.g. it was deleted), fall back to overview
  // once the job list has loaded — never leave the user on a dead screen.
  useEffect(() => {
    if (!loadingJobs && (view === "processing" || view === "review") && !selectedJob) {
      setView("overview");
    }
  }, [loadingJobs, view, selectedJob]);

  const upsertJob = useCallback((updated: Job) => {
    setSelectedJob(updated);
    setJobs((current) => [
      updated,
      ...current.filter((candidate) => candidate.id !== updated.id),
    ]);
  }, []);

  const deleteJob = useCallback(async (job: Job) => {
    const response = await fetch(`/api/v1/jobs/${job.id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) {
      const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
      throw new Error(payload?.detail ?? `HTTP ${response.status}`);
    }
    setJobs((current) => current.filter((candidate) => candidate.id !== job.id));
    setSelectedJob((current) => (current?.id === job.id ? null : current));
  }, []);

  const flagJob = useCallback(async (job: Job, flagged: boolean) => {
    // Toggle the "important" bookmark. Update in place (don't reorder the list) so the row the
    // user just starred doesn't jump under their cursor.
    const updated = await api<Job>(`/api/v1/jobs/${job.id}/flag`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flagged }),
    });
    setJobs((current) =>
      current.map((candidate) => (candidate.id === updated.id ? updated : candidate)),
    );
    setSelectedJob((current) => (current?.id === updated.id ? updated : current));
  }, []);

  function openJob(job: Job) {
    setSelectedJob(job);
    setView(isReadyForReview(job) ? "review" : "processing");
  }

  function navigate(next: Exclude<View, "processing">) {
    if (next === "review" && !selectedJob) return;
    // Silently refresh job statuses (review may have flipped a job to COMPLETED).
    if (next === "overview" || next === "exports") setReloadToken((value) => value + 1);
    // A sidebar visit to "Tạo job" is a fresh start, not the cut-video hand-off.
    if (next === "create") setCondensedForCreate(null);
    setView(next);
  }

  function reloadJobs() {
    setLoadingJobs(true);
    setJobsError("");
    setReloadToken((value) => value + 1);
  }

  let content;
  if ((view === "processing" || view === "review") && loadingJobs) {
    // Restoring a job-bound screen after reload — wait for the job list before deciding.
    content = (
      <section className="page">
        <LoadingState label="Đang tải job…" />
      </section>
    );
  } else if (view === "overview") {
    if (loadingJobs) {
      content = (
        <section className="page">
          <LoadingState label="Đang tải danh sách job…" />
        </section>
      );
    } else if (jobsError) {
      content = (
        <section className="page">
          <ErrorState message={jobsError} onRetry={reloadJobs} />
        </section>
      );
    } else {
      content = (
        <OverviewPage
          jobs={jobs}
          onCreate={(scope) => {
            // Video scope creates a condensed video (Cắt video); the vehicle scopes share the
            // normal job-creation flow, pre-selecting the matching vehicle type.
            if (scope === "video") {
              setView("condense");
              return;
            }
            setCondensedForCreate(null);
            setCreateVehicleType(scope);
            setView("create");
          }}
          onDelete={deleteJob}
          onOpen={openJob}
          onOpenCondense={() => setView("condense")}
        />
      );
    }
  } else if (view === "create") {
    content = (
      <CreateJobPage
        initialCondensed={condensedForCreate}
        initialVehicleType={createVehicleType}
        key={condensedForCreate?.id ?? "new-upload"}
        onDraftSaved={upsertJob}
        onStarted={(job) => {
          upsertJob(job);
          setView("processing");
        }}
      />
    );
  } else if (view === "processing" && selectedJob) {
    content = (
      <ProcessingPage
        job={selectedJob}
        onReview={() => setView("review")}
        onUpdate={upsertJob}
      />
    );
  } else if (view === "review" && selectedJob) {
    content = <ReviewPage job={selectedJob} key={selectedJob.id} />;
  } else if (view === "exports") {
    content = (
      <ExportsPage jobs={jobs} onDelete={deleteJob} onFlag={flagJob} onOpen={openJob} />
    );
  } else if (view === "condense") {
    content = (
      <CondensePage
        onOpenInCreate={(item) => {
          // Hand the cut video to the Create Job flow (pre-selected) so the reviewer draws the ROI
          // and sets the lane there, instead of it going straight to processing unconfigured.
          setCondensedForCreate(item);
          setView("create");
        }}
      />
    );
  } else {
    content = (
      <section className="page">
        <ErrorState message="Chưa chọn job để mở màn hình này." />
      </section>
    );
  }

  return (
    <AppLayout
      health={health}
      job={selectedJob}
      onNavigate={navigate}
      view={view}
    >
      {content}
    </AppLayout>
  );
}
