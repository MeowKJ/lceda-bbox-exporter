#!/usr/bin/env python3
"""Export only BBox values explicitly stored in an EasyEDA .elibz2 archive.

This tool intentionally does *not* derive a footprint outline from pads, silk,
or other primitives.  A footprint whose archive does not contain a BBOX field
must be opened in EasyEDA and exported by the extension, which calls the
official runtime getPrimitivesBBox() API.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMATIC_BBOX_UNIT_TO_MM = 0.254  # .elibz2 PART.BBOX: 0.01 inch
CSV_HEADER = [
    'Designator',
    'Footprint',
    'X-Length of Bottom Edge on Board (Spacing Line)',
    'Y-Width',
    'Z-Height',
]


@dataclass(frozen=True)
class StoredBBox:
    record_type: str
    record_id: str
    title: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def x_length_mm(self) -> float:
        return round((self.max_x - self.min_x) * SCHEMATIC_BBOX_UNIT_TO_MM, 4)

    @property
    def y_width_mm(self) -> float:
        return round((self.max_y - self.min_y) * SCHEMATIC_BBOX_UNIT_TO_MM, 4)


def read_json_member(archive: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        return json.loads(archive.read(name).decode('utf-8'))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f'无法读取 {name}。') from error


def read_elibu_objects(archive: zipfile.ZipFile) -> list[dict[str, Any]]:
    members = [name for name in archive.namelist() if name.lower().endswith('.elibu')]
    if len(members) != 1:
        raise ValueError(f'预期压缩包内恰有一个 .elibu 文件，实际为 {len(members)} 个。')
    try:
        content = archive.read(members[0]).decode('utf-8')
    except UnicodeDecodeError as error:
        raise ValueError(f'无法以 UTF-8 解码 {members[0]}。') from error

    objects: list[dict[str, Any]] = []
    for fragment in content.split('|'):
        fragment = fragment.strip()
        if not fragment.startswith('{'):
            continue
        try:
            value = json.loads(fragment)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def find_stored_bboxes(objects: list[dict[str, Any]]) -> list[StoredBBox]:
    """Return BBox fields saved beside an .elibu record, never geometry-derived."""
    records: list[StoredBBox] = []
    current_record: dict[str, Any] | None = None
    for value in objects:
        if isinstance(value.get('type'), str) and isinstance(value.get('id'), str):
            current_record = value
            continue
        bbox = value.get('BBOX')
        if current_record is None or not isinstance(bbox, list) or len(bbox) != 4:
            continue
        if not all(isinstance(number, (int, float)) for number in bbox):
            continue
        records.append(StoredBBox(
            record_type=current_record['type'],
            record_id=current_record['id'],
            title=str(value.get('title', current_record['id'])),
            min_x=float(bbox[0]),
            min_y=float(bbox[1]),
            max_x=float(bbox[2]),
            max_y=float(bbox[3]),
        ))
    return records


def device_labels(document: dict[str, Any]) -> tuple[str, str]:
    devices = document.get('devices', {})
    device = next(iter(devices.values()), {}) if isinstance(devices, dict) else {}
    attributes = device.get('attributes', {}) if isinstance(device, dict) else {}
    designator = str(attributes.get('Designator', '')) if isinstance(attributes, dict) else ''
    footprint_id = attributes.get('Footprint') if isinstance(attributes, dict) else None
    footprints = document.get('footprints', {})
    footprint = footprints.get(footprint_id, {}) if isinstance(footprints, dict) else {}
    footprint_name = footprint.get('display_title') or footprint.get('title') or ''
    return designator, str(footprint_name)


def export_stored_part_bboxes(input_path: Path, output_path: Path) -> list[StoredBBox]:
    if not zipfile.is_zipfile(input_path):
        raise ValueError('输入文件不是有效的 .elibz2 ZIP 压缩包。')
    with zipfile.ZipFile(input_path) as archive:
        document = read_json_member(archive, 'device2.json')
        stored_bboxes = find_stored_bboxes(read_elibu_objects(archive))

    # PART.BBOX is explicitly persisted in the known .elibz2 format and uses
    # the schematic 0.01-inch unit.  Other records are deliberately omitted
    # until their persisted unit contract is known.
    part_bboxes = [bbox for bbox in stored_bboxes if bbox.record_type == 'PART']
    if not part_bboxes:
        raise ValueError('文件没有存储 PART.BBOX；不会从图元手工计算替代值。')

    designator, footprint_name = device_labels(document)
    with output_path.open('w', newline='', encoding='utf-8-sig') as output:
        writer = csv.writer(output)
        writer.writerow(CSV_HEADER)
        for bbox in part_bboxes:
            writer.writerow([designator, footprint_name, bbox.x_length_mm, bbox.y_width_mm, ''])
    return part_bboxes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='从 .elibz2 中导出已存储的 PART.BBOX；不计算封装 BBox。',
    )
    parser.add_argument('input', type=Path, nargs='?', help='输入 .elibz2 文件；省略时打开图形界面')
    parser.add_argument('-o', '--output', type=Path, help='输出 CSV 路径（默认与输入同目录）')
    parser.add_argument('--gui', action='store_true', help='打开图形界面')
    return parser.parse_args()


def run_gui() -> int:
    """A small standard-library GUI for double-click use on Windows/macOS."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox
    except ImportError:
        print('错误：当前 Python 没有 tkinter；请使用命令行方式运行。', file=sys.stderr)
        return 1

    root = tk.Tk()
    root.title('EasyEDA .elibz2 已存储 BBox 提取器')
    root.resizable(False, False)
    root.geometry('760x360')
    padding = {'padx': 10, 'pady': 6}
    input_var = tk.StringVar()
    output_var = tk.StringVar()
    result_var = tk.StringVar(value='选择 .elibz2 文件后导出；不会计算未保存的封装 BBox。')

    def choose_input() -> None:
        filename = filedialog.askopenfilename(
            title='选择 .elibz2 文件',
            filetypes=[('EasyEDA library', '*.elibz2'), ('All files', '*.*')],
        )
        if filename:
            input_var.set(filename)
            input_path = Path(filename)
            output_var.set(str(input_path.with_name(f'{input_path.stem}-stored-bbox.csv')))

    def choose_output() -> None:
        filename = filedialog.asksaveasfilename(
            title='保存 BBox CSV',
            defaultextension='.csv',
            filetypes=[('CSV', '*.csv')],
            initialfile=Path(output_var.get() or 'stored-bbox.csv').name,
        )
        if filename:
            output_var.set(filename)

    def export() -> None:
        input_path = Path(input_var.get())
        output_path = Path(output_var.get())
        if not input_var.get() or not input_path.is_file():
            messagebox.showerror('无法导出', '请选择有效的 .elibz2 文件。')
            return
        if not output_var.get():
            messagebox.showerror('无法导出', '请选择 CSV 保存位置。')
            return
        try:
            bboxes = export_stored_part_bboxes(input_path, output_path)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            messagebox.showerror('无法导出', str(error))
            return
        details = '\n'.join(
            f'{bbox.title}: raw [{bbox.min_x:g}, {bbox.min_y:g}, {bbox.max_x:g}, {bbox.max_y:g}] → '
            f'{bbox.x_length_mm:g} × {bbox.y_width_mm:g} mm'
            for bbox in bboxes
        )
        result_var.set(f'已导出 {len(bboxes)} 条记录：{output_path}')
        messagebox.showinfo('导出完成', f'{details}\n\n已写入：\n{output_path}')

    frame = tk.Frame(root, padx=12, pady=12)
    frame.pack(fill='both', expand=True)
    tk.Label(frame, text='从嘉立创 .elibz2 读取已存储的 PART.BBOX', anchor='w').pack(fill='x', pady=(0, 4))
    tk.Label(frame, text='不手算封装外框；缺少已存储 BBox 时会明确提示。', anchor='w').pack(fill='x', pady=(0, 12))
    tk.Button(frame, text='1. 选择 .elibz2 文件…', command=choose_input, height=2).pack(fill='x', pady=(0, 12))
    tk.Label(frame, text='输入文件', anchor='w').pack(fill='x')
    tk.Entry(frame, textvariable=input_var, relief='sunken', bd=1).pack(fill='x', pady=(2, 6))
    tk.Button(frame, text='重新选择文件…', command=choose_input).pack(anchor='e', pady=(0, 12))
    tk.Label(frame, text='输出 CSV', anchor='w').pack(fill='x')
    tk.Entry(frame, textvariable=output_var, relief='sunken', bd=1).pack(fill='x', pady=(2, 6))
    tk.Button(frame, text='选择保存位置…', command=choose_output).pack(anchor='e', pady=(0, 12))
    tk.Button(frame, text='2. 读取已存储 BBox 并导出 CSV', command=export, height=2).pack(fill='x', pady=(2, 10))
    tk.Label(frame, textvariable=result_var, wraplength=700, justify='left', anchor='w').pack(fill='x')
    root.mainloop()
    return 0


def main() -> int:
    args = parse_args()
    if args.gui or args.input is None:
        return run_gui()
    output = args.output or args.input.with_name(f'{args.input.stem}-stored-bbox.csv')
    try:
        bboxes = export_stored_part_bboxes(args.input, output)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f'错误：{error}', file=sys.stderr)
        return 1

    for bbox in bboxes:
        print(
            f'已读取 {bbox.record_type} {bbox.title}: '
            f'raw BBOX=[{bbox.min_x:g}, {bbox.min_y:g}, {bbox.max_x:g}, {bbox.max_y:g}], '
            f'X={bbox.x_length_mm:g} mm, Y={bbox.y_width_mm:g} mm',
        )
    print(f'已写入 CSV：{output}')
    print('说明：未发现或未导出的 FOOTPRINT.BBOX 不会被手工计算；请使用 EDA 插件导出官方封装 BBox。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
