/// <reference types="vite/client" />
declare module '*.css';
declare module '*?url' { const value: string; export default value; }
