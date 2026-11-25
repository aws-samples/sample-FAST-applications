// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import path from "path"

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  build: {
    outDir: "build",
    sourcemap: true,
    rollupOptions: {
      output: {
        // Vite 8 (Rolldown) only accepts the function form of manualChunks.
        manualChunks(id: string) {
          if (!id.includes("node_modules")) return
          if (/node_modules\/(react|react-dom|react-router|react-router-dom)\//.test(id)) {
            return "react-vendor"
          }
          if (/node_modules\/@radix-ui\//.test(id)) return "ui-vendor"
          if (
            /node_modules\/(react-oidc-context|oidc-client-ts|aws-amplify|@aws-amplify)\//.test(id)
          ) {
            return "auth-vendor"
          }
        },
      },
    },
  },

  server: {
    port: 3000,
    open: true,
  },
})
