# 部署

生产服务由 `paper-web`、`paper-worker`、`paper-proxy`、`paper-caddy` 和独立预览服务 `paper-preview` 组成。网站只监听 `127.0.0.1:8030`；公网入口只有 Caddy 的 80/443。Caddy 为 `paper.guanchi.ren` 和 `www.paper.guanchi.ren` 申请免费证书并自动续期，www 重定向到主域名。

## 目录

| 路径 | 用途 |
| --- | --- |
| `/srv/paper/releases/` | 应用版本，root 所有，运行用户只读 |
| `/srv/paper/current` | 当前版本符号链接 |
| `/srv/paper/venv` | Python 虚拟环境 |
| `/opt/paper/python` | 运行用户可读的独立 Python 3.12 |
| `/var/lib/paper` | 数据库、任务和 Codex 登录状态，paper 用户所有 |
| `/etc/paper/paper.env` | 应用配置，root:paper，0640 |
| `/etc/paper/Caddyfile` | HTTPS 配置 |
| `/etc/paper-proxy/config.yaml` | 私有代理配置，root:paper-proxy，0640 |
| `/var/lib/caddy` | ACME 账号和证书，caddy 用户所有 |

## 环境准备

1. 创建 `paper`、`paper-proxy`、`caddy` 三个系统用户，分别使用 `/var/lib/<用户名>` 作为主目录。
2. 安装 Python 3.12、Node.js 22+、Codex CLI、Caddy、Mihomo 与 Docker。预览镜像使用 Ubuntu 24.04 和官方 LibreOffice 26.8.1，因为主机的旧 LibreOffice 7.1 会错误解析部分公式，26.8 的官方二进制需要更新的 glibc。软件和 Python 解释器不能放在其他用户无法访问的 `/root` 子目录内。
3. 在 `/srv/paper/venv` 安装 `requirements.lock`；前端在开发机执行 `npm ci && npm run build`，将产物随代码一起部署。
4. 使用 `paper` 用户运行 `codex login --device-auth`。代理仅监听 loopback；将必要的高级专线狮城节点配置保存在仓库外。不复制代理订阅、节点密码或 Codex 凭据到代码仓库。
5. 将 `paper.env.example` 复制到 `/etc/paper/paper.env`，根据实际目录调整。将本目录的 systemd 文件和 Caddyfile 放到对应位置。构建预览镜像，步骤见下文。
6. 打开 80/443 的系统防火墙和云安全组；确认两个域名的 A 记录指向这台机器。
7. 以 paper 用户执行 `python -m paper.manage create-admin`；将一次性输出保存在只有管理员可读的位置。
8. 执行 `systemctl daemon-reload` 与 `systemctl enable --now paper-proxy paper-caddy paper-web paper-worker paper-preview`。

管理员密码丢失时，可在应用目录中用 paper 用户运行 `python -m paper.manage reset-password --username guanchi`，使用实际管理员登录名。命令会生成新的随机密码并撤销该账号已有会话；不要将输出写入公共日志。

Codex 通过平台运营者授权的 ChatGPT 登录状态或 API Key 认证。订阅方式受账号配额限制，API Key 方式按用量计费；不要把 CLI 本身或任意命令执行接口直接暴露给网站用户。

## 更新与回滚

将代码与已构建的 `frontend/dist` 解压到新的 release 目录。运行账号不应拥有修改应用代码的权限。更新 `current` 符号链接后重启 `paper-web` 和 `paper-worker`。更新前避免正在执行的任务；重启时，中断任务会显示为可重试失败。

SQLite 和上传文件位于 `/var/lib/paper`，不随代码发布覆盖。回滚时将 `current` 指回先前版本，再重启应用服务。数据库结构发生变化时需先设计相应迁移，不能直接用旧代码覆盖新数据。

## 运维检查

```sh
systemctl is-active paper-web paper-worker paper-proxy paper-caddy paper-preview
journalctl -u paper-web -u paper-worker -n 50 --no-pager
curl -f https://paper.guanchi.ren/healthz
```

代理凭据、Codex 登录状态、数据库、任务目录和一次性密码需要单独保护。管理员重置普通账号密码或停用账号，会立即撤销该账号已有会话。任务上限是每账号一个（含排队），全局队列最多 30 个。

## 分页预览

独立 `paper-preview` 服务逐个补齐已有 Word 的预览，包含已完成的旧任务。网站与识别 Worker 继续使用低权限的 paper 用户，无 Docker 权限。预览监督进程由 root 启动固定镜像，每个容器以 paper 的 UID/GID 运行，只挂载一个任务目录和只读的应用渲染代码；禁止网络、移除 capabilities、只读根文件系统，限制 512 MB 内存、1 个 CPU 和 80 个进程。任务目录与 Word 必须为普通路径，拒绝符号链接。不会挂载 Codex 认证目录。

使用 `deploy/Dockerfile.preview` 构建 `paper-preview:26.8.1`。构建上下文包含 `paper/preview.py`、`deploy/fonts.conf` 和经过官方 SHA-256 校验的完整安装目录 `libreoffice26.8/`。首次验证使用官方 `LibreOffice_26.8.1_Linux_x86-64_rpm.tar.gz`，SHA-256 为 `5d2b44386e10d24e181f221dffe34e773f1743ddb93ce7b4d08e95affc288256`；下载地址及散列见 Document Foundation 官方镜像列表。镜像安装 Poppler、中文/数学字体和 Python Pillow。需要代理时在构建时传入 http_proxy/https_proxy，不把代理凭据打进镜像。

渲染失败不影响 Word 下载；每次转换有超时和 30 页上限，使用独立 LibreOffice 配置目录，结束后删除临时 PDF、配置和容器。预览是 Office 渲染的页面图像，不再转换为网页公式；Microsoft Word 与 LibreOffice 的字体和分页仍可能略有差异。

LibreOffice 会给「a=____」这类缺少右侧表达式的公式添加红色占位符。渲染时仅在临时副本中将末尾等号移到相邻文字，保留可见等号并对齐基线。源 Word 的字节及下载内容不修改，临时副本随后删除。

`jobs/<任务ID>/preview.json` 记录源 Word 的 SHA-256、渲染版本和页尺寸；PNG 放在 `preview/`，只能通过登录且属于当前账号的接口访问。重新生成 Word 后旧缓存自动失效。失败结果会缓存以避免反复占用资源；修复依赖后可删除该任务的 `preview.json` 等待预览服务重新生成。镜像中的 `PAPER_SOFFICE_BIN` 指向 26.8；开发时也可用 `PAPER_PDFINFO_BIN`、`PAPER_PDFTOPPM_BIN` 指定程序路径。
