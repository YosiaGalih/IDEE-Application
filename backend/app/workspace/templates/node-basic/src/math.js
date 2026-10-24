export function add(a, b) {
  return a + b;
}

export function average(values) {
  if (values.length === 0) {
    throw new RangeError("values must not be empty");
  }
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}
