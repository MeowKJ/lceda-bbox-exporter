from __future__ import annotations

import csv
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = ROOT / "tools" / "elibz2_bbox_tool.py"
SPEC = importlib.util.spec_from_file_location("elibz2_bbox_tool", TOOL_PATH)
assert SPEC and SPEC.loader
tool = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tool
SPEC.loader.exec_module(tool)


def pair(header: dict, data: object) -> str:
    return json.dumps(header, separators=(",", ":")) + "||" + json.dumps(data, separators=(",", ":")) + "|\n"


def document(
    uuid: str = "fp1",
    title: str = "Fixture",
    records: list[tuple[dict, object]] | None = None,
    version: str = "3.2.175",
) -> str:
    value = pair({"type": "DOCHEAD"}, {"docType": "FOOTPRINT", "uuid": uuid, "editVersion": version})
    value += pair({"type": "META", "id": "META", "ticket": 1}, {"title": title})
    for header, data in records or []:
        value += pair(header, data)
    return value


def write_archive(path: Path, elibus: dict[str, str], metadata: dict | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in elibus.items():
            archive.writestr(name, content)
        if metadata is not None:
            archive.writestr("device2.json", json.dumps(metadata))


def poly_record(record_id: str = "p1", path=None, width: float = 2) -> tuple[dict, dict]:
    return (
        {"type": "POLY", "id": record_id, "ticket": 2, "client": "a"},
        {"layerId": 3, "width": width, "path": path or [0, 0, "L", 10, 10]},
    )


class GeometryTests(unittest.TestCase):
    def assertBBox(self, actual, expected, places=6):
        for left, right in zip((actual.min_x, actual.min_y, actual.max_x, actual.max_y), expected):
            self.assertAlmostEqual(left, right, places=places)

    def test_line_width(self):
        record = tool.Record("LINE", "l", 1, "", {"startX": 0, "startY": 2, "endX": 10, "endY": 4, "width": 2})
        self.assertBBox(tool.primitive_bbox(record), (-1, 1, 11, 5))

    def test_arc_extrema(self):
        box = tool.path_bbox([1, 0, "ARC", 180, -1, 0])
        self.assertBBox(box, (-1, 0, 1, 1))

    def test_clockwise_arc_extrema(self):
        box = tool.path_bbox([1, 0, "ARC", -180, -1, 0])
        self.assertBBox(box, (-1, -1, 1, 0))

    def test_cubic_bezier_extrema(self):
        box = tool.path_bbox([0, 0, "C", 0, 10, 10, 10, 10, 0])
        self.assertBBox(box, (0, 0, 10, 7.5))

    def test_rotated_r_path_uses_top_left(self):
        box = tool.path_bbox(["R", 0, 0, 10, 4, 90])
        self.assertBBox(box, (3, -3, 7, 7))

    def test_circle(self):
        self.assertBBox(tool.path_bbox(["CIRCLE", 2, 3, 4]), (-2, -1, 6, 7))

    def test_rotated_rect_pad(self):
        record = tool.Record("PAD", "p", 1, "", {
            "layerId": 1, "centerX": 0, "centerY": 0, "padAngle": 90,
            "defaultPad": {"padType": "RECT", "width": 10, "height": 4}, "specialPad": [],
        })
        self.assertBBox(tool.primitive_bbox(record), (-2, -5, 2, 5))

    def test_relative_angle_only_rotates_multilayer_hole(self):
        record = tool.Record("PAD", "p", 1, "", {
            "layerId": 12, "centerX": 0, "centerY": 0, "padAngle": 0,
            "defaultPad": {"padType": "RECT", "width": 10, "height": 4}, "specialPad": [],
            "hole": ["RECT", 2, 20], "relativeAngle": 90, "padOffsetX": 5, "padOffsetY": 0,
        })
        self.assertBBox(tool.primitive_bbox(record), (-5, -2, 15, 2))

    def test_single_layer_hole_does_not_expand_pad(self):
        record = tool.Record("PAD", "p", 1, "", {
            "layerId": 1, "centerX": 0, "centerY": 0, "padAngle": 0,
            "defaultPad": {"padType": "RECT", "width": 10, "height": 4}, "specialPad": [],
            "hole": ["RECT", 100, 100], "relativeAngle": 45,
        })
        self.assertBBox(tool.primitive_bbox(record), (-5, -2, 5, 2))

    def test_special_pad_is_merged(self):
        record = tool.Record("PAD", "p", 1, "", {
            "layerId": 12, "centerX": 0, "centerY": 0, "padAngle": 0,
            "defaultPad": {"padType": "ROUND", "width": 4, "height": 4},
            "specialPad": [[1, 2, {"padType": "RECT", "width": 10, "height": 2}]],
        })
        self.assertBBox(tool.primitive_bbox(record), (-5, -2, 5, 2))

    def test_unknown_path_fails_closed(self):
        with self.assertRaisesRegex(tool.ToolError, "不支持的路径命令"):
            tool.path_bbox([0, 0, "Q", 1, 2])

    def test_pqfp_rounding_baseline(self):
        box = tool.BBox(-482.994, -384.567, 482.994, 364.884)
        self.assertEqual(tool._rounded_dimensions(box), (24.5361, 19.0361))


class LogAndArchiveTests(unittest.TestCase):
    def test_highest_ticket_wins_and_empty_data_deletes(self):
        source = document(records=[
            ({"type": "POLY", "id": "p", "ticket": 2, "client": "z"}, {"layerId": 3, "width": 2, "path": [0, 0, "L", 2, 2]}),
            ({"type": "POLY", "id": "p", "ticket": 3, "client": "z"}, {"layerId": 3, "width": 2, "path": [0, 0, "L", 4, 4]}),
            ({"type": "POLY", "id": "p", "ticket": 4, "client": "z"}, ""),
        ])
        docs = tool.parse_elibu(source)
        self.assertIsNone(docs[0].records[("POLY", "p")].data)

    def test_equal_ticket_smaller_client_wins(self):
        source = document(records=[
            ({"type": "POLY", "id": "p", "ticket": 2, "client": "z"}, {"value": "z"}),
            ({"type": "POLY", "id": "p", "ticket": 2, "client": "a"}, {"value": "a"}),
        ])
        self.assertEqual(tool.parse_elibu(source)[0].records[("POLY", "p")].data["value"], "a")

    def test_delete_doc_removes_document(self):
        source = document(records=[({"type": "DELETE_DOC", "ticket": 3}, {"deleted": True})])
        self.assertEqual(tool.parse_elibu(source), [])

    def test_multiple_dochead_segments_merge_by_uuid(self):
        source = document(records=[]) + pair({"type": "DOCHEAD"}, {"docType": "FOOTPRINT", "uuid": "fp1", "editVersion": "3.2.175"}) + pair(*poly_record())
        docs = tool.parse_elibu(source)
        self.assertEqual(len(docs), 1)
        self.assertIn(("POLY", "p1"), docs[0].records)

    def test_multiple_elibu_and_multiple_footprints(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "multi.elibz2"
            write_archive(archive, {
                "a.elibu": document("a", "A", [poly_record("a")]),
                "b.elibu": document("b", "B", [poly_record("b")]),
            })
            rows, audits = tool.process_archive(archive)
            self.assertEqual([row.footprint for row in rows], ["A", "B"])
            self.assertTrue(all(audit.status == "SUCCESS" for audit in audits))

    def test_no_footprint(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "none.elibz2"
            write_archive(archive, {"a.elibu": pair({"type": "DOCHEAD"}, {"docType": "SYMBOL", "uuid": "s"})})
            _, audits = tool.process_archive(archive)
            self.assertEqual(audits[0].error_code, "NO_FOOTPRINT")

    def test_corrupt_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "bad.elibz2"
            archive.write_bytes(b"not zip")
            _, audits = tool.process_archive(archive)
            self.assertEqual(audits[0].error_code, "INVALID_ZIP")

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "unsafe.elibz2"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../bad.elibu", document(records=[poly_record()]))
            _, audits = tool.process_archive(archive)
            self.assertEqual(audits[0].error_code, "UNSAFE_ZIP")

    def test_unknown_primitive_fails_only_that_footprint(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "partial.elibz2"
            good = document("good", "Good", [poly_record()])
            bad = document("bad", "Bad", [({"type": "ALIEN", "id": "x", "ticket": 2}, {"x": 1})])
            write_archive(archive, {"both.elibu": good + bad})
            rows, audits = tool.process_archive(archive)
            self.assertEqual(len(rows), 1)
            self.assertEqual({audit.status for audit in audits}, {"SUCCESS", "FAILED"})

    def test_ignored_component_layers_do_not_expand_bbox(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "layer.elibz2"
            records = [
                ({"type": "LAYER", "id": '["LAYER",48]', "ticket": 2}, {"layerType": "COMPONENT_SHAPE"}),
                poly_record("normal", [0, 0, "L", 10, 10], 0),
                ({"type": "POLY", "id": "aux", "ticket": 3}, {"layerId": 48, "width": 0, "path": [-100, -100, "L", 100, 100]}),
            ]
            write_archive(archive, {"a.elibu": document(records=records)})
            rows, _ = tool.process_archive(archive)
            self.assertEqual(rows[0].bbox, tool.BBox(0, 0, 10, 10))


class MetadataAndOutputTests(unittest.TestCase):
    def test_gui_result_puts_key_dimensions_first_and_status_last(self):
        audit = tool.AuditRow(
            Path("sample.elibz2"),
            footprint="C0402",
            status="SUCCESS",
            x_mm=1.9,
            y_mm=1.1517,
            z_mm=None,
        )
        self.assertEqual(
            tool.gui_result_values(audit),
            ("C0402", "1.9", "1.1517", "", "sample.elibz2", "", "成功"),
        )

    def test_height_is_read_only_from_linked_3d_model_title(self):
        metadata = [{"devices": {"d": {"attributes": {"Footprint": "fp", "3D Model Title": "PKG_L1-W2-H3.4-X"}}}, "footprints": {"fp": {"display_title": "Named"}}}]
        self.assertEqual(tool._metadata_for_uuid(metadata, "fp"), ("Named", 3.4, ""))

    def test_conflicting_heights_are_blank(self):
        metadata = [{"devices": {
            "a": {"attributes": {"Footprint": "fp", "3D Model Title": "X-H1.0"}},
            "b": {"attributes": {"Footprint": "fp", "3D Model Title": "X-H2.0"}},
        }}]
        _, height, warning = tool._metadata_for_uuid(metadata, "fp")
        self.assertIsNone(height)
        self.assertIn("冲突", warning)

    def test_recursive_discovery_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            child = root / "sub" / "a.elibz2"
            child.parent.mkdir()
            child.touch()
            self.assertEqual(tool.discover_inputs([root, child]), [child])

    def test_csv_bom_header_quoting_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.csv"
            row = tool.ExportRow(Path("a.elibz2"), "fp", 'A,"B"', "3.2.175", ("POLY",), tool.BBox(0, 0, 1, 1), 0.0254, 0.0254, None)
            result = tool.BatchResult((row,), (tool.AuditRow(Path("a.elibz2"), "fp", 'A,"B"', status="SUCCESS"),))
            tool.write_results(result, output)
            self.assertTrue(output.read_bytes().startswith(b"\xef\xbb\xbf"))
            with output.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.reader(stream))
            self.assertEqual(rows[0], tool.CSV_HEADER)
            self.assertEqual(rows[1][1], 'A,"B"')
            audit_path = tool.audit_path_for(output)
            self.assertTrue(audit_path.exists())
            with audit_path.open(encoding="utf-8-sig", newline="") as stream:
                audit_rows = list(csv.reader(stream))
            self.assertEqual(audit_rows[0][:4], ["Footprint", "X-Length (mm)", "Y-Width (mm)", "Z-Height (mm)"])
            self.assertFalse(any("Min " in column or "Max " in column for column in audit_rows[0]))
            self.assertEqual(audit_rows[0][-1], "Status")
            self.assertEqual(audit_rows[1][-1], "SUCCESS")

    def test_output_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.csv"
            output.touch()
            with self.assertRaises(tool.ToolError) as context:
                tool.write_results(tool.BatchResult((), ()), output)
            self.assertEqual(context.exception.code, "OUTPUT_EXISTS")

    def test_cli_partial_and_all_failure_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "good.elibz2"
            bad = root / "bad.elibz2"
            write_archive(good, {"a.elibu": document(records=[poly_record()])})
            bad.write_bytes(b"bad")
            partial = subprocess.run([sys.executable, str(TOOL_PATH), str(good), str(bad), "-o", str(root / "partial.csv")], check=False, capture_output=True)
            failed = subprocess.run([sys.executable, str(TOOL_PATH), str(bad), "-o", str(root / "failed.csv")], check=False, capture_output=True)
            self.assertEqual(partial.returncode, 1)
            self.assertEqual(failed.returncode, 2)

    def test_headless_platform_smoke(self):
        for platform_name in ("win32", "darwin", "linux"):
            process = subprocess.run(
                [sys.executable, str(TOOL_PATH), "--smoke", "--simulate-platform", platform_name],
                check=False, capture_output=True, text=True,
            )
            self.assertEqual(process.returncode, 0)
            self.assertIn(f"/ {platform_name}", process.stdout)


if __name__ == "__main__":
    unittest.main()
