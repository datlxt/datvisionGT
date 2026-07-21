import { useEffect, useState } from "react";

type Health = {
  status: string;
  database: string;
  redis: string;
  storage: string;
};

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/v1/health")
      .then(async (response) => {
        const body = (await response.json()) as Health;
        if (!response.ok) throw new Error(`Health check returned ${response.status}`);
        return body;
      })
      .then(setHealth)
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Không thể kết nối backend");
      });
  }, []);

  return (
    <main className="shell">
      <section className="card">
        <p className="eyebrow">GROUND TRUTH PLATFORM</p>
        <h1>DatVision GT</h1>
        <p className="lead">Plate-first evidence pipeline</p>

        {health && (
          <div className="status success">
            Hệ thống sẵn sàng · PostgreSQL {health.database} · Redis {health.redis}
          </div>
        )}
        {!health && !error && <div className="status">Đang kiểm tra hệ thống…</div>}
        {error && <div className="status error">Backend chưa sẵn sàng: {error}</div>}

        <div className="grid">
          <article>
            <span>01</span>
            <h2>Evidence baseline</h2>
            <p>Video, frame, timestamp và crop có thể truy vết.</p>
          </article>
          <article>
            <span>02</span>
            <h2>Plate pipeline</h2>
            <p>Detector và OCR được cắm qua interface độc lập.</p>
          </article>
          <article>
            <span>03</span>
            <h2>Human review</h2>
            <p>Model chỉ tạo draft; người dùng xác nhận GT cuối.</p>
          </article>
        </div>
      </section>
    </main>
  );
}

