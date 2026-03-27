---
name: math_operations
description: Perform mathematical calculations and statistical analysis
version: 1.0.0
trigger: "calculate|compute|math|statistics|convert|evaluate"
tools: [shell]
---

# Math Operations Skill

Use this skill to perform mathematical calculations, statistical analysis, and unit conversions with high precision.

## When to Use

Use this skill when:
- You need to perform mathematical calculations
- You need statistical analysis of data
- You need unit conversions
- You need precise numerical results

## Usage

Call the skill-runner with:

```bash
node /app/scripts/skill-runner.mjs --skill math-operations --params '{"operation":"calculate","expression":"2+2"}'
```

## Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| operation | string | Yes | - | Operation: "calculate", "statistics", or "convert" |
| expression | string | For calculate | - | Mathematical expression to evaluate |
| data | array | For statistics | - | Array of numbers for statistical analysis |
| precision | number | No | 4 | Decimal precision for results |
| value | number | For convert | - | Value to convert |
| fromUnit | string | For convert | - | Source unit |
| toUnit | string | For convert | - | Target unit |

## Examples

### Calculate Expression
```bash
node /app/scripts/skill-runner.mjs --skill math-operations --params '{"operation":"calculate","expression":"sqrt(16) + sin(pi/2)"}'
```

### Statistical Analysis
```bash
node /app/scripts/skill-runner.mjs --skill math-operations --params '{"operation":"statistics","data":[1,2,3,4,5,6,7,8,9,10],"precision":2}'
```

### Unit Conversion
```bash
node /app/scripts/skill-runner.mjs --skill math-operations --params '{"operation":"convert","value":100,"fromUnit":"km","toUnit":"mi"}'
```

## Supported Operations

### Calculate
Supports: basic math (+, -, *, /), exponents (^), functions (sin, cos, tan, sqrt, log, etc.)
Constants: pi, e

### Statistics
Returns: count, sum, mean, median, mode, variance, stdDev, min, max, range

### Convert
Supports: length (m, km, mi, ft, in, yd), weight (kg, g, lb, oz), temperature (C, F, K), volume (l, gal, qt)

## Output

Returns JSON with:
- `result`: Calculation result or statistics object
- `operation`: Operation performed
- `steps`: Array of calculation steps (for calculate)
- `unit`: Target unit (for convert)