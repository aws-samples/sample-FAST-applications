"use client"

import { MessageSquare, Plus, Trash2, LogOut } from "lucide-react"
import type { SessionSummary } from "@/services/sessionService"
import { useAuth } from "@/hooks/useAuth"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"

type ChatSidebarProps = {
  sessions: SessionSummary[]
  currentSessionId?: string
  onSessionSelect: (sessionId: string) => void
  onSessionDelete: (sessionId: string) => void
  onNewChat: () => void
}

export function ChatSidebar({
  sessions,
  currentSessionId,
  onSessionSelect,
  onSessionDelete,
  onNewChat,
}: ChatSidebarProps) {
  const { user, signOut, isAuthenticated } = useAuth()
  const email = (user?.profile?.email as string | undefined) || "Signed in"

  return (
    <Sidebar>
      <SidebarHeader className="p-4 space-y-2">
        <Button onClick={onNewChat} className="w-full justify-start gap-2">
          <Plus className="h-4 w-4" />
          New Chat
        </Button>
      </SidebarHeader>
      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Recent Chats</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {sessions.length === 0 && (
                <div className="px-2 py-1 text-xs text-gray-400">No conversations yet</div>
              )}
              {sessions.map(session => (
                <SidebarMenuItem key={session.sessionId}>
                  <SidebarMenuButton
                    onClick={() => onSessionSelect(session.sessionId)}
                    isActive={currentSessionId === session.sessionId}
                    className="w-full justify-start gap-2 pr-8"
                    title={session.title}
                  >
                    <MessageSquare className="h-4 w-4 shrink-0" />
                    <span className="truncate">{session.title}</span>
                  </SidebarMenuButton>
                  <SidebarMenuAction
                    onClick={() => onSessionDelete(session.sessionId)}
                    showOnHover
                    aria-label="Delete conversation"
                    title="Delete conversation"
                    className="text-gray-400 hover:text-red-600"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </SidebarMenuAction>
                </SidebarMenuItem>
              ))}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>
      {isAuthenticated && (
        <SidebarFooter className="p-2">
          <div className="flex items-center gap-2 rounded-md border border-gray-200 bg-white px-2 py-1.5">
            <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-gray-800 text-xs font-medium text-white">
              {email.charAt(0).toUpperCase()}
            </div>
            <span className="min-w-0 flex-1 truncate text-xs text-gray-700" title={email}>
              {email}
            </span>
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0 text-gray-400 hover:text-red-600"
                  aria-label="Logout"
                  title="Logout"
                >
                  <LogOut className="h-4 w-4" />
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>Confirm Logout</AlertDialogTitle>
                  <AlertDialogDescription>
                    Are you sure you want to log out? You will need to sign in again.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancel</AlertDialogCancel>
                  <AlertDialogAction onClick={() => signOut()}>Confirm</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </SidebarFooter>
      )}
    </Sidebar>
  )
}
