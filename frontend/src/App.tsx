import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  ArrowRight,
  Check,
  Clipboard,
  Download,
  Eye,
  FileImage,
  FileText,
  Loader2,
  LogOut,
  ShieldCheck,
  UploadCloud,
  Users,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { WordPreview } from "@/components/WordPreview";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type User = {
  id: number;
  username: string;
  role: "admin" | "user";
  active?: number;
  created_at?: number;
};
type Job = {
  id: string;
  original_name: string;
  title: string | null;
  status: "queued" | "processing" | "completed" | "failed";
  stage: string;
  created_at: number;
  warnings: string[];
  error: string | null;
  attempts: number;
};
type Credentials = { username: string; password: string };
let csrf = "";
class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
async function api(path: string, method = "GET", body?: unknown) {
  const headers: Record<string, string> = {};
  if (method !== "GET") headers["X-CSRF-Token"] = csrf;
  const form = body instanceof FormData;
  if (body && !form) headers["Content-Type"] = "application/json";
  const response = await fetch("/api" + path, {
    method,
    headers,
    credentials: "same-origin",
    body: body ? ((form ? body : JSON.stringify(body)) as BodyInit) : undefined,
  });
  const data = await response.json();
  if (!response.ok)
    throw new ApiError(data.error || "请求失败，请稍后重试。", response.status);
  return data;
}
const date = (timestamp: number) =>
  new Date(timestamp * 1000).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
const message = (error: unknown) =>
  error instanceof Error ? error.message : "操作失败，请重试。";

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="rounded-lg bg-primary p-2 text-primary-foreground">
        <FileText size={20} />
      </div>
      <div>
        <div className="text-lg font-semibold tracking-tight">Paper</div>
        <div className="text-[11px] text-muted-foreground">试卷电子化</div>
      </div>
    </div>
  );
}
function Notice({ children }: { children: React.ReactNode }) {
  return (
    <Alert className="border-amber-200 bg-amber-50 text-amber-900">
      <AlertDescription className="text-amber-900">{children}</AlertDescription>
    </Alert>
  );
}

export default function App() {
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(true);
  const [page, setPage] = useState<"papers" | "users">("papers");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [nextRequestAt, setNextRequestAt] = useState(0);
  const [now, setNow] = useState(Date.now() / 1000);
  const [error, setError] = useState("");
  const fail = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) {
      setUser(null);
      csrf = "";
    } else setError(message(err));
  }, []);
  useEffect(() => {
    api("/me")
      .then((data) => {
        csrf = data.csrf;
        setUser(data.user);
      })
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  const loadJobs = useCallback(async () => {
    try {
      const data = await api("/jobs");
      setJobs(data.jobs);
      setNextRequestAt(data.next_request_at);
    } catch (err) {
      fail(err);
    }
  }, [fail]);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!user) return;
    void loadJobs();
    const timer = setInterval(() => void loadJobs(), 4000);
    return () => clearInterval(timer);
  }, [user, loadJobs]);
  if (checking)
    return (
      <div className="grid min-h-screen place-items-center" aria-label="加载中">
        <Loader2 className="animate-spin text-muted-foreground" />
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={(data) => {
          csrf = data.csrf;
          setUser(data.user);
          setError("");
        }}
      />
    );
  return (
    <div className="min-h-screen">
      <header className="border-b bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-5 py-4 sm:px-8">
          <Brand />
          <div className="flex items-center gap-3">
            <span className="hidden text-sm text-muted-foreground sm:block">
              {user.username}
            </span>
            <Button
              variant="ghost"
              size="sm"
              onClick={async () => {
                try {
                  await api("/logout", "POST");
                  csrf = "";
                  setUser(null);
                  setJobs([]);
                } catch (err) {
                  fail(err);
                }
              }}
            >
              <LogOut size={16} />
              退出
            </Button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-5 py-8 sm:px-8 sm:py-10">
        <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">
              {page === "papers" ? "把试卷照片变成 Word" : "账号管理"}
            </h1>
            <p className="mt-2 text-sm text-muted-foreground">
              {page === "papers"
                ? "上传一张清晰的试卷，获取可编辑的题目和数学公式。"
                : "创建账号后，将账号和生成的密码交给使用者。"}
            </p>
          </div>
          {user.role === "admin" && (
            <div className="flex gap-2">
              <Button
                variant={page === "papers" ? "secondary" : "ghost"}
                onClick={() => setPage("papers")}
              >
                <FileText size={16} />
                试卷
              </Button>
              <Button
                variant={page === "users" ? "secondary" : "ghost"}
                onClick={() => setPage("users")}
              >
                <Users size={16} />
                账号
              </Button>
            </div>
          )}
        </div>
        {error && (
          <div className="mb-5">
            <Notice>
              {error}
              <button className="ml-3 underline" onClick={() => setError("")}>
                关闭
              </button>
            </Notice>
          </div>
        )}
        {page === "users" ? (
          <Accounts onError={fail} />
        ) : (
          <>
            <div className="grid items-start gap-6 lg:grid-cols-[1fr_300px]">
              <Uploader
                onUploaded={loadJobs}
                onError={fail}
                blocked={
                  jobs.some(
                    (j) => j.status === "queued" || j.status === "processing",
                  )
                    ? "等待当前任务完成"
                    : nextRequestAt > now
                      ? `${Math.ceil(nextRequestAt - now)} 秒后可再次提交`
                      : ""
                }
              />
              <Card className="shadow-none">
                <CardHeader>
                  <CardTitle className="text-base">上传前检查</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4 text-sm leading-relaxed text-muted-foreground">
                  <p>整页拍摄，题目和选项都在画面中。</p>
                  <p>尽量正对纸面，避免反光和遮挡。</p>
                  <p>系统会忽略手写答案和批注；模糊内容会标为待核对。</p>
                  <div className="border-t pt-4">
                    <div className="mb-1 flex items-center gap-2 font-medium text-foreground">
                      <FileText size={16} />
                      可编辑的 Word
                    </div>
                    <p>文字与数学公式可以继续修改。复杂配图保留原图局部。</p>
                  </div>
                </CardContent>
              </Card>
            </div>
            <div className="mt-9">
              <div className="mb-4 flex items-center justify-between">
                <h2 className="text-base font-semibold">我的试卷</h2>
                <span className="text-xs text-muted-foreground">
                  处理状态自动更新
                </span>
              </div>
              <Jobs
                jobs={jobs}
                onRetry={async (id) => {
                  try {
                    await api(`/jobs/${id}/retry`, "POST");
                    await loadJobs();
                  } catch (err) {
                    fail(err);
                  }
                }}
              />
            </div>
          </>
        )}
        <footer className="mt-10 flex flex-wrap items-center justify-between gap-2 border-t pt-5 text-xs text-muted-foreground">
          <span>Paper · 试卷电子化</span>
          <span>识别后请核对题目，尤其是数学符号。</span>
        </footer>
      </main>
    </div>
  );
}

function Login({
  onLogin,
}: {
  onLogin: (data: { user: User; csrf: string }) => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      onLogin(
        await api("/login", "POST", {
          username: form.get("username"),
          password: form.get("password"),
        }),
      );
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="flex min-h-screen flex-col items-center justify-center px-5 py-12">
      <div className="mb-8">
        <Brand />
      </div>
      <Card className="w-full max-w-sm shadow-sm">
        <CardHeader className="pb-5">
          <CardTitle className="text-xl">登录你的账号</CardTitle>
          <CardDescription>使用管理员提供的账号和密码。</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="username">账号</Label>
              <Input
                id="username"
                name="username"
                autoComplete="username"
                required
                autoFocus
                placeholder="输入账号"
                maxLength={32}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">密码</Label>
              <Input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                placeholder="输入密码"
                maxLength={256}
              />
            </div>
            {error && (
              <p role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <Button className="w-full" disabled={busy}>
              {busy ? (
                <Loader2 className="animate-spin" />
              ) : (
                <ArrowRight size={16} />
              )}
              登录
            </Button>
          </form>
        </CardContent>
      </Card>
      <p className="mt-6 text-center text-xs text-muted-foreground">
        账号由管理员创建，暂不开放注册。
      </p>
    </main>
  );
}

function Uploader({
  onUploaded,
  onError,
  blocked,
}: {
  onUploaded: () => Promise<void>;
  onError: (err: unknown) => void;
  blocked: string;
}) {
  const [file, setFile] = useState<File | null>(null),
    [preview, setPreview] = useState(""),
    [busy, setBusy] = useState(false),
    [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!file) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  function choose(f?: File) {
    if (!f) return;
    if (!["image/jpeg", "image/png", "image/webp"].includes(f.type)) {
      onError(new Error("请选择 JPG、PNG 或 WebP 图片。"));
      return;
    }
    if (f.size > 12 * 1024 * 1024) {
      onError(new Error("图片不能超过 12 MB。"));
      return;
    }
    setFile(f);
  }
  async function upload() {
    if (!file) return;
    setBusy(true);
    const form = new FormData();
    form.append("image", file);
    try {
      await api("/jobs", "POST", form);
      setFile(null);
      if (input.current) input.current.value = "";
      await onUploaded();
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card className="shadow-none">
      <CardHeader>
        <CardTitle className="text-base">上传试卷图片</CardTitle>
        <CardDescription>
          一次一页，支持 JPG、PNG、WebP，最大 12 MB。
        </CardDescription>
      </CardHeader>
      <CardContent>
        <input
          ref={input}
          id="paper-image"
          type="file"
          accept="image/jpeg,image/png,image/webp"
          className="sr-only"
          disabled={busy}
          onChange={(e) => choose(e.target.files?.[0])}
        />
        <label
          htmlFor="paper-image"
          role="button"
          tabIndex={busy ? -1 : 0}
          aria-label="选择试卷图片"
          onKeyDown={(e) => {
            if (!busy && (e.key === "Enter" || e.key === " ")) {
              e.preventDefault();
              input.current?.click();
            }
          }}
          onDragOver={(e) => {
            e.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            if (!busy) choose(e.dataTransfer.files[0]);
          }}
          className={`flex min-h-60 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-8 text-center outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring ${dragging ? "border-primary bg-muted" : "border-border bg-muted/30 hover:bg-muted/60"}`}
        >
          {preview ? (
            <img
              src={preview}
              alt="待上传的试卷"
              className="max-h-56 max-w-full rounded object-contain"
            />
          ) : (
            <>
              <div className="mb-4 rounded-full border bg-white p-4">
                <UploadCloud className="text-muted-foreground" size={26} />
              </div>
              <p className="font-medium">点击选择，或拖入试卷照片</p>
              <p className="mt-2 text-xs text-muted-foreground">
                请使用清晰、完整的原图
              </p>
            </>
          )}
        </label>
        {file && (
          <div className="mt-3 flex items-center justify-between gap-3 text-sm">
            <span className="truncate text-muted-foreground">{file.name}</span>
            <Button
              variant="ghost"
              size="icon"
              aria-label="移除图片"
              disabled={busy}
              onClick={() => {
                setFile(null);
                if (input.current) input.current.value = "";
              }}
            >
              <X size={16} />
            </Button>
          </div>
        )}
        <Button
          className="mt-5 w-full sm:w-auto"
          disabled={!file || busy || !!blocked}
          onClick={upload}
        >
          {busy ? (
            <Loader2 className="animate-spin" size={16} />
          ) : (
            <FileText size={16} />
          )}
          {busy ? "正在上传…" : blocked || "识别并生成 Word"}
        </Button>
        <p className="mt-3 text-xs text-muted-foreground">
          识别并生成 Word 预计需要约 3 分钟，排队时可能更久。
        </p>
        <p className="mt-2 text-xs text-muted-foreground">
          每个账号同时处理一张试卷，两次提交至少间隔 30 秒。
        </p>
      </CardContent>
    </Card>
  );
}

function Jobs({
  jobs,
  onRetry,
}: {
  jobs: Job[];
  onRetry: (id: string) => Promise<void>;
}) {
  const previewTrigger = useRef<HTMLButtonElement | null>(null);
  const [selected, setSelected] = useState<{
      id: string;
      kind: "image" | "word";
    } | null>(null),
    [retrying, setRetrying] = useState<string | null>(null);
  if (!jobs.length)
    return (
      <div className="rounded-xl border bg-white px-6 py-12 text-center">
        <FileImage className="mx-auto mb-3 text-muted-foreground" size={28} />
        <p className="text-sm font-medium">还没有上传试卷</p>
        <p className="mt-2 text-xs text-muted-foreground">
          上传第一张图片，生成的 Word 会出现在这里。
        </p>
      </div>
    );
  return (
    <div className="space-y-3">
      {jobs.map((job) => (
        <Card key={job.id} className="gap-0 py-0 shadow-none">
          <CardContent className="p-4 sm:p-5">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div className="flex min-w-0 items-center gap-3">
                <div
                  className={`shrink-0 rounded-lg p-3 ${job.status === "completed" ? "bg-emerald-50 text-emerald-700" : "bg-muted text-muted-foreground"}`}
                >
                  {job.status === "processing" ? (
                    <Loader2 size={20} className="animate-spin" />
                  ) : job.status === "completed" ? (
                    <Check size={20} />
                  ) : (
                    <FileText size={20} />
                  )}
                </div>
                <div className="min-w-0">
                  <p className="max-w-60 truncate text-sm font-medium sm:max-w-md">
                    {job.title || job.original_name}
                  </p>
                  <div className="mt-1.5 flex flex-wrap items-center gap-2">
                    <Badge
                      variant={
                        job.status === "failed" ? "destructive" : "secondary"
                      }
                    >
                      {job.stage}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {date(job.created_at)}
                    </span>
                    {job.warnings.length > 0 && (
                      <span className="text-xs text-amber-700">
                        {job.warnings.length} 处待核对
                      </span>
                    )}
                  </div>
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    setSelected(
                      selected?.id === job.id && selected.kind === "image"
                        ? null
                        : { id: job.id, kind: "image" },
                    )
                  }
                >
                  {selected?.id === job.id && selected.kind === "image"
                    ? "收起原图"
                    : "查看原图"}
                </Button>
                {job.status === "completed" && (
                  <Button
                    size="sm"
                    variant="outline"
                    aria-expanded={
                      selected?.id === job.id && selected.kind === "word"
                    }
                    onClick={(event) => {
                      previewTrigger.current = event.currentTarget;
                      setSelected(
                        selected?.id === job.id && selected.kind === "word"
                          ? null
                          : { id: job.id, kind: "word" },
                      );
                    }}
                  >
                    <Eye size={15} />
                    预览 Word
                  </Button>
                )}
                {job.status === "completed" && (
                  <Button size="sm" asChild>
                    <a href={`/api/jobs/${job.id}/download`}>
                      <Download size={15} />
                      下载 Word
                    </a>
                  </Button>
                )}
                {job.status === "failed" && job.attempts < 3 && (
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={retrying === job.id}
                    onClick={async () => {
                      setRetrying(job.id);
                      await onRetry(job.id);
                      setRetrying(null);
                    }}
                  >
                    重试
                  </Button>
                )}
              </div>
            </div>
            {job.error && (
              <p className="mt-3 text-sm text-destructive">{job.error}</p>
            )}
            {(job.status === "queued" || job.status === "processing") && (
              <p className="mt-3 text-xs text-muted-foreground">
                预计需要约 3 分钟，完成后即可预览和下载 Word。
              </p>
            )}
            {job.warnings.length > 0 && (
              <div className="mt-3 space-y-1 rounded-md bg-amber-50 p-3 text-xs leading-relaxed text-amber-900">
                {job.warnings.map((warning, i) => (
                  <p key={i}>{warning}</p>
                ))}
              </div>
            )}
            {selected?.id === job.id && selected.kind === "word" && (
              <WordPreview
                key={job.id}
                jobId={job.id}
                title={job.title || job.original_name}
                onClose={() => setSelected(null)}
                onRestoreFocus={() => previewTrigger.current?.focus()}
              />
            )}
            {selected?.id === job.id && selected.kind === "image" && (
              <div className="mt-5 rounded-lg border bg-muted/30 p-3">
                <img
                  alt={job.original_name}
                  src={`/api/jobs/${job.id}/image`}
                  className="mx-auto max-h-[650px] max-w-full object-contain"
                  loading="lazy"
                />
              </div>
            )}
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function Accounts({ onError }: { onError: (err: unknown) => void }) {
  const [users, setUsers] = useState<User[]>([]),
    [busy, setBusy] = useState(false),
    [credentials, setCredentials] = useState<Credentials | null>(null),
    [copied, setCopied] = useState(false);
  const load = useCallback(async () => {
    try {
      setUsers((await api("/admin/users")).users);
    } catch (err) {
      onError(err);
    }
  }, [onError]);
  useEffect(() => {
    void load();
  }, [load]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    try {
      setCredentials(
        await api("/admin/users", "POST", { username: data.get("username") }),
      );
      setCopied(false);
      form.reset();
      await load();
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }
  async function action(user: User, type: "reset" | "toggle") {
    setBusy(true);
    try {
      const data = await api(`/admin/users/${user.id}/${type}`, "POST");
      if (type === "reset") {
        setCredentials(data);
        setCopied(false);
      }
      await load();
    } catch (err) {
      onError(err);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-6">
      <Card className="shadow-none">
        <CardHeader>
          <CardTitle className="text-base">创建用户账号</CardTitle>
          <CardDescription>
            密码由系统生成，只在创建或重置时显示。
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            className="flex flex-col gap-3 sm:flex-row sm:items-end"
            onSubmit={create}
          >
            <div className="flex-1 space-y-2">
              <Label htmlFor="new-username">账号</Label>
              <Input
                id="new-username"
                name="username"
                placeholder="例如 teacher01"
                required
                minLength={3}
                maxLength={32}
                pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{2,31}"
              />
            </div>
            <Button disabled={busy}>
              {busy ? (
                <Loader2 className="animate-spin" size={16} />
              ) : (
                <Users size={16} />
              )}
              创建账号
            </Button>
          </form>
          {credentials && (
            <div className="mt-5 rounded-lg border bg-muted/30 p-4">
              <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                <ShieldCheck size={16} />
                请保存这组登录信息
              </div>
              <dl className="grid gap-2 text-sm">
                <div>
                  <dt className="inline text-muted-foreground">账号：</dt>
                  <dd className="inline font-mono">{credentials.username}</dd>
                </div>
                <div>
                  <dt className="inline text-muted-foreground">密码：</dt>
                  <dd className="inline break-all font-mono">
                    {credentials.password}
                  </dd>
                </div>
              </dl>
              <Button
                className="mt-4"
                size="sm"
                variant="outline"
                onClick={async () => {
                  try {
                    await navigator.clipboard.writeText(
                      `网址：https://paper.guanchi.ren\n账号：${credentials.username}\n密码：${credentials.password}`,
                    );
                    setCopied(true);
                  } catch {
                    onError(
                      new Error("无法访问剪贴板，请手动复制账号和密码。"),
                    );
                  }
                }}
              >
                {copied ? <Check size={14} /> : <Clipboard size={14} />}
                {copied ? "已复制" : "复制登录信息"}
              </Button>
              <Button
                className="ml-2"
                size="sm"
                variant="ghost"
                onClick={() => setCredentials(null)}
              >
                隐藏
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
      <Card className="overflow-hidden py-0 shadow-none">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>账号</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="hidden sm:table-cell">创建时间</TableHead>
              <TableHead className="text-right">操作</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((user) => (
              <TableRow key={user.id}>
                <TableCell className="font-medium">
                  {user.username}
                  {user.role === "admin" && (
                    <Badge variant="outline" className="ml-2">
                      管理员
                    </Badge>
                  )}
                </TableCell>
                <TableCell>
                  <Badge variant={user.active ? "secondary" : "outline"}>
                    {user.active ? "可用" : "已停用"}
                  </Badge>
                </TableCell>
                <TableCell className="hidden text-muted-foreground sm:table-cell">
                  {user.created_at ? date(user.created_at) : ""}
                </TableCell>
                <TableCell className="text-right">
                  {user.role !== "admin" && (
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => void action(user, "reset")}
                      >
                        重置密码
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => void action(user, "toggle")}
                      >
                        {user.active ? "停用" : "启用"}
                      </Button>
                    </div>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
    </div>
  );
}
