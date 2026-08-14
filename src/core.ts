export const MIL_TO_MM = 0.0254;
/** 原理图 BBox 的官方单位是 0.01 inch，即 10 mil。 */
export const SCHEMATIC_BBOX_UNIT_TO_MM = 0.254;

export interface RawBBox {
	maxX: number;
	maxY: number;
	minX: number;
	minY: number;
}

export interface BBoxExportRow {
	designator: string;
	footprintName: string;
	xLength: number;
	yWidth: number;
	zHeight: number | '';
}

export interface PrimitiveMetadata {
	designator?: string;
	footprintName?: string;
	model3DName?: string;
}

export function milToMm(value: number): number {
	return Number((value * MIL_TO_MM).toFixed(4));
}

/**
 * 嘉立创常见的 3D 模型命名包含类似 `L14.0-W20.0-H3.2` 的尺寸。
 * BBox 本身只有二维数据，因此仅在模型名明确给出 H 值时才导出 Z 高度。
 */
export function modelNameToZHeightMm(modelName?: string): number | '' {
	if (!modelName)
		return '';

	const match = /(?:^|[_\s-])H\s*(\d+(?:\.\d+)?)(?:\s*mm)?(?:$|[_\s-])/i.exec(modelName);
	return match ? Number(Number.parseFloat(match[1]).toFixed(4)) : '';
}

export function makeBBoxRow(
	metadata: PrimitiveMetadata,
	bbox: RawBBox,
	unitToMm = MIL_TO_MM,
): BBoxExportRow {
	const toMm = (value: number) => Number((value * unitToMm).toFixed(4));
	const minX = toMm(bbox.minX);
	const minY = toMm(bbox.minY);
	const maxX = toMm(bbox.maxX);
	const maxY = toMm(bbox.maxY);
	const xLength = Number((maxX - minX).toFixed(4));
	const yWidth = Number((maxY - minY).toFixed(4));
	const zHeight = modelNameToZHeightMm(metadata.model3DName);
	return {
		designator: metadata.designator ?? '',
		footprintName: metadata.footprintName ?? '',
		xLength,
		yWidth,
		zHeight,
	};
}

export const CSV_COLUMNS: Array<keyof BBoxExportRow> = [
	'designator',
	'footprintName',
	'xLength',
	'yWidth',
	'zHeight',
];

export const CSV_HEADERS: Record<keyof BBoxExportRow, string> = {
	designator: 'Designator',
	footprintName: 'Footprint',
	xLength: 'X-Length of Bottom Edge on Board (Spacing Line)',
	yWidth: 'Y-Width',
	zHeight: 'Z-Height',
};

export function escapeCsv(value: unknown): string {
	const text = String(value ?? '');
	return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export interface TruthBBoxRow {
	bbox: RawBBox;
	edaVersion: string;
	extensionVersion: string;
	footprintName: string;
	footprintUuid: string;
}

/** Development-only truth data used to compare the offline V3 parser. */
export function truthRowsToCsv(rows: Array<TruthBBoxRow>): string {
	const headers = [
		'Footprint',
		'Footprint UUID',
		'X-Length (mm)',
		'Y-Width (mm)',
		'EDA Version',
		'Extension Version',
	];
	const lines = [headers.join(',')];
	for (const row of rows) {
		const dimensions = makeBBoxRow({ footprintName: row.footprintName }, row.bbox);
		lines.push([
			row.footprintName,
			row.footprintUuid,
			dimensions.xLength,
			dimensions.yWidth,
			row.edaVersion,
			row.extensionVersion,
		].map(escapeCsv).join(','));
	}
	return `\uFEFF${lines.join('\r\n')}\r\n`;
}

export function rowsToCsv(rows: Array<BBoxExportRow>): string {
	const lines = [CSV_COLUMNS.map(column => CSV_HEADERS[column]).join(',')];
	for (const row of rows)
		lines.push(CSV_COLUMNS.map(column => escapeCsv(row[column])).join(','));
	return `\uFEFF${lines.join('\r\n')}\r\n`;
}
