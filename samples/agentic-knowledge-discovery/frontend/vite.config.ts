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
        // Vite 8 uses the rolldown bundler, which requires manualChunks to be a
        // function — the object form is no longer supported and fails at build.
        manualChunks(id: string) {
          if (id.includes("node_modules")) {
            if (/[\\/]node_modules[\\/](react|react-dom|react-router-dom)[\\/]/.test(id))
              return "react-vendor"
            if (id.includes("@radix-ui") || id.includes("radix-ui")) return "ui-vendor"
            if (id.includes("react-oidc-context") || id.includes("aws-amplify"))
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
