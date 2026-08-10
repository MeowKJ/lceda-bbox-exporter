export const MIL_TO_MM = 0.0254;

export interface RawBBox {
	maxX: number;
	maxY: number;
	minX: number;
	minY: number;
}

export interface BBoxExportRow extends RawBBox {
	componentName: string;
	designator: string;
	footprintName: string;
	height: number;
	model3DName: string;
	primitiveId: string;
	primitiveType: string;
	rotation: number | string;
	unit: 'mm';
	width: number;
	xLength: number;
	yWidth: number;
	zHeight: number | '';
	zHeightSource: 'model-name' | 'unavailable';
}

export interface PrimitiveMetadata {
	componentName?: string;
	designator?: string;
	footprintName?: string;
	model3DName?: string;
	primitiveId: string;
	primitiveType: string;
	rotation?: number;
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

export function makeBBoxRow(metadata: PrimitiveMetadata, bbox: RawBBox): BBoxExportRow {
	const minX = milToMm(bbox.minX);
	const minY = milToMm(bbox.minY);
	const maxX = milToMm(bbox.maxX);
	const maxY = milToMm(bbox.maxY);
	const width = Number((maxX - minX).toFixed(4));
	const height = Number((maxY - minY).toFixed(4));
	const zHeight = modelNameToZHeightMm(metadata.model3DName);
	return {
		primitiveId: metadata.primitiveId,
		primitiveType: metadata.primitiveType,
		designator: metadata.designator ?? '',
		componentName: metadata.componentName ?? '',
		footprintName: metadata.footprintName ?? '',
		model3DName: metadata.model3DName ?? '',
		rotation: metadata.rotation ?? '',
		minX,
		minY,
		maxX,
		maxY,
		width,
		height,
		xLength: width,
		yWidth: height,
		zHeight,
		zHeightSource: zHeight === '' ? 'unavailable' : 'model-name',
		unit: 'mm',
	};
}

export const CSV_COLUMNS: Array<keyof BBoxExportRow> = [
	'primitiveId',
	'primitiveType',
	'designator',
	'componentName',
	'footprintName',
	'model3DName',
	'rotation',
	'xLength',
	'yWidth',
	'zHeight',
	'zHeightSource',
	'minX',
	'minY',
	'maxX',
	'maxY',
	'width',
	'height',
	'unit',
];

export const CSV_HEADERS: Record<keyof BBoxExportRow, string> = {
	primitiveId: 'Primitive ID',
	primitiveType: 'Primitive Type',
	designator: 'Designator',
	componentName: 'Device',
	footprintName: 'Footprint',
	model3DName: '3D Model',
	rotation: 'Rotation',
	xLength: 'X-Length of Bottom Edge on Board (Spacing Line)',
	yWidth: 'Y-Width',
	zHeight: 'Z-Height',
	zHeightSource: 'Z-Height Source',
	minX: 'BBox Min X',
	minY: 'BBox Min Y',
	maxX: 'BBox Max X',
	maxY: 'BBox Max Y',
	width: 'BBox Width',
	height: 'BBox Height',
	unit: 'Unit',
};

function escapeCsv(value: unknown): string {
	const text = String(value ?? '');
	return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function rowsToCsv(rows: Array<BBoxExportRow>): string {
	const lines = [CSV_COLUMNS.map(column => CSV_HEADERS[column]).join(',')];
	for (const row of rows)
		lines.push(CSV_COLUMNS.map(column => escapeCsv(row[column])).join(','));
	return `\uFEFF${lines.join('\r\n')}\r\n`;
}

export function rowsToJson(rows: Array<BBoxExportRow>): string {
	return `${JSON.stringify({ schemaVersion: 1, coordinateSystem: 'cartesian', unit: 'mm', rows }, null, 2)}\n`;
}
