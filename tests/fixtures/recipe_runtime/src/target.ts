/** Calcula um desconto limitado ao intervalo permitido. */
export function discountedPrice(price: number, discount: number): number {
  return Math.round(price * (1 - Math.max(0, Math.min(100, discount)) / 100));
}
