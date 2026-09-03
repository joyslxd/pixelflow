/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_CONTENT_APP_ORIGIN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
