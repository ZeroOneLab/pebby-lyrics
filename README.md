# Pebby Lyrics

给本地音乐批量补齐同名 `.lrc` 歌词的 Windows 小工具：**QQ 音乐优先、LRCLIB 兜底**，不登录、不下载音频、不改动音乐文件本身。

图形界面和命令行共用同一套实现。适合给只认“同目录同名 `.lrc`”侧车歌词的播放器、车机、开发板（比如 [Pebby-Player](https://github.com/ZeroOneLab/Pebby-Player)）准备歌词。

![界面截图](docs/screenshot.png)

## 特性

| 能力 | 说明 |
|---|---|
| 批量扫描 | 递归处理目录，也可只给单个音频文件；只读取标签，不写音频 |
| 双来源择优 | 同专辑 → 简繁优先级 → 来源 → 时长差 → 行长，逐级比较 |
| 简繁处理 | 优先简体/繁体，首选找不到时自动用另一种；`--simplify` 才强制转简体（OpenCC） |
| 清理片头 | 默认移除 QQ 歌词开头的“歌名 - 歌手”“词曲/制作”信息行 |
| 长句拆行 | 超过设定字数拆成多条时间轴，适配小屏显示 |
| 拒绝覆盖 | 已有 `.lrc`（连空文件）一律跳过；写入用排他创建，失败时清理半成品 |
| 编码可选 | UTF-8（默认，无 BOM）或 GBK |
| 自带环境 | 首次运行按提示建 `.lrc-env` 隔离环境，不污染全局 Python |
| 更新提示 | 启动后每个自然日最多查一次 GitHub Release，可关闭，不上报任何信息 |

## 快速开始

### 方式一：下载 EXE（无需 Python）

1. 到 [Releases](../../releases/latest) 下载 `Pebby-Lyrics.exe`
2. 放到**可写目录**（桌面、`D:\Tools` 都行），**不要放 `C:\Program Files`**：单文件版会在 exe 同目录解压临时文件
3. 双击运行，选择音乐目录，点“开始生成”

Windows Defender 或 360 偶尔会对 PyInstaller 单文件报“未知程序”，加信任即可；本项目不做任何自解压之外的系统改动。

### 方式二：源码运行

需要 Python 3.10+（Windows x64 上缺 Python 时脚本可代为安装官方版本）：

```powershell
git clone https://github.com/ZeroOneLab/pebby-lyrics.git
cd pebby-lyrics

# 图形界面：首次会说明用途并询问是否安装 Mutagen / OpenCC
.\lrc_gui.cmd

# 命令行：把音乐目录拖到 prepare_lrc.cmd 上也可以
.\prepare_lrc.cmd "C:\Users\你\Music"
```

依赖只装在本目录的 `.lrc-env` 里，不修改 PATH、不写全局 site-packages。

## 图形界面

- 选择音乐目录、按扩展名过滤（MP3 / WAV / FLAC / M4A / OGG / OPUS）
- 优先简体或繁体、是否强制转简体
- 覆盖已有 LRC、移除片头信息、仅预览不写文件
- 最大时长差、结尾容差、行长阈值
- 来源（auto / qq / lrclib）与输出编码
- 实时日志、结果统计、保存 JSON 报告、批量删除歌词（优先进回收站）

窗口选项保存在 `%APPDATA%\Pebby-Lyrics\settings.json`。

## 命令行

```powershell
# 处理整个目录，默认 QQ 优先、LRCLIB 兜底
.\prepare_lrc.cmd "C:\Music"

# 只查询和校验，不写文件；报告名可自定义
.\prepare_lrc.cmd "C:\Music" --dry-run --report build\lrc-preview.json

# 优先繁体来源 / 强制转简体 / 调整行长 / 覆盖已有
.\prepare_lrc.cmd "C:\Music" --prefer traditional
.\prepare_lrc.cmd "C:\Music" --simplify
.\prepare_lrc.cmd "C:\Music" --max-line-chars 12
.\prepare_lrc.cmd "C:\Music" --overwrite

# 只查某一个来源、输出 GBK
.\prepare_lrc.cmd "C:\Music" --source qq --encoding gbk
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `root` | `桌面\音乐` | 音乐目录或单个音频文件 |
| `--source` | `auto` | `auto` / `qq` / `lrclib` |
| `--prefer` | `simplified` | 首选文字形态，找不到时自动用另一种 |
| `--simplify` | 关 | 强制把最终歌词（含元数据）转简体 |
| `--keep-credits` | 关 | 保留 QQ 片头的歌名/词曲行 |
| `--max-line-chars` | `18` | 长句拆分阈值，`0` 表示不拆 |
| `--overwrite` | 关 | 覆盖同名 `.lrc`；默认跳过且不联网 |
| `--encoding` | `utf-8` | `utf-8` / `gbk` |
| `--dry-run` | 关 | 只查询校验，不写文件 |
| `--report` | 无 | 输出 UTF-8 JSON 报告（不含歌词正文） |
| `--timeout` | `15` | 单次网络请求超时秒数 |
| `--version` | | 打印版本 |

`python lrc_gui.py --version` / `--check-update` 可在源码模式下查版本、查更新；打包后的 EXE 没有控制台，这两个参数会以消息框显示。

## 更新检查与隐私

- 只在 GUI 启动约 2.5 秒后，在后台线程请求一次 `api.github.com/repos/ZeroOneLab/pebby-lyrics/releases/latest`
- 每个自然日最多一次；设置里可关闭；“跳过此版本”会记住被跳过的版本，不再自动打扰
- 匿名 API 有 60 次/小时/IP 的配额，共享出口很容易被限流；此时改读 `releases/latest` 的跳转地址取版本号，不依赖 API 配额
- 请求只带 `User-Agent: Pebby-Lyrics/<版本>`，没有设备 ID、没有统计、没有遥测
- **不会**自动下载或替换程序，只打开浏览器到发布页
- 命令行默认不联网，只有显式 `--check-update` 才请求

## 常见问题

**提示“没有符合优先级的同步歌词”**
该曲目在 QQ 音乐和 LRCLIB 都没有合格的时间轴歌词，或同名歌曲时长差超过 8 秒。可换 `--source` 单独排查，或手工补歌词。

**报告里 `ambiguous` 是什么意思**
同来源、同文字形态、同时长差评分下存在两份不同歌词，工具不会替你选，需要人工确认。

**QQ 接口会不会失效**
会。它用的是网页内部接口，没有稳定性承诺；上游一变动就会失败（此时自动退回 LRCLIB）。这属于预期行为，不是安装问题。

**EXE 双击没反应 / 报找不到临时目录**
确认 exe 放在可写目录，且没有被安全软件拦截。

## 目录结构

```
prepare_lrc.py            命令行与抓取/匹配/写入核心
lrc_gui.py                Tkinter 图形界面
version.py                版本号唯一来源
update_check.py           GitHub Release 更新检查（只读公开接口）
prepare_lrc.cmd / .ps1    命令行入口与环境安装
lrc_gui.cmd               图形界面入口
build_lrc_exe.cmd         用 PyInstaller 打单文件 EXE
requirements-lyrics.txt   Mutagen / OpenCC 版本约束
```

## 开发

```powershell
# 打包（需要先跑过一次 prepare_lrc.cmd 以建立 .lrc-env）
.\build_lrc_exe.cmd
# 产物：build\output\Pebby-Lyrics.exe

# 单元测试（不联网）
python -m unittest discover -s tests -v
```

打包用 PyInstaller 6.16.0，`--collect-data opencc` 负责把 OpenCC 的配置和词典塞进单文件；漏掉这一步，简繁转换会在别的机器上运行时崩溃。

发布流程：

1. 改 `version.py` 里的 `__version__`
2. 提交并打 tag：`git tag v1.0.1 && git push origin v1.0.1`
3. GitHub Actions 自动构建并把 `Pebby-Lyrics.exe` 挂到 Release
4. 已安装的旧版本下次启动就会看到更新提示

## 许可与免责

[MIT](LICENSE) © 2026 零壹实验室

歌词文本版权归原权利人。本工具只读取公开可访问的歌词文本，供个人为自己已拥有的音乐整理播放列表使用；不下载音频、不绕过付费或登录限制、不提供任何资源的破解。
