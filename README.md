# BBox 尺寸导出器

本仓库包含两个用途明确分开的工具：

1. `tools/elibz2_bbox_tool.py`：最终交付的独立 `.elibz2` 批量 BBox 导出工具；
2. 嘉立创 EDA 专业版扩展：日常从画布导出官方 BBox，并在开发期生成真值 CSV。

独立工具运行时不安装、不启动、不登录嘉立创 EDA，也不依赖扩展。

## 独立 `.elibz2` 批量工具

### 环境

- Python 3.10 或更高版本；
- 不需要安装 pip 包；
- Windows、macOS 和 Linux 均可运行；
- 图形界面使用 Python 自带 Tkinter。部分 Linux 发行版需要另装 `python3-tk`，没有 Tk 时 CLI 仍可使用。

### 图形界面

直接运行且不带参数：

```bash
python3 tools/elibz2_bbox_tool.py
```

界面支持添加多个文件、递归添加文件夹、删除、清空和自动去重。处理在线程中执行，可取消。结果表按“封装、X、Y、Z、输入文件、错误/说明、状态”排列，关键尺寸在前，状态固定在最后。

### 命令行

```bash
python3 tools/elibz2_bbox_tool.py <文件或文件夹...> -o result.csv
```

文件夹会递归扫描 `.elibz2`。已有主 CSV 或审计 CSV 时默认拒绝覆盖，明确使用 `--force` 才覆盖：

```bash
python3 tools/elibz2_bbox_tool.py library.elibz2 ./libraries -o result.csv --force
```

退出码：

- `0`：全部封装成功；
- `1`：部分成功、部分失败；
- `2`：全部失败、无输入或发生致命错误。

### 输出

主文件是 UTF-8 BOM CSV，固定五列：

- `Designator`：独立封装库没有位号，统一留空；
- `Footprint`；
- `X-Length of Bottom Edge on Board (Spacing Line)`；
- `Y-Width`；
- `Z-Height`。

同时生成 `<主文件名>-audit.csv`，按“封装、X/Y/Z、UUID、格式/图元、输入路径、错误信息、状态”排列。不生成 JSON，也不输出 Min/Max 原始坐标。

`Z-Height` 只在关联设备的 `3D Model Title` 明确包含 `H3.4` 一类参数时填写。缺少高度或存在多个冲突高度时留空。

### 计算口径与失败策略

- 真值目标是封装作为 PCB 器件以 0° 放置时的灰色选择框；
- V3 PCB/FOOTPRINT 坐标按 `1 mil = 0.0254 mm`；
- 读取 ZIP 内全部 `.elibu`，按 `DOCHEAD`/UUID 聚合 FOOTPRINT 文档；
- 同 `type + id` 保留最高 `ticket`，同票冲突按 client 稳定归并，并处理空数据删除与 `DELETE_DOC`；
- 支持焊盘/特殊焊盘/孔、直线、圆弧、旋转矩形、多边形、填充路径、三阶贝塞尔、描边、圆以及有明确路径或宽高的图片和文字；
- `relativeAngle` 只旋转孔，不旋转焊盘本体；封装整体按 0° 合并；
- 未知格式版本、未知图元或缺少必要几何数据时，该封装在审计表中标记失败，主表不输出估算行，其他封装继续处理；
- 不读取 `PART.BBOX` 作为封装尺寸。

当前声明支持嘉立创 EDA 专业版 3.2 系列导出的 V3 键值 `.elibz2`。其他格式版本必须先增加官方真值回归，不能按近似模式放行。

## 嘉立创 EDA 官方真值扩展

扩展使用官方 `getPrimitivesBBox()` API，可在 PCB、封装和原理图环境导出五列 CSV。PCB 器件统一按关联封装的官方 0° BBox 输出，不受板上随机旋转影响。

封装编辑器中额外提供“开发：导出当前封装官方真值 CSV”，包含：

- 封装名称和 UUID；
- 原始 `minX/minY/maxX/maxY`（mil）；
- X/Y（mm）；
- EDA 版本和扩展版本。

该命令只用于开发期比较离线解析结果，不是离线工具依赖，也不改变用户五列导出格式。

构建需要 Node.js 20.17 或更高版本：

```bash
npm install
npm run check
```

扩展包生成于 `build/dist/lceda-bbox-exporter_v1.0.3.eext`。在嘉立创 EDA 专业版中进入“高级 → 扩展管理器 → 导入”安装。

## 旧 `PART.BBOX` 提取脚本

`tools/extract_elibz2_stored_bbox.py` 保留用于兼容旧工作流。它只提取库中已存储的原理图符号 `PART.BBOX`，不是封装几何计算器。

例如 PQFP 样例的 `PART.BBOX` 是 `53.34 × 165.1 mm`，而 PCB 0° 封装官方真值是 `24.5361 × 19.0361 mm`。两者对象和单位口径不同，不能互相替代。需要封装尺寸时应使用新的 `elibz2_bbox_tool.py`。

## 验证与开发依据

- 嘉立创公开的 EasyEDA Pro V3 文件格式；
- 嘉立创 EDA 专业版扩展 API；
- `eda.pcb_Primitive.getPrimitivesBBox()` 黑盒真值对照；
- PQFP-128 已知 PCB 0° 基线：`24.5361 × 19.0361 mm`；
- `npm run check` 同时执行 ESLint、TypeScript、Node 测试、Python 测试、构建和扩展包校验。

项目不复制或分发嘉立创客户端代码与资源。
