import { defineConfig } from 'vitest/config';
export default defineConfig({test: {include: ['*.case.ts'], includeTaskLocation: true}});
