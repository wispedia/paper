import { useEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";

export function WordPreview({ jobId }: { jobId: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const pages = useRef<HTMLDivElement>(null);
  const styles = useRef<HTMLDivElement>(null);
  const fitWidth = useRef(true);
  const [fitted, setFitted] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);

  function fit() {
    if (viewport.current && pages.current?.scrollWidth) {
      pages.current.style.zoom = String(
        fitWidth.current
          ? Math.min(
              1,
              viewport.current.clientWidth / pages.current.scrollWidth,
            )
          : 1,
      );
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;
    let observer: ResizeObserver | undefined;
    const target = pages.current!;
    const styleTarget = styles.current!;
    setLoading(true);
    setError("");
    target.replaceChildren();
    styleTarget.replaceChildren();

    async function load() {
      try {
        const [response, { renderAsync }] = await Promise.all([
          fetch(`/api/jobs/${jobId}/download`, {
            credentials: "same-origin",
            signal: controller.signal,
          }),
          import("docx-preview"),
        ]);
        if (!response.ok)
          throw new Error("预览暂时不可用，请重试或下载 Word 查看。");
        const document = await response.blob();
        if (cancelled) return;
        await renderAsync(document, target, styleTarget, {
          className: "paper-docx",
          renderAltChunks: false,
          useBase64URL: true,
          ignoreHeight: true,
          experimental: true,
        });
        if (cancelled) return;
        fit();
        observer = new ResizeObserver(fit);
        observer.observe(viewport.current!);
        setLoading(false);
      } catch {
        if (!cancelled) {
          setLoading(false);
          setError("预览暂时不可用，请重试或下载 Word 查看。");
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
      controller.abort();
      observer?.disconnect();
    };
  }, [jobId, attempt]);

  return (
    <div className="word-preview mt-5 rounded-lg border bg-muted/30 p-3 sm:p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Word 预览</h3>
        {!loading && !error && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              fitWidth.current = !fitted;
              setFitted(!fitted);
              fit();
            }}
          >
            {fitted ? "原始大小" : "适应宽度"}
          </Button>
        )}
        <p className="text-xs text-muted-foreground">
          网页版预览，排版以下载的 Word 为准。
        </p>
      </div>
      {loading && (
        <div
          role="status"
          className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground"
        >
          <Loader2 size={18} className="animate-spin" />
          正在加载预览…
        </div>
      )}
      {error && (
        <div role="alert" className="py-6 text-center text-sm">
          <p>{error}</p>
          <Button
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={() => setAttempt(attempt + 1)}
          >
            重新加载
          </Button>
        </div>
      )}
      <div ref={styles} />
      <div
        ref={viewport}
        className="max-h-[75vh] overflow-auto"
        aria-label="Word 文档内容"
        aria-busy={loading}
        style={{ height: loading || error ? 0 : undefined }}
      >
        <div
          ref={pages}
          className="word-preview-pages"
          style={{ visibility: loading || error ? "hidden" : "visible" }}
        />
      </div>
    </div>
  );
}
