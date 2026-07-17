"use client"
// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Global context provider for the application
 * Provides shared state and functionality across components
 */

import { createContext, useContext, PropsWithChildren, useState, useCallback } from "react"

// localStorage key for the user's selected model. We persist the
// stable logical key (e.g. "opus-4.8"), never the physical Bedrock id, so the
// saved choice survives model/version/routing changes on the backend.
const MODEL_KEY_STORAGE = "fast.selectedModelKey"

interface GlobalContextType {
  isLoading: boolean
  setIsLoading: (loading: boolean) => void
  // The user's selected model logical key, persisted to localStorage.
  // null until a model list is loaded or a choice is made; ChatInterface seeds
  // it with the default once aws-exports models are available.
  selectedModelKey: string | null
  setSelectedModelKey: (key: string) => void
}

const GlobalContext = createContext<GlobalContextType | undefined>(undefined)

/**
 * Hook to access the global context
 * @returns The global context value
 * @throws Error if used outside of GlobalContextProvider
 */
export function useGlobal(): GlobalContextType {
  const context = useContext(GlobalContext)
  if (context === undefined) {
    throw new Error("useGlobal must be used within a GlobalContextProvider")
  }
  return context
}

/**
 * Global context provider component
 * Wraps the application to provide global state
 * @param children - Child components to wrap
 */
export function GlobalContextProvider({ children }: PropsWithChildren) {
  const [isLoading, setIsLoading] = useState(false)

  // Lazy initializer reads the persisted choice once. Guarded for SSR, where
  // window/localStorage do not exist.
  const [selectedModelKey, setSelectedModelKeyState] = useState<string | null>(() => {
    if (typeof window === "undefined") return null
    return window.localStorage.getItem(MODEL_KEY_STORAGE)
  })

  // Update state and persist. Wrapped in useCallback so it is stable across
  // renders (it is read in effect dependency lists downstream).
  const setSelectedModelKey = useCallback((key: string) => {
    setSelectedModelKeyState(key)
    if (typeof window !== "undefined") {
      window.localStorage.setItem(MODEL_KEY_STORAGE, key)
    }
  }, [])

  const value: GlobalContextType = {
    isLoading,
    setIsLoading,
    selectedModelKey,
    setSelectedModelKey,
  }

  return <GlobalContext.Provider value={value}>{children}</GlobalContext.Provider>
}
