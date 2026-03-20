/**
 * Math Operations Skill Implementation
 * Performs accurate mathematical calculations and statistical analysis
 */

interface MathParams {
  operation: 'calculate' | 'statistics' | 'convert';
  expression?: string;
  data?: number[];
  precision?: number;
  fromUnit?: string;
  toUnit?: string;
  value?: number;
}

interface MathResult {
  result: number | { [key: string]: number };
  operation: string;
  steps?: string[];
  unit?: string;
}

// Safe math evaluation
function evaluateExpression(expr: string): number {
  // Remove whitespace
  expr = expr.replace(/\s+/g, '');

  // Validate expression (only allow safe characters)
  if (!/^[\d+\-*/%^.()a-z\s]+$/i.test(expr)) {
    throw new Error('Invalid characters in expression');
  }

  // Replace math functions
  const mathFunctions: Record<string, string> = {
    'sin': 'Math.sin',
    'cos': 'Math.cos',
    'tan': 'Math.tan',
    'asin': 'Math.asin',
    'acos': 'Math.acos',
    'atan': 'Math.atan',
    'sqrt': 'Math.sqrt',
    'abs': 'Math.abs',
    'log': 'Math.log',
    'log10': 'Math.log10',
    'exp': 'Math.exp',
    'floor': 'Math.floor',
    'ceil': 'Math.ceil',
    'round': 'Math.round',
    'pi': 'Math.PI',
    'e': 'Math.E'
  };

  // Convert ^ to ** for exponentiation
  expr = expr.replace(/\^/g, '**');

  // Replace function names
  for (const [name, replacement] of Object.entries(mathFunctions)) {
    const regex = new RegExp(`\\b${name}\\b`, 'gi');
    expr = expr.replace(regex, replacement);
  }

  try {
    // Use Function constructor for safer evaluation
    const result = new Function(`return ${expr}`)();
    if (typeof result !== 'number' || !isFinite(result)) {
      throw new Error('Invalid result');
    }
    return result;
  } catch (error) {
    throw new Error(`Failed to evaluate expression: ${expr}`);
  }
}

// Statistical functions
function calculateStatistics(data: number[]): { [key: string]: number } {
  const n = data.length;
  if (n === 0) {
    throw new Error('Cannot calculate statistics on empty data');
  }

  // Sort for median and percentiles
  const sorted = [...data].sort((a, b) => a - b);

  // Mean
  const mean = data.reduce((sum, val) => sum + val, 0) / n;

  // Median
  const median = n % 2 === 0
    ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
    : sorted[Math.floor(n / 2)];

  // Mode
  const frequency: Record<number, number> = {};
  let maxFreq = 0;
  let mode = sorted[0];
  for (const val of data) {
    frequency[val] = (frequency[val] || 0) + 1;
    if (frequency[val] > maxFreq) {
      maxFreq = frequency[val];
      mode = val;
    }
  }

  // Variance and standard deviation
  const variance = data.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / n;
  const stdDev = Math.sqrt(variance);

  // Range
  const range = sorted[n - 1] - sorted[0];

  return {
    count: n,
    sum: data.reduce((sum, val) => sum + val, 0),
    mean,
    median,
    mode,
    variance,
    stdDev,
    min: sorted[0],
    max: sorted[n - 1],
    range
  };
}

// Unit conversions
const conversions: Record<string, Record<string, number>> = {
  length: {
    m: 1,
    km: 1000,
    cm: 0.01,
    mm: 0.001,
    mi: 1609.344,
    ft: 0.3048,
    in: 0.0254,
    yd: 0.9144
  },
  weight: {
    kg: 1,
    g: 0.001,
    mg: 0.000001,
    lb: 0.453592,
    oz: 0.0283495,
    ton: 1000
  },
  temperature: {
    // Special handling for temperature
  },
  volume: {
    l: 1,
    ml: 0.001,
    gal: 3.78541,
    qt: 0.946353,
    pt: 0.473176,
    cup: 0.236588
  }
};

function convertUnit(value: number, from: string, to: string): number {
  const fromLower = from.toLowerCase();
  const toLower = to.toLowerCase();

  // Temperature is special
  if (fromLower === 'c' && toLower === 'f') {
    return (value * 9 / 5) + 32;
  }
  if (fromLower === 'f' && toLower === 'c') {
    return (value - 32) * 5 / 9;
  }
  if (fromLower === 'c' && toLower === 'k') {
    return value + 273.15;
  }
  if (fromLower === 'k' && toLower === 'c') {
    return value - 273.15;
  }
  if (fromLower === 'f' && toLower === 'k') {
    return (value - 32) * 5 / 9 + 273.15;
  }
  if (fromLower === 'k' && toLower === 'f') {
    return (value - 273.15) * 9 / 5 + 32;
  }

  // Find conversion category
  for (const [category, units] of Object.entries(conversions)) {
    if (category === 'temperature') continue;

    const fromUnit = Object.keys(units).find(u => u === fromLower);
    const toUnit = Object.keys(units).find(u => u === toLower);

    if (fromUnit && toUnit) {
      // Convert to base unit then to target
      const baseValue = value * units[fromUnit as keyof typeof units];
      return baseValue / units[toUnit as keyof typeof units];
    }
  }

  throw new Error(`Cannot convert from ${from} to ${to}`);
}

// Main function
export function mathOperations(params: MathParams): MathResult {
  const { operation, expression, data, precision = 4, fromUnit, toUnit, value } = params;

  let result: number | { [key: string]: number };
  const steps: string[] = [];

  switch (operation) {
    case 'calculate':
      if (!expression) {
        throw new Error('Expression required for calculate operation');
      }
      steps.push(`Evaluating: ${expression}`);
      result = evaluateExpression(expression);
      steps.push(`Result: ${result}`);
      break;

    case 'statistics':
      if (!data || data.length === 0) {
        throw new Error('Data array required for statistics operation');
      }
      steps.push(`Calculating statistics for ${data.length} values`);
      result = calculateStatistics(data);
      steps.push(`Mean: ${(result as any).mean}`);
      steps.push(`Std Dev: ${(result as any).stdDev}`);
      break;

    case 'convert':
      if (value === undefined || !fromUnit || !toUnit) {
        throw new Error('Value, fromUnit, and toUnit required for convert operation');
      }
      steps.push(`Converting ${value} ${fromUnit} to ${toUnit}`);
      result = convertUnit(value, fromUnit, toUnit);
      steps.push(`Result: ${result} ${toUnit}`);
      break;

    default:
      throw new Error(`Unknown operation: ${operation}`);
  }

  // Round result if it's a number
  if (typeof result === 'number') {
    result = Math.round(result * Math.pow(10, precision)) / Math.pow(10, precision);
  }

  return {
    result,
    operation,
    steps
  };
}

export default mathOperations;