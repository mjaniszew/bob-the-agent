# Math Operations Skill

Performs accurate mathematical calculations and statistical analysis.

## Features
- Basic arithmetic with precision handling
- Statistical functions (mean, median, mode, std dev)
- Expression parsing
- Unit conversions
- Financial calculations

## Usage
```yaml
skill: math-operations
parameters:
  operation: "calculate" | "statistics" | "convert"
  expression: "(2 + 3) * 4 / 5"
  precision: 2
```

## Supported Operations
- Arithmetic: +, -, *, /, %, ^, sqrt
- Statistics: mean, median, mode, variance, std_dev
- Trigonometry: sin, cos, tan, asin, acos, atan
- Financial: NPV, IRR, compound interest

## Output
Returns calculation result with:
- Result value
- Steps (if requested)
- Error handling for invalid expressions