# Changelog

## 0.7.0

- 接入与去耦喵相同的原理图画布右键菜单 Hook：右键单个已选器件时显示「BBox 尺寸导出器 → 导出所选原理图图元 BBox CSV」。
- Hook 仅负责加入入口；实际 BBox 仍通过公开的 `eda.sch_Primitive.getPrimitivesBBox()` 读取。
- 增加特征检测、重复安装防护、定时重试与 `deactivate()` 恢复逻辑；内部接口不可用时保留顶部与列表右键入口。
- 新增零依赖 Python 脚本，可读取 `.elibz2` 内显式保存的 `PART.BBOX`；没有保存的封装 BBox 一律不计算。

## 0.6.0

- 修复封装编辑器菜单遗漏“导出当前封装官方 BBox CSV”的问题；该命令直接读取当前封装的官方 BBox，不需要手工计算。
- 新增原理图选中图元导出，并在底部符号列表注册右键入口。
- 原理图 BBox 的官方单位为 0.01 inch，导出时统一换算为 mm；PCB/封装仍使用官方 mil BBox。

## 0.5.0

- 在封装编辑器新增“导出当前封装库官方 BBox CSV”，以当前封装的官方 `getPrimitivesBBox()` 返回值为 X/Y 尺寸来源。

## 0.4.0

- 仅保留 CSV 导出，移除 JSON 命令与 JSON 代码。
- CSV 收敛为参考格式的 `Designator`、`Footprint`、X/Y/Z 五列，删除内部 ID、旋转、模型名与原始 BBox 坐标等冗余列。

## 0.3.0

- 将 CSV 和 JSON 拆分为独立菜单命令，避免嘉立创 EDA 桌面端连续 `saveFile` 调用丢失第二个保存窗口。

## 0.2.1

- 桌面端启动时显式注册顶部菜单，修复已安装但菜单未显示的问题；
- 支持封装编辑器中的选中图元导出。

## 0.2.0

- 更名为「BBox 尺寸导出器」；
- CSV 增加 BOM 可直接使用的 X-Length、Y-Width、Z-Height 列；
- 从关联 3D 模型名称中的 `H` 参数读取可验证的 Z 高度，无法读取时明确留空。

## 0.1.0

- 初始版本；
- 支持选中图元和全部器件 BBox 导出；
- 支持 CSV、JSON 和 mil 到 mm 换算。
