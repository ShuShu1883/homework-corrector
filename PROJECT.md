# 基于大模型的中小学作业智能批改系统

## 项目目标

本项目是一个面向 Python 大作业的智能作业批改系统。用户上传作业图片后，系统异步执行 OCR 识别和大模型批改，最后展示结构化批改结果、总分、评语和学习建议。

第一版目标不是替代真实阅卷系统，而是完成一条可演示、可讲解、架构完整的 AI 作业批改流程。

## 技术架构

- 前端界面：Streamlit
- 后台任务：Python `queue.Queue` + 后台线程池
- OCR 模块：腾讯云题目识别 OCR
- 智能批改：OpenAI-compatible Chat Completions API
- 数据存储：本地 JSON 文件 / MySQL / 腾讯云 COS 结果 JSON
- 账号系统：本地 JSON 文件 + Streamlit Session State
- 文件存储：默认使用项目运行目录下的 `uploads/`、`processed/`、`cuts/`、`results/`、`data/` 目录，可通过 `APP_RUNTIME_DIR` 改到部署环境可写目录；也可以通过 `FILE_STORAGE_BACKEND=cos` 将新任务产物和结果 JSON 保存到腾讯云 COS。

系统采用生产者-消费者模型。用户上传图片后，前端立即生成任务 ID 并将任务放入队列。后台 worker 线程从队列中取出任务，依次执行腾讯云切题 OCR、大模型批改和结果保存。前端通过任务 ID 查询任务状态。启用 COS 后，图片处理过程中仍会临时写入本地文件，任务成功保存到 COS 后会删除对应本地临时产物；系统不再按时间定时扫描删除运行目录。

## 模块划分

- `app.py`：Streamlit 根入口，仅负责调用 `homework_corrector.app_main.main()`，保持 `streamlit run app.py` 和 Streamlit Cloud 入口兼容。
- `homework_corrector/app_main.py`：应用主流程，负责页面路由、登录态判断、任务提交和结果展示调度。
- `homework_corrector/core/`：配置读取、Session State、时间工具、资源路径和运行目录清理等基础工具。
- `homework_corrector/ui/`：Streamlit 页面、上传输入、结果视图、分析页面和主题样式。
- `homework_corrector/auth/`：简化账号注册、登录校验和本地 JSON 存储。
- `homework_corrector/processing/`：图片增强、腾讯云题目识别 OCR、大模型批改和分数统计。
- `homework_corrector/storage/`：本地 JSON、MySQL、腾讯云 COS 和结果附件持久化。
- `homework_corrector/tasks/`：任务队列、后台 worker 流程和手机拍照上传临时令牌。

## 登录系统

系统支持用户注册、登录和退出。登录状态仅保存在当前 Streamlit 页面会话中，刷新网页后需要重新登录。新提交的批改任务会记录所属用户名，批改记录只展示当前用户自己的结果；升级前生成的不含用户名的历史结果不会在登录后的页面中展示。

账号保存在 `APP_RUNTIME_DIR/data/users.json`。当前版本为了保持实现简单，密码以明文保存在本地文件中，只适合低风险、小规模部署。用户不应使用其他网站的常用密码。若面向真实用户长期运行，应升级为密码哈希和更完整的会话管理。

## 任务状态

- `waiting`：任务已提交，等待后台线程处理。
- `running`：任务正在执行 OCR 和大模型批改。
- `finished`：任务处理完成，结果已保存。
- `failed`：任务处理失败，页面展示错误原因。

## 图片增强模块

系统新增独立的图片增强页面，用于模拟手机文档模式或扫描软件的基础能力。用户上传整页作业照片后，系统会使用 OpenCV 检测纸张四角，裁剪背景并进行透视校正，然后生成保留颜色信息的清晰增强图。增强主要处理亮度通道，用于减弱阴影、提升对比度和轻度锐化。

如果系统无法稳定检测到文档四角，会自动退化为整图文字增强，避免因为图片边界不明显导致流程中断。后续接入 OCR 主流程时，可优先使用清晰增强图作为 OCR 输入。

## 题目识别模块

系统新增独立的题目识别页面，用于调用腾讯云 `QuestionSplitOCR` 接口。用户上传整页作业图片后，可选择是否使用新模型以及是否开启切边增强/弯曲矫正。接口返回后，系统展示每道题的识别文字、坐标信息，并根据坐标裁出题目区域小图。作业批改主流程也会优先使用该题目识别 OCR 结果进入逐题批改。

腾讯云 SecretId/SecretKey 只通过 `.env` 或 Streamlit Secrets 读取，不写入源码或提交到 GitHub。

## 本地运行

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 创建 `.env`：

```text
TENCENT_SECRET_ID=你的腾讯云SecretId
TENCENT_SECRET_KEY=你的腾讯云SecretKey
TENCENT_OCR_REGION=ap-guangzhou
LLM_MODE=mock
MAX_WORKERS=3
```

3. 启动应用：

```bash
streamlit run app.py
```

## API 配置

真实 OCR 模式需要配置：

```text
TENCENT_SECRET_ID=你的腾讯云SecretId
TENCENT_SECRET_KEY=你的腾讯云SecretKey
TENCENT_OCR_REGION=ap-guangzhou
```

大模型 API 默认采用 DeepSeek OpenAI-compatible 格式，批改模型使用推理质量更好的 `deepseek-v4-pro`，批改请求使用低随机性配置以减少简单计算题误判。API key 只能放在本地 `.env` 或 Streamlit Secrets 中，不要写入源码或提交到 GitHub：

```text
LLM_MODE=api
LLM_API_KEY=你的DeepSeek API Key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
LLM_MAX_TOKENS=4096
DEEPSEEK_THINKING=disabled
LLM_CONSISTENCY_RETRIES=2
```

也可以使用 `DEEPSEEK_API_KEY` 代替 `LLM_API_KEY`。如果切换到其他 OpenAI-compatible 服务商，只需要修改 `LLM_BASE_URL` 和 `LLM_MODEL`。

腾讯云 COS 存储可选配置：

```text
FILE_STORAGE_BACKEND=cos
COS_SECRET_ID=你的腾讯云 COS SecretId
COS_SECRET_KEY=你的腾讯云 COS SecretKey
COS_REGION=ap-guangzhou
COS_BUCKET=你的存储桶名-APPID
COS_PREFIX=homework-correction
COS_PUBLIC_BASE_URL=https://你的存储桶名-APPID.cos.ap-guangzhou.myqcloud.com
```

`COS_BUCKET` 需要使用腾讯云完整存储桶名称，例如 `examplebucket-1250000000`。当前实现按公有读 URL 展示图片；如果存储桶默认域名无法直接预览，请配置自定义域名并写入 `COS_PUBLIC_BASE_URL`。

## 报告可用表述

本系统采用异步任务处理机制。用户上传作业后，系统不会同步等待题目识别 OCR 和大模型批改完成，而是立即生成任务 ID，并将任务加入内存消息队列。后台线程池从队列中取出任务并并发处理腾讯云题目识别 OCR、智能批改和结果保存。前端通过任务 ID 查询任务状态，实现 waiting、running、finished、failed 等状态展示。

当前实现适用于单机小规模并发场景。若部署到生产环境，可将内存队列升级为 Redis/Celery，将文件存储迁移至对象存储，将任务结果保存到数据库中，从而支持多进程、多节点和更高并发。

## 后续扩展

- 接入更稳定的教育 OCR/切题接口。
- 根据 OCR bbox 在原图上绘制错误红框。
- 使用 SQLite 保存历史记录和统计数据。
- 生成 Word/PDF 批改报告。
- 增加班级平均分、错题率、知识点统计等图表。
