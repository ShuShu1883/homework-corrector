# 基于大模型的中小学作业智能批改系统

## 项目目标

本项目是一个面向 Python 大作业的智能作业批改系统。用户上传作业图片后，系统异步执行 OCR 识别和大模型批改，最后展示结构化批改结果、总分、评语和学习建议。

第一版目标不是替代真实阅卷系统，而是完成一条可演示、可讲解、架构完整的 AI 作业批改流程。

## 技术架构

- 前端界面：Streamlit
- 后台任务：Python `queue.Queue` + 后台线程池
- OCR 模块：腾讯云试卷切题 OCR + 阿里云 OCR HTTP 适配器
- 智能批改：OpenAI-compatible Chat Completions API
- 数据存储：本地 JSON 文件
- 文件存储：本地 `uploads/` 目录

系统采用生产者-消费者模型。用户上传图片后，前端立即生成任务 ID 并将任务放入队列。后台 worker 线程从队列中取出任务，依次执行腾讯云切题 OCR、大模型批改和结果保存。前端通过任务 ID 查询任务状态。

## 模块划分

- `app.py`：Streamlit 页面入口，负责上传图片、提交任务、展示状态和结果。
- `task_queue.py`：任务队列、任务状态管理、后台 worker 启动控制。
- `worker.py`：单个作业任务的完整处理流程。
- `image_processing.py`：文档图片加工模块，负责文档边界检测、透视校正和文字增强。
- `paper_cut_tencent.py`：腾讯云试卷切题 OCR 模块，调用 `QuestionSplitOCR` 并按返回坐标裁出题目小图。
- `paper_cut_aliyun.py`：旧版阿里云试卷切题模块，保留作参考和兼容。
- `ocr_aliyun.py`：阿里云 OCR 调用封装，保留普通 OCR 适配能力。
- `llm_corrector.py`：大模型批改封装，对外只暴露 `correct_homework`。
- `storage.py`：批改结果 JSON 保存和读取。
- `config.py`：环境变量、`.env`、Streamlit Secrets 配置读取。

## 任务状态

- `waiting`：任务已提交，等待后台线程处理。
- `running`：任务正在执行 OCR 和大模型批改。
- `finished`：任务处理完成，结果已保存。
- `failed`：任务处理失败，页面展示错误原因。

## 图片加工模块

系统新增独立的图片加工页面，用于模拟手机文档模式或扫描软件的基础能力。用户上传整页作业照片后，系统会使用 OpenCV 检测纸张四角，裁剪背景并进行透视校正，然后生成保留颜色信息的清晰增强图。增强主要处理亮度通道，用于减弱阴影、提升对比度和轻度锐化。

如果系统无法稳定检测到文档四角，会自动退化为整图文字增强，避免因为图片边界不明显导致流程中断。后续接入 OCR 主流程时，可优先使用清晰增强图作为 OCR 输入。

## 试卷切题模块

系统新增独立的试卷切题页面，用于调用腾讯云 `QuestionSplitOCR` 接口。用户上传整页试卷图片后，可选择是否使用新模型以及是否开启切边增强/弯曲矫正。接口返回后，系统展示每道题的识别文字、坐标信息，并根据坐标裁出题目区域小图。上传批改主流程也会优先使用该切题 OCR 结果进入逐题批改。

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

大模型 API 采用 OpenAI-compatible 格式：

```text
LLM_MODE=api
LLM_API_KEY=你的key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
```

如果使用 DeepSeek、通义千问等兼容接口，只需要修改 `LLM_BASE_URL` 和 `LLM_MODEL`。

## 报告可用表述

本系统采用异步任务处理机制。用户上传作业后，系统不会同步等待切题 OCR 和大模型批改完成，而是立即生成任务 ID，并将任务加入内存消息队列。后台线程池从队列中取出任务并并发处理腾讯云切题 OCR、智能批改和结果保存。前端通过任务 ID 查询任务状态，实现 waiting、running、finished、failed 等状态展示。

当前实现适用于单机小规模并发场景。若部署到生产环境，可将内存队列升级为 Redis/Celery，将文件存储迁移至对象存储，将任务结果保存到数据库中，从而支持多进程、多节点和更高并发。

## 后续扩展

- 接入更稳定的教育 OCR/切题接口。
- 根据 OCR bbox 在原图上绘制错误红框。
- 使用 SQLite 保存历史记录和统计数据。
- 生成 Word/PDF 批改报告。
- 增加班级平均分、错题率、知识点统计等图表。
