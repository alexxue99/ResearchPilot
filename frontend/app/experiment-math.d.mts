export function normalizeExperimentMath(text: string, options?: {inferPlainMath?: boolean}): string;
export function delimitPlainExperimentMath(text: string): string;
export function flattenExperimentSettings(value: unknown, prefix?: string): {label: string; value: string}[];
