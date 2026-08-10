# 嘉立创 EDA GUI 验证记录

- 日期：2026-08-11
- 嘉立创 EDA 专业版：3.2.149.88089769
- 扩展：BBox 尺寸导出器 v0.3.0
- 测试文档：`傻瓜式检测器 / 测试 / PCB3`
- 测试器件：U1，封装 `PQFP-128_L14.0-W20.0-P0.50-BL`

## 通过项

- 从“高级(A) → BBox 尺寸导出器”可见四个单格式命令：选中/全部器件 × CSV/JSON。
- `导出全部器件尺寸（CSV）` 成功生成 `component-bbox-v030.csv`。
- `导出全部器件尺寸（JSON）` 成功生成 `component-bbox-v030.json`，未再发生连续原生保存窗口导致第二份文件丢失的问题。
- `导出选中图元尺寸（CSV）` 成功生成 `selected-bbox-v030.csv`。
- 未选中任何图元时，`导出选中图元尺寸（CSV）` 显示：`请先在 PCB 或封装画布中选择至少一个图元。`
- CSV 与 JSON 的 U1 数值一致；`width = maxX - minX`，`height = maxY - minY`。

## 实测 U1 BBox

| 字段 | 值 |
| --- | ---: |
| X-Length / BBox Width | 24.5361 mm |
| Y-Width / BBox Height | 19.0361 mm |
| Z-Height | 3.4 mm |
| BBox Min X, Min Y | 0, -24.0361 mm |
| BBox Max X, Max Y | 24.5361, -5 mm |
| 3D 模型 | `PQFP-128_L20.0-W14.0-H3.4-LS23.2-P0.50` |

X/Y 为官方二维灰色 BBox 的焊盘外缘范围；Z 从 3D 模型名的显式 `H3.4` 参数解析。

## 证据

- GUI 截图：`pcb3-u1-selected.jpeg`、`pcb3-empty-selection-error.jpeg`。
- 实际导出：`/Users/kongjing/Downloads/component-bbox-v030.csv`、`/Users/kongjing/Downloads/component-bbox-v030.json`、`/Users/kongjing/Downloads/selected-bbox-v030.csv`。

## 未通过/待补充

- PCB1 的多选图元尝试受大面积铺铜和选择过滤器交互影响，尚未获得稳定的多选导出证据；未将其标记为通过。
