# BBox 尺寸导出器

嘉立创 EDA 专业版 V3 扩展，用官方 `getPrimitivesBBox` API 导出原理图、PCB 与封装图元的灰色包围框（BBox）尺寸 CSV。

## 功能

- 导出当前选中图元的 CSV BBox；
- 导出当前文档中全部器件的 CSV BBox；
- 原理图中支持导出选中图元的官方 BBox；
- 将官方 API 返回的 mil 坐标转换为毫米，保留四位小数；
- 输出与参考 PDF 标注对应的精简 BOM 列：`Designator`、`Footprint`、`X-Length of Bottom Edge on Board (Spacing Line)`、`Y-Width`、`Z-Height`；
- 不生成 JSON，也不导出内部 ID、旋转角、原始 Min/Max 坐标等冗余条目。
- 支持在底部器件列表、原理图底部符号列表中右键导出。
- 在封装编辑器中支持“导出当前封装库官方 BBox CSV”：将当前封装全部官方图元一次性传给 `getPrimitivesBBox()`，不自行计算边界。

## 构建

需要 Node.js 20.17 或更高版本。

```bash
npm install
npm run check
```

扩展包生成于 `build/dist/lceda-bbox-exporter_v0.7.0.eext`。

在嘉立创 EDA 专业版中进入“高级 → 扩展管理器 → 导入”，选择生成的 `.eext` 文件。

## 离线读取 `.elibz2` 中已存储的 BBox

`tools/extract_elibz2_stored_bbox.py` 是一个零依赖 Python 3 脚本，用于读取 `.elibz2` 文件内**已经保存**的 `PART.BBOX`，并导出同样的五列 CSV：

```bash
python3 tools/extract_elibz2_stored_bbox.py /path/to/component.elibz2 -o /path/to/stored-bbox.csv
```

它不会从焊盘、丝印或其他封装图元手算外框。若文件没有持久化 `FOOTPRINT.BBOX`，脚本会明确保留该限制；要获得与嘉立创 EDA 灰色包围框一致的封装 BBox，请在 EDA 内使用本扩展导出。

注意：存储在库文件里的 `PART.BBOX` 也不保证等于一个已放置器件的 EDA 运行时官方 BBox。当前样例中，脚本读取到 `53.34 × 165.1 mm`，而 EDA 对已放置的 U1 通过官方 API 导出为 `53.594 × 165.354 mm`；两者应按用途分别使用，不可互相替代。

## 使用

1. 打开 PCB、封装或原理图画布；
2. 选中一个或多个图元；
3. 进入“高级(A) → BBox 尺寸导出器”，选择选中图元或全部器件的 CSV 导出命令；
4. 保存 CSV 文件。

要从封装库导出：先把 `.elibz2` 导入嘉立创 EDA 并打开目标封装，选择“高级(A) → BBox 尺寸导出器 → 导出当前封装库官方 BBox CSV”。导出取该封装编辑器返回的官方 BBox，不取 PCB 已放置实例，也不手动计算四条边。

“导出全部器件尺寸”会忽略普通线条、焊盘等非器件图元，只导出器件级 BBox。

嘉立创 EDA 目前仅对底部“器件列表、符号列表、封装列表”等右键菜单开放扩展 API；PCB 与原理图画布本身的右键菜单不能由扩展添加项目。

## 数据约定

- 单位：`mm`；
- 换算：`1 mil = 0.0254 mm`；
- 原理图官方 BBox 单位为 `0.01 inch`，即 `0.254 mm`；
- `X-Length... = maxX - minX`；
- `Y-Width = maxY - minY`；它们严格来自编辑器选中时显示的二维灰色 BBox。
- 二维 BBox 不含 Z 轴。仅当关联 3D 模型名以常见 `L…-W…-H…` 形式明确给出 H 值时，`Z-Height` 才会填写；否则留空，不会伪造高度。

## 开发依据

- 嘉立创 EDA 专业版扩展 API；
- `eda.pcb_SelectControl.getAllSelectedPrimitives()`；
- `eda.pcb_Primitive.getPrimitivesBBox()`；
- `eda.pcb_PrimitiveComponent.getAll()`；
- `eda.sys_FileSystem.saveFile()`。

项目结构和构建工具基于嘉立创 EDA 扩展 SDK，业务代码与扩展 UUID 独立。
