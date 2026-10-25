/** Return the magnitude of a finite number; reject non-finite inputs. */
export function magnitude(value: number): number {
  if (!Number.isFinite(value)) throw new Error('finite number required');
  return value < 0 ? -value : value;
}
