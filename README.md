# Paper

把试卷照片转成可编辑的 Word。用户使用管理员创建的账号登录，上传一页试卷，后台经 Codex CLI 提取题目、选项和 LaTeX 公式，固定程序将结果转换成 Word 原生公式并生成 `.docx`。

## 功能

- 不开放注册；管理员创建账号、生成或重置密码、停用账号。
- 支持 JPG、PNG、WebP 静态图片，最大 12 MB、3500 万像素。
- 上传前选择标准或精细档位；任务记录使用的档位，失败重试沿用该档位。
- 页面保留「印刷试卷」并新增「手写识别」。手写固定标准档 `medium`，保留学生错答和计算过程，将已划掉的草稿与待核对内容单独列入可编辑 Word。
- 手写可选自动、单页或左右双页布局。自动模式将宽高比至少 1.2 的图片按左右双页处理；横向单页可手动改为单页。双页沿中线无重叠裁开，以 PNG 输入一次识别，不重复输入整图。
- 每个账号最多一个排队或运行中的任务；两次成功提交或重试至少间隔 30 秒。
- 独立后台队列，整台机器同时处理一个任务，失败支持有限次数重试。
- 用户只能查看自己的原图、在弹窗预览自己的 Word 或下载文件。服务器用 LibreOffice 将原 Word 渲染为分页图像，避免浏览器再次转换公式；不同办公软件的字体和分页可能略有差异。
- 上传区域和处理中的任务提示预计约 3 分钟，排队时可能更久。
- 文字、分式、根号、指数、分段函数使用可编辑的 Word 原生格式；复杂配图保留局部截图。
- 模糊内容标记为待核对，缺失的页或题不补写。
- shadcn/ui + React 前端；Flask + SQLite 后端；Caddy 自动 HTTPS。

## 本地开发

需要 Python 3.12、Node.js 22 或更高版本，以及已登录的 Codex CLI。

```sh
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.lock
cd frontend
npm ci
npm run build
cd ..
```

先创建本地管理员。命令仅显示一次随机密码，已有账号不会被覆盖：

```sh
.venv/bin/python -m paper.manage create-admin --username guanchi
```

在两个终端中分别运行网站与 Worker：

```sh
PAPER_SECURE_COOKIE=0 PAPER_ALLOWED_ORIGINS=http://127.0.0.1:8030 .venv/bin/python -m paper.serve
.venv/bin/python -m paper.worker
```

浏览器打开 `http://127.0.0.1:8030`。上传后 Worker 自动处理；终止 Worker 会将处理中任务标记为中断，用户可重试。

## 模型与识别

默认模型为 `gpt-6.1-sol`。新上传任务默认标准档（`medium`），可切换精细档（`high`）；历史任务迁移后标为精细档。档位保存在任务中，失败重试不会因页面当前选择而改变。通过 `PAPER_CODEX_MODEL` 配置模型；更换模型前应使用真实试卷逐题比较，尤其是根号次数、指数、对数底数、不等号、跨栏选项和被批注遮挡的部分。

上述档位切换仅用于印刷试卷。手写模式使用独立的提示、JSON Schema 和文档排版流程，服务端始终使用 `medium`。它保留可见的手写题目、答案与过程，不纠错、不评分；模糊和覆盖笔迹需人工核对，不把图形或选择题涂卡当成手写答案。所有模式继续使用原有认证、限流、队列、预览和下载权限。

`POST /api/jobs` 新增可选表单字段：`recognition_type=printed|handwritten`（默认 `printed`）和 `page_layout=auto|single|double`（手写默认 `auto`）。服务端将手写的 `recognition_mode` 规范为 `standard`，保存最终 `single|double` 布局；印刷保持 `single` 及原有标准/精细档位。历史任务增量迁移为印刷单页，重试保留已保存的模式、档位和布局。

实验性 `PAPER_IMAGE_ZOOM=1` 会同时提供完整图和四个重叠局部图；默认关闭。Luna 的质量对照仍有未提示的公式及选项错误，生产模型保持 Sol 6.1。medium 和 high 都需要核对，精细档也不保证修复所有错误。详见 [验证记录](docs/validation.md)。

图片先按 EXIF 校正方向，长边最多 3200 像素，去除元数据并以 JPEG 95 保存；优化 Huffman 编码以减少文件体积，不降低既定编码质量。文件 KB/MB 下降不等于图片 token 同比例下降；不会为节省 token 自动裁掉题目或强行缩至低分辨率。

CLI 采用 `codex exec --json --output-schema`，同时关闭 shell、多智能体、插件、应用、钩子、浏览器、电脑操作及网页搜索。识别结果进入 JSON Schema 校验后再生成文档。每个任务的 `events.jsonl` 保留 CLI 用量记录；凭据和任务文件不提供公开静态访问。

不能保证所有照片都被正确识别。模糊、遮挡、复杂配图及低质量拍摄应由使用者核对。LaTeX 无法转换时，任务显示失败，不以图片公式冒充可编辑公式。

## 验证

```sh
.venv/bin/python -m pytest -q
cd frontend
npm run build
```

测试覆盖认证、CSRF、管理员权限、停用后会话撤销、用户文件隔离、并发提交竞争、30 秒限流、失败重试及可编辑 Word 公式。

部署方式见 [deploy/README.md](deploy/README.md)。
