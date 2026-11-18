---
name: image2vector
display_name: 图片转矢量图
display_name_en: Image to Vector
description: 把用户指定的 PNG、JPEG、TIFF、BMP 或 WebP 位图转为可编辑的 Adobe Illustrator .ai 文件，同时输出 SVG 与 PNG 预览。适用于图片转 AI、位图描摹和图表矢量化。
description_zh: 用本机 VectorCraft 把位图转换成 Illustrator 矢量路径。
description_en: Trace a raster image into editable Illustrator paths using local VectorCraft.
version: "1.2.0"
author: image2vector contributors
examples_zh:
  - 把这张流程图转成 Illustrator 可编辑的 AI 文件。
---

# image2vector

使用安装目录中 `python-path.txt` 指向的 Python 解释器，调用同目录下的 `scripts/image2vector.py`。PowerShell 示例中的 `$skillRoot` 和 `$imagePath` 分别是技能目录（通常为 `~/.workbuddy/skills/image2vector`）和用户选定的源图。

```powershell
$image2vectorPython = (Get-Content -LiteralPath (Join-Path $skillRoot 'python-path.txt') -Raw -Encoding UTF8).Trim()
& $image2vectorPython -I -B (Join-Path $skillRoot 'scripts/image2vector.py') -- $imagePath
```

在 Bash 中先从同一文件读取解释器路径，再运行 `"$image2vectorPython" -I -B "$skillRoot/scripts/image2vector.py" -- "$imagePath"`。文件路径作为独立参数传递。VectorCraft 的可执行文件由受信任的本机配置或安装位置确定。

默认使用与源图同名的 `.ai` 文件。指定目标时把 `-o` 与目标路径放在 `--` 前。图表可用 `--mode flat`，照片可用 `--mode photo --max-colors 24`。遇到重名时可以指定新的目标。只有 JSON 的 `ok` 为 `true` 时才报告转换成功。返回 `ai`、`svg`、`preview` 路径，并使用 PNG 预览检查效果。文字以轮廓形式输出。
