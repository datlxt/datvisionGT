import { useCallback, useEffect, useState } from "react";

import { AppLayout } from "./components/AppLayout";
import { ErrorState, LoadingState } from "./components/ui";
import { api } from "./lib/api";
import { isReadyForReview } from "./lib/format";
import { CreateJobPage } from "./pages/CreateJobPage";
import { ExportsPage } from "./pages/ExportsPage";
import { OverviewPage } from "./pages/OverviewPage";
import { ProcessingPage } from "./pages/ProcessingPage";
import { ReviewPage } from "./pages/ReviewPage";
import type { Health, Job, View } from "./types";

export default function App() {
  const [view, setView] = useState<View>("create");
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

  function openJob(job: Job) {
    setSelectedJob(job);
    setView(isReadyForReview(job) ? "review" : "processing");
  }

  function navigate(next: Exclude<View, "processing">) {
    if (next === "review" && !selectedJob) return;
    setView(next);
  }

  function reloadJobs() {
    setLoadingJobs(true);
    setJobsError("");
    setReloadToken((value) => value + 1);
  }

  let content;
  if (view === "overview") {
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
          onCreate={() => setView("create")}
          onDelete={deleteJob}
          onOpen={openJob}
        />
      );
    }
  } else if (view === "create") {
    content = (
      <CreateJobPage
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
    content = <ExportsPage jobs={jobs} onDelete={deleteJob} onOpen={openJob} />;
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
