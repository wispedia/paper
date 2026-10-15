import { useEffect, useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

type Page = { number: number; width: number; height: number; url: string };

export function WordPreview({
  jobId,
  title,
  onClose,
  onRestoreFocus,
}: {
  jobId: string;
  title: string;
  onClose: () => void;
  onRestoreFocus: () => void;
}) {
  const [pages, setPages] = useState<Page[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [fitted, setFitted] = useState(true);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let cancelled = false;
    setLoading(true);
    setError("");
    setPages([]);
    async function load() {
      try {
        const response = await fetch(`/api/jobs/${jobId}/preview`, {
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error();
        const data = await response.json();
        if (cancelled) return;
        if (data.status === "pending") {
          timer = setTimeout(load, 2000);
          return;
        }
        if (data.status !== "ready" || !data.pages.length) throw new Error();
        setPages(data.pages);
        setLoading(false);
      } catch {
        if (!cancelled) {
          setLoading(false);
          setError("预览暂时不可用，仍可下载 Word 查看。");
        }
      }
    }
    void load();
    return () => {
      cancelled = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [jobId, attempt]);

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent
        className="flex h-[90dvh] max-w-[calc(100%-1.5rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-5xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault();
          onRestoreFocus();
        }}
      >
        <DialogHeader className="shrink-0 border-b px-4 py-4 pr-12 text-left sm:px-6">
          <DialogTitle>Word 预览 · {title}</DialogTitle>
          <DialogDescription>
            按 Word 文档渲染。不同办公软件的字体和分页可能略有差异。
          </DialogDescription>
        </DialogHeader>
        <div className="flex shrink-0 flex-wrap items-center justify-between gap-2 border-b px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <Button
              size="sm"
              variant="outline"
              disabled={!pages.length || !!error}
              onClick={() => setFitted(!fitted)}
            >
              {fitted ? "原始大小" : "适应宽度"}
            </Button>
            {!!pages.length && (
              <span className="text-xs text-muted-foreground">
                共 {pages.length} 页
              </span>
            )}
          </div>
          <Button size="sm" asChild>
            <a href={`/api/jobs/${jobId}/download`}>
              <Download size={15} />
              下载 Word
            </a>
          </Button>
        </div>
        <div
          className="min-h-0 flex-1 overflow-auto bg-muted/60 p-3 sm:p-5"
          aria-label="Word 文档页面"
          aria-busy={loading}
          tabIndex={0}
        >
          {loading && (
            <div
              role="status"
              className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground"
            >
              <Loader2 size={18} className="animate-spin" />
              正在准备预览，请稍候…
            </div>
          )}
          {error ? (
            <div role="alert" className="py-12 text-center text-sm">
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
          ) : (
            <div
              className="mx-auto space-y-4"
              style={{
                width: fitted
                  ? "100%"
                  : Math.max(0, ...pages.map((p) => p.width)),
                maxWidth: fitted ? 850 : undefined,
              }}
            >
              {pages.map((page) => (
                <figure key={page.number}>
                  <img
                    src={page.url}
                    alt={`第 ${page.number} 页`}
                    width={page.width}
                    height={page.height}
                    className="h-auto w-full bg-white shadow-sm"
                    loading={page.number === 1 ? "eager" : "lazy"}
                    onError={() =>
                      setError("页面加载失败，请重新加载或下载 Word 查看。")
                    }
                  />
                  <figcaption className="mt-2 text-center text-xs text-muted-foreground">
                    第 {page.number} 页 / 共 {pages.length} 页
                  </figcaption>
                </figure>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
