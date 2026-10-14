# 部署

生产服务由四个 systemd 单元组成：`paper-web`、`paper-worker`、`paper-proxy`、`paper-caddy`。网站只监听 `127.0.0.1:8030`；公网入口只有 Caddy 的 80/443。Caddy 为 `paper.guanchi.ren` 和 `www.paper.guanchi.ren` 申请免费证书并自动续期，www 重定向到主域名。

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
2. 安装 Python 3.12、Node.js 22+、Codex CLI、Caddy，以及 Mihomo。软件和 Python 解释器不能放在其他用户无法访问的 `/root` 子目录内。
3. 在 `/srv/paper/venv` 安装 `requirements.lock`；前端在开发机执行 `npm ci && npm run build`，将产物随代码一起部署。
4. 使用 `paper` 用户运行 `codex login --device-auth`。代理仅监听 loopback；将必要的高级专线狮城节点配置保存在仓库外。不复制代理订阅、节点密码或 Codex 凭据到代码仓库。
5. 将 `paper.env.example` 复制到 `/etc/paper/paper.env`，根据实际目录调整。将本目录的 systemd 文件和 Caddyfile 放到对应位置。
6. 打开 80/443 的系统防火墙和云安全组；确认两个域名的 A 记录指向这台机器。
7. 以 paper 用户执行 `python -m paper.manage create-admin`；将一次性输出保存在只有管理员可读的位置。
8. 执行 `systemctl daemon-reload` 与 `systemctl enable --now paper-proxy paper-caddy paper-web paper-worker`。

管理员密码丢失时，可在应用目录中用 paper 用户运行 `python -m paper.manage reset-password --username admin`。命令会生成新的随机密码并撤销该账号已有会话；不要将输出写入公共日志。

Codex 通过平台运营者授权的 ChatGPT 登录状态或 API Key 认证。订阅方式受账号配额限制，API Key 方式按用量计费；不要把 CLI 本身或任意命令执行接口直接暴露给网站用户。

## 更新与回滚

将代码与已构建的 `frontend/dist` 解压到新的 release 目录。运行账号不应拥有修改应用代码的权限。更新 `current` 符号链接后重启 `paper-web` 和 `paper-worker`。更新前避免正在执行的任务；重启时，中断任务会显示为可重试失败。

SQLite 和上传文件位于 `/var/lib/paper`，不随代码发布覆盖。回滚时将 `current` 指回先前版本，再重启应用服务。数据库结构发生变化时需先设计相应迁移，不能直接用旧代码覆盖新数据。

## 运维检查

```sh
systemctl is-active paper-web paper-worker paper-proxy paper-caddy
journalctl -u paper-web -u paper-worker -n 50 --no-pager
curl -f https://paper.guanchi.ren/healthz
```

代理凭据、Codex 登录状态、数据库、任务目录和一次性密码需要单独保护。管理员重置普通账号密码或停用账号，会立即撤销该账号已有会话。任务上限是每账号一个（含排队），全局队列最多 30 个。
