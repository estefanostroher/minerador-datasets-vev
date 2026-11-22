import { discountedPrice } from './target';

test('aplica desconto', function appliesDiscount() { expect(discountedPrice(200, 25)).toBe(150); });
test('limita desconto negativo', function limitsNegative() { expect(discountedPrice(200, -5)).toBe(200); });
test('limita desconto excessivo', function limitsExcess() { expect(discountedPrice(200, 120)).toBe(0); });
test('arredonda o resultado', function roundsResult() { expect(discountedPrice(99, 10)).toBe(89); });
