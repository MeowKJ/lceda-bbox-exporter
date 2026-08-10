# BBox 导出喵

嘉立创 EDA 专业版 V3 扩展，用官方 `getPrimitivesBBox` API 导出 PCB/封装图元的包围盒。

## 功能

- 导出当前选中图元的独立 BBox；
- 导出当前文档中全部器件的独立 BBox；
- 同时生成 UTF-8 CSV 与 JSON；
- 将官方 API 返回的 mil 坐标转换为毫米，保留四位小数；
- 输出坐标、宽高、图元类型以及可获得的器件/封装信息。

## 构建

需要 Node.js 20.17 或更高版本。

```bash
npm install
npm run check
```

扩展包生成于 `build/dist/lceda-bbox-exporter_v0.1.0.eext`。

在嘉立创 EDA 专业版中进入“高级 → 扩展管理器 → 导入”，选择生成的 `.eext` 文件。

## 使用

1. 打开 PCB 或封装画布；
2. 选中一个或多个图元；
3. 进入“BBox 导出喵 → 导出选中图元 BBox”；
4. 分别保存 CSV 和 JSON 文件。

“导出全部器件 BBox”会忽略普通线条、焊盘等非器件图元，只导出器件级 BBox。

## 数据约定

- 坐标系：笛卡尔坐标系，右上为正；
- 单位：`mm`；
- 换算：`1 mil = 0.0254 mm`；
- `width = maxX - minX`；
- `height = maxY - minY`。

## 开发依据

- 嘉立创 EDA 专业版扩展 API；
- `eda.pcb_SelectControl.getAllSelectedPrimitives()`；
- `eda.pcb_Primitive.getPrimitivesBBox()`；
- `eda.pcb_PrimitiveComponent.getAll()`；
- `eda.sys_FileSystem.saveFile()`。

项目结构参考 [`MeowKJ/lceda-decoupling-meow`](https://github.com/MeowKJ/lceda-decoupling-meow)，业务代码与扩展 UUID 独立。
