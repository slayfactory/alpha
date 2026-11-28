import path from 'path';
import react from '@vitejs/plugin-react';
import {defineConfig} from 'vite';

// Один файл JS (IIFE: window.P42Trades) и один CSS — страница подгружает их сама; CDN нет.
export default defineConfig({
  plugins: [react()],
  resolve: {alias: {'@': path.resolve(__dirname, 'src')}},
  define: {'process.env.NODE_ENV': '"production"'},
  build: {
    lib: {entry: 'src/main.tsx', name: 'P42TradesLib', formats: ['iife'], fileName: () => 'trades.js'},
    cssCodeSplit: false,
    emptyOutDir: true,
    outDir: 'dist',
    rollupOptions: {output: {assetFileNames: 'trades[extname]'}},
  },
});
