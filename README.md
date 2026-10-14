# 中国 5A 景区 · Reddit 草稿工具

在本机按省份、城市生成 Reddit 草稿。每个帖子介绍一座城市的全部 5A 景区：历史、为何评为 5A、主要看点。图片优先用有许可的真实照片，找不到时才用本地 AI 绘制。草稿只保存在本地，不会自动发布。

## 运行

需要 Node.js、Python 3.12，以及本机的 Ollama、Chrome。图片回退还需要本机 ComfyUI。

```bash
copy .env.example .env
npm install
pip install -r requirements-city.txt
python -m playwright install chromium
npm run dev
```

浏览器打开 http://127.0.0.1:5173 。先选省份，再选城市，点「生成 / 补跑这个城市的帖子」。也可以一次跑所选省份里还没完成的城市。`npm run dev` 会同时启动网页和 Python 后端，关掉这个终端才会停。

也可以不打开网页：

```bash
python scenic_pipeline.py --province 浙江 --city 金华市
python scenic_pipeline.py --province 浙江
```

`--no-llm` 和 `--no-images` 只用于离线检查，不会写出完整帖子。

## 配置（.env）

`.env` 已被 git 忽略，不要提交。字段见 `.env.example`。

| 变量 | 用途 |
| --- | --- |
| `UPSPLASH_ACCESS_KEY` | 搜索 Unsplash 照片。演示额度每小时约 50 次 |
| `SCENIC_MODEL` | 写英文的本地模型，默认 `qwen2.5:3b` |
| `COMFY_PYTHON`、`COMFY_CHECKPOINT`、`COMFY_PERSON_MODEL` | 本地绘图和人物检测，必填 |
| `COMFY_URL`、`COMFY_INPUT_DIR`、`COMFY_OUTPUT_DIR` | 留空则用本机默认值 |
| `DEEPSEEK_API_KEY` | 只给旧的 `main.py` 用，5A 流程不读取 |

## 一篇帖子怎么来

景区名单在 `provinces_5a/`，32 个省区、359 个景区，依据文旅部截止 2025-03-11 的目录。城市从景区名称里解析出来，共 221 个；直辖市各算一个城市。跨省或名称里没有城市的景区，写在 `scenic_pipeline.py` 的 `CITY_OVERRIDES`。评定年份有争议的条目标了 `rating_year_disputed`，帖子里不写这个年份。

每个景区：

1. 先查中文维基百科和对应英文条目，查不到再读百度百科。资料缓存在 `work/scenic/`。
2. 本地模型只根据这些资料写英文。没有资料来源的景区只列名字，不编内容。
3. 找一张图，按这个顺序：Unsplash，然后 Wikimedia Commons 上可复用的照片（CC BY、CC BY-SA、CC0、公有领域）。照片说明里必须出现景区的英文名或拼音专名。
4. 两处都没有时，用无头 Chrome 从百度图片取一张参考图，交给本地 ComfyUI 重绘并去掉人物。参考图不发布。

有许可的照片在正文末尾署名。帖子不附加 AI 声明。画面是否对得上景区，发布前需要人工核对。

结果在 `city_posts/5a/省份/城市_版本/`：`title.txt`、`body.txt`、`post.json`、按景区顺序编号的 PNG、`image_search.json`。

网页上勾选「我已核对」之后，可以让本地模型审一遍，再经专用 Chrome 窗口发到 Reddit。准备和发布是两步，点发布才会提交。

## 测试

```bash
set PYTHONIOENCODING=utf-8
set PYTHONPATH=tests
python -m unittest discover -s tests -t .
```

用项目实际运行的那个 Python。系统自带的 Python 没有装 Pillow 时，城市相关测试会失败。
