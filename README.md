# BBox 尺寸导出器

嘉立创 EDA 专业版 V3 扩展，用官方 `getPrimitivesBBox` API 导出 PCB/封装图元的灰色包围框（BBox）实际尺寸。

## 功能

- 分别导出当前选中图元的 CSV 或 JSON BBox；
- 分别导出当前文档中全部器件的 CSV 或 JSON BBox；
- 同时生成 UTF-8 CSV 与 JSON；
- 将官方 API 返回的 mil 坐标转换为毫米，保留四位小数；
- 输出坐标、宽高、图元类型以及可获得的器件/封装/3D 模型信息；
- CSV 包含与截图 BOM 对应的 `X-Length of Bottom Edge on Board (Spacing Line)`、`Y-Width`、`Z-Height`。

## 构建

需要 Node.js 20.17 或更高版本。

```bash
npm install
npm run check
```

扩展包生成于 `build/dist/lceda-bbox-exporter_v0.1.0.eext`。

在嘉立创 EDA 专业版中进入“高级 → 扩展管理器 → 导入”，选择生成的 `.eext` 文件。

## 使用

1. 打开 PCB 或封装画布；原理图符号的 BBox 不是封装外形尺寸，不能用于本导出；
2. 选中一个或多个图元；
3. 进入“BBox 尺寸导出器”，选择 CSV 或 JSON 导出命令；
4. 分别保存 CSV 和 JSON 文件。

“导出全部器件尺寸”会忽略普通线条、焊盘等非器件图元，只导出器件级 BBox。CSV 和 JSON 各自使用一个独立的系统保存窗口：这是为了兼容嘉立创 EDA 桌面端，避免连续保存窗口导致第二个导出丢失。

## 数据约定

- 坐标系：笛卡尔坐标系，右上为正；
- 单位：`mm`；
- 换算：`1 mil = 0.0254 mm`；
- `width = maxX - minX`；
- `height = maxY - minY`。
- `X-Length... = width`、`Y-Width = height`；它们严格来自编辑器选中时显示的二维灰色 BBox。
- 二维 BBox 不含 Z 轴。仅当关联 3D 模型名以常见 `L…-W…-H…` 形式明确给出 H 值时，`Z-Height` 才会填写；否则留空，并标记 `Z-Height Source=unavailable`，不会伪造高度。

## 开发依据

- 嘉立创 EDA 专业版扩展 API；
- `eda.pcb_SelectControl.getAllSelectedPrimitives()`；
- `eda.pcb_Primitive.getPrimitivesBBox()`；
- `eda.pcb_PrimitiveComponent.getAll()`；
- `eda.sys_FileSystem.saveFile()`。

项目结构和构建工具基于嘉立创 EDA 扩展 SDK，业务代码与扩展 UUID 独立。
