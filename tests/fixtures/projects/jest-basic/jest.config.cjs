module.exports = {
  testEnvironment: 'node', testMatch: ['**/*.test.ts'],
  transform: {'^.+\\.tsx?$': ['ts-jest', {diagnostics: false, tsconfig: {target: 'ES2022', module: 'CommonJS'}}]},
};
