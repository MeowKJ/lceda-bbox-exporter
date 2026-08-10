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
	primitiveId: string;
	primitiveType: string;
	rotation: number | string;
	unit: 'mm';
	width: number;
}

export interface PrimitiveMetadata {
	componentName?: string;
	designator?: string;
	footprintName?: string;
	primitiveId: string;
	primitiveType: string;
	rotation?: number;
}

export function milToMm(value: number): number {
	return Number((value * MIL_TO_MM).toFixed(4));
}

export function makeBBoxRow(metadata: PrimitiveMetadata, bbox: RawBBox): BBoxExportRow {
	const minX = milToMm(bbox.minX);
	const minY = milToMm(bbox.minY);
	const maxX = milToMm(bbox.maxX);
	const maxY = milToMm(bbox.maxY);
	return {
		primitiveId: metadata.primitiveId,
		primitiveType: metadata.primitiveType,
		designator: metadata.designator ?? '',
		componentName: metadata.componentName ?? '',
		footprintName: metadata.footprintName ?? '',
		rotation: metadata.rotation ?? '',
		minX,
		minY,
		maxX,
		maxY,
		width: Number((maxX - minX).toFixed(4)),
		height: Number((maxY - minY).toFixed(4)),
		unit: 'mm',
	};
}

export const CSV_COLUMNS: Array<keyof BBoxExportRow> = [
	'primitiveId',
	'primitiveType',
	'designator',
	'componentName',
	'footprintName',
	'rotation',
	'minX',
	'minY',
	'maxX',
	'maxY',
	'width',
	'height',
	'unit',
];

function escapeCsv(value: unknown): string {
	const text = String(value ?? '');
	return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function rowsToCsv(rows: Array<BBoxExportRow>): string {
	const lines = [CSV_COLUMNS.join(',')];
	for (const row of rows)
		lines.push(CSV_COLUMNS.map(column => escapeCsv(row[column])).join(','));
	return `\uFEFF${lines.join('\r\n')}\r\n`;
}

export function rowsToJson(rows: Array<BBoxExportRow>): string {
	return `${JSON.stringify({ schemaVersion: 1, coordinateSystem: 'cartesian', unit: 'mm', rows }, null, 2)}\n`;
}
